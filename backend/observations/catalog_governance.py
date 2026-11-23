"""Controlled station directory proposals and committed changes."""

from dataclasses import dataclass

from accounts.governance import command, receipt, replay
from accounts.models import GovernanceAction
from accounts.research_session import SessionCheck, request_transaction
from django.core import signing
from django.db.models import Max
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime
from psycopg.types.range import Range

from .catalog import DIRECTORY_RULE, boundary_conflicts, covers, directory_span, relocate
from .models import (
    CatalogChange,
    Change,
    City,
    CityIdentifier,
    Product,
    SiteEpoch,
    Source,
    Station,
    StationIdentifier,
)
from .policy import REQUIRED, Denied, Scope, actor, applicable
from .quality_events import add_key_issue, advance_key_events
from .quality_governance import user_permission
from .queries import pk
from .revisions import key_scope
from .s04_http import fingerprint

PROFILE = DIRECTORY_RULE
REASONS = {"documented_rename", "documented_relocation", "documented_code_reuse"}
ACTIONS = {"rename", "relocate", "reuse_code"}
FIELDS = {key: {"evidence_ref"} for key in ACTIONS}


def timestamp(value):
    try:
        at = parse_datetime(value)
    except (TypeError, ValueError, OverflowError):
        raise Denied("invalid_request") from None
    if at is None or at.utcoffset() is None or not 1900 <= at.year <= 2200:
        raise Denied("invalid_request")
    return at


def propose(data):
    action = data.get("action")
    if action not in ACTIONS or set(data) != {"product", "action", "identifier", "at", "target"}:
        raise Denied("invalid_request")
    return [
        pk(data["product"]),
        action,
        pk(data["identifier"]),
        timestamp(data["at"]).isoformat(),
        data["target"] if action == "rename" else pk(data["target"]),
    ]


@dataclass(frozen=True)
class BoundDirectory:
    product: Product
    epoch: SiteEpoch
    identifier: StationIdentifier
    period: Range
    target: object
    edition: str

    def spans(self, product):
        geography = self.epoch.city.geography
        target_geography = (
            self.target.geography
            if isinstance(self.target, City)
            else (self.target.city.geography if isinstance(self.target, SiteEpoch) else geography)
        )
        return [
            directory_span(product, self.identifier.code, geo, self.period)
            for geo in sorted({geography, target_geography})
        ]


def registered(product, station, specification):
    _, action, ident_id, stamp, target_id = specification
    at = timestamp(stamp)
    # F proposals are project-authored. No formal-source directory evidence adapter is admitted.
    if (
        product.level != "station"
        or product.family != "F"
        or not product.synthetic
        or not product.evidence
    ):
        raise Denied("directory_evidence_unavailable")
    ident = StationIdentifier.objects.filter(
        pk=ident_id,
        station=station,
        source=product.source,
        kind="code",
        active_period__contains=at,
    ).first()
    old = (
        SiteEpoch.objects.filter(station=station, active_period__contains=at)
        .select_related("city")
        .first()
    )
    if (
        not ident
        or not ident.evidence
        or not old
        or not old.location_evidence
        or old.city.source_id != product.source_id
    ):
        raise Denied("precondition_conflict")
    upper = [x for x in (old.active_period.upper, ident.active_period.upper) if x is not None]
    period = Range(at, min(upper) if upper else None, "[)")
    if action == "rename":
        target = target_id
        if (
            not isinstance(target, str)
            or not 1 <= len(target) <= 100
            or not target.isprintable()
            or not target.strip()
        ):
            raise Denied("invalid_request")
        aliases = StationIdentifier.objects.filter(station=station, kind="alias", evidence=PROFILE)
        if aliases.filter(active_period__startswith=at).exists():
            raise Denied("precondition_conflict")
        successor = (
            aliases.filter(active_period__startswith__gt=at)
            .order_by("active_period__startswith")
            .first()
        )
        if successor and (period.upper is None or successor.active_period.lower < period.upper):
            period = Range(at, successor.active_period.lower, "[)")
    elif action == "relocate":
        # The existing, independently registered city identifies the new reporting location.
        target = City.objects.filter(pk=target_id, source=product.source).first()
        if (
            not target
            or at <= old.active_period.lower
            or period.upper != old.active_period.upper
            or not CityIdentifier.objects.filter(
                city=target,
                source=product.source,
                kind="code",
                active_period__contains=period,
            )
            .exclude(evidence="")
            .exists()
        ):
            raise Denied("precondition_conflict")
    else:
        target = (
            SiteEpoch.objects.filter(
                pk=target_id,
                station__source=product.source,
                city__source=product.source,
                active_period__contains=at,
            )
            .exclude(station=station)
            .select_related("city")
            .first()
        )
        if (
            not target
            or not target.location_evidence
            or at <= ident.active_period.lower
            or (period.upper != ident.active_period.upper)
            or (
                target.active_period.upper is not None
                and (period.upper is None or not covers(target.active_period, at, period.upper))
            )
        ):
            raise Denied("precondition_conflict")
    edition = fingerprint(
        [
            product.pk,
            product.contract_version,
            product.evidence,
            station.pk,
            ident.pk,
            ident.evidence,
            str(ident.active_period),
            old.pk,
            old.location_evidence,
            str(old.active_period),
            [target.pk, str(target.active_period), target.location_evidence]
            if isinstance(target, SiteEpoch)
            else [target.pk, target.geography]
            if isinstance(target, City)
            else target,
        ]
    )
    return BoundDirectory(product, old, ident, period, target, edition)


