"""Object-specific lineage and current source-use checks for S08 audit reads."""

from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

from collection import control
from collection.admission import LEDGER, lock_job
from collection.models import ControlEvent, Job, Run, WorkWindow
from collection.s07_admin import review_window
from collection.s08_schedule import REASONS as SCHEDULE_ACTIONS
from collection.s08_schedule import review as review_schedule
from collection.s08_schedule import review_takeover
from collection.schedule_segments import hour
from collection.services import locked
from observations.catalog import DIRECTORY_RULE, directory_span
from observations.catalog_governance import ACTIONS as STATION_ACTIONS
from observations.catalog_governance import allowed as review_catalog
from observations.models import (
    CatalogChange,
    Product,
    QualityDecision,
    QualityIssue,
    Source,
    Station,
    StationIdentifier,
    UseGrant,
)
from observations.policy import RAW, Denied, Scope, actor, applicable
from observations.quality_governance import ACTIONS as QUALITY_ACTIONS
from observations.quality_governance import checks, product_lock, review_retained
from observations.revisions import key_scope
from psycopg.types.range import Range

from .assigned_permissions import CONFIGURE_SCHEDULE, assigned_permissions
from .models import GovernanceAction

MANAGE = "observations.manage_collection"
REVIEW = "observations.review_observation"
PERMISSIONS = {
    "job": {MANAGE, CONFIGURE_SCHEDULE},
    "run": {REVIEW},
    "quality_issue": {REVIEW},
    "station": {REVIEW, "accounts.manage_catalog"},
    "grant": {"observations.manage_grants"},
}


def authorize(user_id, kind):
    user = actor(user_id)
    permissions = user.get_all_permissions()
    needed = PERMISSIONS[kind]
    if kind == "job":
        # Both routes to schedule audit require assignments on the principal.
        permissions = (permissions - needed) | (assigned_permissions(user) & needed)
    allowed = bool(permissions & needed) if kind == "job" else needed <= permissions
    if not allowed:
        raise Denied("permission_denied")
    return permissions


def grant_rows(product):
    return list(product.usegrant_set.filter(revoked_at__isnull=True))


def action_rows(kind, target, actions):
    return GovernanceAction.objects.filter(
        target_kind=kind, target=str(target), action__in=actions, outcome="applied"
    )


class Target:
    timestamp = "created_at"
    collection = False

    def check(self, now, permissions):
        try:
            self.authorized_target(now, permissions)
        except Denied as exc:
            if exc.code == "database_unavailable":
                raise
            # Do not distinguish an unavailable, disposed or inconsistent target.
            raise Denied() from None

    def authorized_target(self, now, permissions):
        pass

    def permit(self, row, now, permissions):
        try:
            return self.authorized_record(row, now, permissions)
        except Denied as exc:
            if exc.code == "database_unavailable":
                raise
            return None


class CollectionTarget(Target):
    timestamp = "occurred_at"
    collection = True

    def __init__(self, job, run=None):
        self.job = job
        self.run = run
        self.product = job.product
        # The origin Job/Run lock already owns its Source. Fence source-wide rights
        # writers on their Product rows before the principal is locked.
        self.products = list(
            Product.objects.select_for_update()
            .filter(source_id=job.product.source_id)
            .order_by("pk")
        )
        self.grants = {p.pk: grant_rows(p) for p in self.products}

    def rows(self):
        rows = ControlEvent.objects.filter(job=self.job, receipt__outcome="applied")
        if self.run is not None:
            rows = rows.filter(run=self.run)
        return rows.select_related("receipt", "run__window")

    def full_run(self, run, now):
        window = run.window
        if (
            self.job.disposed
            or window.product_id != self.product.pk
            or run.job_id != self.job.pk
            or run.executor_id != self.job.executor_id
            or run.validation_grant_id != self.job.validation_grant_id
            or window.profile != self.job.profile
            or window.selection != self.job.selection
            or window.standard != self.job.standard
            or window.scope_key != (f"city:{self.job.city_id}" if self.job.city_id else "national")
        ):
            raise Denied()
        window.product = self.product
        if not review_window(window, self.job, self.grants[self.product.pk], now, {})[0]:
            raise Denied()

    def authorized_target(self, now, permissions):
        if self.run is not None:
            self.full_run(self.run, now)
        elif MANAGE in permissions:
            control.review_job(self.job, now)
        else:
            # A configure-only caller must prove the target is a registered plan/template.
            if self.job.schedule_template_id:
                self.review_plan(now)
            elif hasattr(self.job, "host_template"):
                review_schedule(self.job, now)
            else:
                raise Denied()

    def review_plan(self, now):
        job = self.job
        if not job.schedule_template_id or not job.schedule_anchor_at:
            raise Denied()
        bases = [job.schedule_template.basis_job]
        if job.predecessor_id:
            bases.append(job.predecessor)
        for basis in bases:
            if basis.product_id != self.product.pk:
                raise Denied()
            basis.product = self.product
            review_schedule(basis, now, job.schedule_anchor_at)
        review_schedule(job, now, job.schedule_anchor_at)

    def schedule_record(self, event, record, now, permissions):
        job = self.job
        if (
            event.run_id is not None
            or not permissions & {CONFIGURE_SCHEDULE, MANAGE}
            or event.schedule_response is None
            or not job.schedule_template_id
            or not job.schedule_anchor_at
            or (record.target_kind, record.target) != ("job", str(job.pk))
        ):
            return None
        if record.action == "create_draft":
            origin = f"template:{job.schedule_template_id}"
        elif record.action == "propose":
            if job.predecessor_id is None:
                return None
            origin = f"job:{job.predecessor_id}"
        else:
            origin = f"job:{job.pk}"
        if record.origin != origin:
            return None
        self.review_plan(now)
        if record.action == "activate":
            review_takeover(job, hour(job.schedule_anchor_at), now)
        return record

    def authorized_record(self, event, now, permissions):
        record = event.receipt
        if record is None or (record.action, record.actor_id, record.created_at) != (
            event.action,
            event.actor_id,
            event.occurred_at,
        ):
            return None
        if self.run is None and record.action in SCHEDULE_ACTIONS:
            return self.schedule_record(event, record, now, permissions)
        if self.run is None and MANAGE not in permissions:
            return None
        if record.origin != str(self.job.pk):
            return None
        if event.run_id and REVIEW not in permissions:
            return None
        if event.run_id and self.run is None:
            self.full_run(event.run, now)
        kind = record.target_kind
        if kind == "job" and not event.run_id and record.target == str(self.job.pk):
            if record.action in {"pause", "resume", "auto_pause"}:
                control.review_job(
                    self.job,
                    now,
                    target=record.created_at.replace(minute=0, second=0, microsecond=0),
                )
                return record
        elif kind == "run" and self.run is not None and record.target == str(self.run.pk):
            if record.action == "redeliver":
                return record
        elif kind in {"source", "product"}:
            target = self.product.source_id if kind == "source" else self.product.pk
            if record.target != str(target) or record.action not in {
                f"resume_{kind}",
                "auto_pause",
            }:
                return None
            # No historical impact list exists. Narrow current grants cannot prove
            # the full effects of a shared-control action, even when its origin is known.
            products = self.products if kind == "source" else (self.product,)
            for product in products:
                scope = Scope("*", "*", "*", date.min, date.max)
                for operation in ("review", "retain_normalized"):
                    applicable(product, operation, scope, LEDGER, now, self.grants[product.pk])
            if event.run_id is None:
                control.review_job(
                    self.job,
                    now,
                    target=record.created_at.replace(minute=0, second=0, microsecond=0),
                )
            return record
        return None