def allowed(product, scopes, now, *, review=True):
    for scope in scopes:
        if review:
            applicable(product, "review", scope, {"catalog"}, now)
        applicable(product, "retain_normalized", scope, {"catalog"}, now)


def decode(token, user_id, station_id):
    if not isinstance(token, str) or len(token) > 2048:
        raise Denied("invalid_request")
    try:
        payload = signing.loads(token, salt=PROFILE, max_age=1800)
    except signing.BadSignature:
        raise Denied("directory_evidence_unavailable") from None
    if payload[:2] != [user_id, station_id]:
        raise Denied("directory_evidence_unavailable")
    return payload


def impact(record, siblings, bound, before_ident, new_epoch, new_ident, label, now):
    for product in siblings:
        try:
            allowed(product, bound.spans(product), now, review=False)
        except Denied:
            continue
        for epoch, ident in {(bound.epoch, before_ident), (new_epoch, new_ident)}:
            change = Change.objects.create(
                product=product, kind="catalog_changed", rights_version=product.rights_version
            )
            CatalogChange.objects.create(
                change=change,
                action=record,
                epoch=epoch,
                identifier=ident,
                label=label,
                active_period=bound.period,
            )


def edit_directory(bound, action):
    old, ident, period, target = bound.epoch, bound.identifier, bound.period, bound.target
    if action == "rename":
        previous = StationIdentifier.objects.filter(
            station=old.station,
            kind="alias",
            evidence=PROFILE,
            active_period__contains=period.lower,
        ).first()
        if previous:
            previous.active_period = Range(previous.active_period.lower, period.lower, "[)")
            previous.save(update_fields=["active_period"])
        label = StationIdentifier.objects.create(
            station=old.station,
            source=ident.source,
            kind="alias",
            code=target,
            evidence=PROFILE,
            active_period=period,
        )
        return old, ident, label
    if action == "relocate":
        return (
            relocate(
                old.station,
                period.lower,
                city=target,
                name=old.name,
                certainty="confirmed",
                location_evidence=PROFILE,
            ),
            ident,
            None,
        )
    ident.active_period = Range(ident.active_period.lower, period.lower, "[)")
    ident.save(update_fields=["active_period"])
    return (
        target,
        StationIdentifier.objects.create(
            station=target.station,
            source=ident.source,
            kind="code",
            code=ident.code,
            evidence=PROFILE,
            active_period=period,
        ),
        None,
    )