class QualityTarget(Target):
    def __init__(self, event, product):
        self.event = event
        self.product = product
        self.key = event.city_key or event.station_key
        self.grants = grant_rows(product)

    def rows(self):
        return action_rows("quality", self.event.pk, QUALITY_ACTIONS)

    def authorized_target(self, now, permissions):
        checks(self.event, self.product, self.key, self.grants, now)

    def authorized_record(self, record, now, permissions):
        if (
            record.origin != ""
            or record.after_version > self.event.event_version
            or record.after_version != record.before_version + 1
        ):
            return None
        if record.action not in {"correct_source", "correct_mapping"}:
            return record
        decision = (
            QualityDecision.objects.filter(action=record)
            .select_related(
                "city_before__key__city",
                "city_after__key__city",
                "station_before__key__site_epoch__city",
                "station_after__key__site_epoch__city",
            )
            .first()
        )
        if self.key is None or decision is None:
            return None
        before = getattr(decision, f"{self.product.level}_before")
        after = getattr(decision, f"{self.product.level}_after")
        if (
            not before
            or not after
            or before.key_id != self.key.pk
            or after.key.product_id != self.product.pk
        ):
            return None
        if record.action == "correct_mapping":
            if after.key_id == before.key_id:
                return None  # A no-op doesn't prove the mapping target scope.
            checks(self.event, self.product, self.key, self.grants, now, raw=True)
            review_retained(self.product, key_scope(after.key), RAW, now, self.grants)
        elif before.key_id != after.key_id:
            return None
        return record


class StationTarget(Target):
    def __init__(self, station, products):
        self.station = station
        self.products = {p.pk: p for p in products}

    def rows(self):
        linked = CatalogChange.objects.filter(epoch__station=self.station).values("action_id")
        return action_rows("station", self.station.pk, STATION_ACTIONS).filter(
            pk__in=linked, after_state="applied"
        )

    def authorized_target(self, now, permissions):
        epochs = list(self.station.siteepoch_set.select_related("city"))
        codes = StationIdentifier.objects.filter(
            station=self.station, kind="code", source_id=self.station.source_id
        ).exclude(evidence="")
        for code in codes:
            for epoch in epochs:
                if epoch.city.source_id != self.station.source_id or not epoch.location_evidence:
                    continue
                start = max(code.active_period.lower, epoch.active_period.lower)
                ends = [
                    bound
                    for bound in (code.active_period.upper, epoch.active_period.upper)
                    if bound is not None
                ]
                end = min(ends) if ends else None
                if end is not None and start >= end:
                    continue
                period = Range(start, end)
                for product in self.products.values():
                    scope = directory_span(product, code.code, epoch.city.geography, period)
                    candidates = {scope.start} | {
                        day
                        for grant in grant_rows(product)
                        for day in (grant.data_from, grant.data_until)
                        if scope.start < day < scope.end
                    }
                    for day in sorted(candidates):
                        try:
                            review_catalog(
                                product,
                                [replace(scope, start=day, end=day + timedelta(days=1))],
                                now,
                            )
                        except Denied:
                            continue
                        return
        raise Denied()

    def authorized_record(self, record, now, permissions):
        if (
            record.origin != ""
            or record.before_state != "registered"
            or record.after_version != record.before_version + 1
        ):
            return None
        details = list(
            CatalogChange.objects.filter(action=record).select_related(
                "change", "epoch__station", "epoch__city", "identifier", "label"
            )
        )
        expected = 1 if record.action == "rename" else 2
        groups = defaultdict(list)
        for detail in details:
            if (
                detail.change.product_id not in self.products
                or detail.change.kind != "catalog_changed"
                or detail.epoch.station.source_id != self.station.source_id
                or detail.epoch.city.source_id != self.station.source_id
                or detail.identifier.source_id != self.station.source_id
                or detail.identifier.station_id != detail.epoch.station_id
                or detail.identifier.kind != "code"
                or (record.action == "rename") != bool(detail.label_id)
                or (
                    detail.label_id
                    and (
                        detail.label.station_id != self.station.pk
                        or detail.label.source_id != self.station.source_id
                        or detail.label.kind != "alias"
                        or detail.label.evidence != DIRECTORY_RULE
                    )
                )
            ):
                return None
            groups[detail.change.product_id].append(detail)
        if not groups or any(len(group) != expected for group in groups.values()):
            return None
        # The initiating product isn't persisted on the receipt: verify the effect
        # under every possible initiating product, even if one sibling link was disposed.
        original = next(iter(groups.values()))
        pairs = {(d.epoch_id, d.identifier_id, d.label_id, d.active_period) for d in original}
        if any(
            {(d.epoch_id, d.identifier_id, d.label_id, d.active_period) for d in group} != pairs
            for group in groups.values()
        ):
            return None
        stations = {d.epoch.station_id for d in original}
        if (
            len(pairs) != expected
            or len({d.active_period for d in original}) != 1
            or self.station.pk not in stations
            or len(stations) != (2 if record.action == "reuse_code" else 1)
            or len({d.identifier.code for d in original}) != 1
            or (record.action == "relocate" and len({d.identifier_id for d in original}) != 1)
        ):
            return None
        for product in self.products.values():
            if product.pk in groups or (
                product.synthetic and product.family == "F" and product.evidence
            ):
                spans = [
                    directory_span(
                        product, d.identifier.code, d.epoch.city.geography, d.active_period
                    )
                    for d in original
                ]
                review_catalog(product, spans, now)
        return record