def station_change(request, station_id, *, values=None, data=None):
    actor(request.user.pk, "review_observation")
    if data is None:
        spec = propose(values)
        proof = None
    else:
        data = command(data, ACTIONS, REASONS, extra=FIELDS)
        proof = decode(data["evidence_ref"], request.user.pk, station_id)
        spec = proof[3]
        if data["action"] != spec[1] or data["expected_version"] != proof[2]:
            raise Denied("precondition_conflict")
    with request_transaction():
        source_id = (
            Station.objects.filter(pk=station_id).values_list("source_id", flat=True).first()
        )
        if source_id is None:
            raise Denied("not_found")
        Source.objects.select_for_update().get(pk=source_id)
        siblings = list(
            Product.objects.select_for_update()
            .filter(source_id=source_id, level="station")
            .order_by("pk")
        )
        product = next((p for p in siblings if p.pk == spec[0]), None)
        if product is None:
            raise Denied()
        target_station = (
            SiteEpoch.objects.filter(pk=spec[4], station__source_id=source_id)
            .values_list("station_id", flat=True)
            .first()
            if spec[1] == "reuse_code"
            else None
        )
        stations = {
            s.pk: s
            for s in Station.objects.select_for_update()
            .filter(
                pk__in=[station_id, target_station],
                source_id=source_id,
            )
            .order_by("pk")
        }
        station = stations.get(station_id)
        if station is None:
            raise Denied("not_found")
        auth = SessionCheck(request)
        user_permission(auth.user_id, "accounts.manage_catalog")
        version = (
            GovernanceAction.objects.filter(
                target_kind="station", target=str(station_id)
            ).aggregate(edition=Max("after_version"))["edition"]
            or 0
        )
        if data is not None:
            parameter = salted_hmac(PROFILE, fingerprint(proof), algorithm="sha256").hexdigest()
            previous = replay(auth.user_id, data, "station", station_id, parameter=parameter)
            if previous:
                allowed(product, [Scope.from_issue(s) for s in proof[5]], auth.finish(()))
                return {
                    "action_id": str(previous.pk),
                    "status": previous.after_state,
                    "version": previous.after_version,
                }
            if version != proof[2]:
                raise Denied("precondition_conflict")
        bound = registered(product, station, spec)
        spans = bound.spans(product)
        allowed(product, spans, auth.now)
        affected = {
            "product": product.pk,
            "synthetic": product.synthetic,
            "scope": [s.serialize() for s in spans],
            "start": bound.period.lower,
            "end": bound.period.upper,
            "query_types": ["observation_detail", "analysis", "analysis_details", "export"],
        }
        if data is None:
            allowed(product, spans, auth.finish(()))
            return {
                "version": version,
                "affected": affected,
                "evidence_ref": signing.dumps(
                    [
                        auth.user_id,
                        station_id,
                        version,
                        spec,
                        bound.edition,
                        [s.serialize() for s in spans],
                    ],
                    salt=PROFILE,
                ),
            }
        if proof[4] != bound.edition:
            raise Denied("precondition_conflict")
        before_ident = bound.identifier
        verb = spec[1]
        conflicts = (
            []
            if verb == "rename"
            else list(
                boundary_conflicts(
                    bound.epoch,
                    bound.period.lower,
                    code=before_ident.code if verb == "reuse_code" else None,
                )
            )
        )
        # A blocked directory edit leaves all historical keys on their original epochs.
        status = "mapping_review_required" if conflicts else "applied"
        if not conflicts:
            new_epoch, new_ident, label = edit_directory(bound, verb)
        record = receipt(
            auth.user_id,
            data,
            "station",
            station_id,
            "registered",
            status,
            version,
            version + 1,
            parameter=parameter,
        )
        now = auth.finish(())
        allowed(product, spans, now)
        if conflicts:
            by_id = {p.pk: p for p in siblings}
            for key in {row.pk: row for row in conflicts}.values():
                scope = key_scope(key)
                owner = by_id[key.product_id]
                try:
                    applicable(owner, "retain_normalized", scope, REQUIRED, now)
                except Denied:
                    continue
                issue = add_key_issue(key, "mapping_review_required", rule=key.rule_floor)
                issue.last_action = record
                issue.save(update_fields=["last_action"])
                advance_key_events(key)
        else:
            impact(record, siblings, bound, before_ident, new_epoch, new_ident, label, now)
            affected.update(old_epoch=bound.epoch.pk, new_epoch=new_epoch.pk)
        return {
            "action_id": str(record.pk),
            "status": status,
            "version": version + 1,
            "affected": affected,
        }