class GrantTarget(Target):
    def __init__(self, grant):
        self.grant = grant

    def rows(self):
        return action_rows("use_grant", self.grant.pk, {"withdraw"})

    def authorized_record(self, record, now, permissions):
        # As on the existing grant action endpoint, handoff supplies one known ID;
        # the application's check is the current named grant-management permission.
        grant = self.grant
        if (
            grant.revoked_at is not None
            and grant.revoked_at <= record.created_at
            and record.actor_id == grant.revoked_by_id
            and record.origin == ""
            and (record.before_state, record.after_state) == ("registered", "withdrawn")
            and record.before_version < record.after_version <= grant.product.rights_version
        ):
            return record
        return None


def load_target(kind, target_id):
    try:
        if kind == "job":
            return CollectionTarget(lock_job(target_id))
        if kind == "run":
            run = locked(target_id)
            return CollectionTarget(run.job, run)
        if kind == "quality_issue":
            product_id = (
                QualityIssue.objects.filter(pk=target_id)
                .values_list("product_id", flat=True)
                .first()
            )
            product = product_lock(product_id)
            event = QualityIssue.objects.select_related(
                "city_key__city",
                "station_key__site_epoch__city",
                "run__job",
                "run__window",
                "run_item",
                "batch",
                "receipt",
            ).get(pk=target_id, product=product)
            return QualityTarget(event, product)
        if kind == "station":
            source_id = (
                Station.objects.filter(pk=target_id).values_list("source_id", flat=True).first()
            )
            Source.objects.select_for_update().get(pk=source_id)
            products = list(
                Product.objects.select_for_update()
                .filter(source_id=source_id, level="station")
                .order_by("pk")
            )
            station = Station.objects.select_for_update().get(pk=target_id, source_id=source_id)
            return StationTarget(station, products)
        if kind == "grant":
            product_id = (
                UseGrant.objects.filter(pk=target_id).values_list("product_id", flat=True).first()
            )
            product = product_lock(product_id)
            grant = UseGrant.objects.get(pk=target_id, product=product)
            grant.product = product
            return GrantTarget(grant)
    except (
        Job.DoesNotExist,
        Run.DoesNotExist,
        WorkWindow.DoesNotExist,
        Source.DoesNotExist,
        QualityIssue.DoesNotExist,
        Station.DoesNotExist,
        UseGrant.DoesNotExist,
        Denied,
    ):
        raise Denied() from None
    raise Denied("invalid_request")
