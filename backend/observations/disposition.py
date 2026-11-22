"""Durable due work. The filesystem is reconciled before a job can become complete."""

import json
import os
import stat
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django_celery_beat.models import ClockedSchedule, IntervalSchedule, PeriodicTask

from .models import (
    Batch,
    Change,
    City,
    CityIdentifier,
    CityObservation,
    DispositionJob,
    ImportAttempt,
    QualityIssue,
    Receipt,
    SiteEpoch,
    StagedInput,
    Station,
    StationIdentifier,
    StationObservation,
)
from .policy import (
    RAW,
    REQUIRED,
    Denied,
    Scope,
    applicable,
    batch_issue_fields,
    clock,
    locked_catalog_product,
)
from .revisions import key_scope, tables

PACKAGE_SCOPE = Scope("beijing", "*", "*", date(2013, 3, 1), date(2017, 3, 1))


def schedule(product, due_at, reason):
    job = DispositionJob.objects.create(product=product, due_at=due_at, reason=reason)
    clocked, _ = ClockedSchedule.objects.get_or_create(clocked_time=due_at)
    PeriodicTask.objects.create(
        name=f"s02.due.{job.pk}",
        task="observations.dispose",
        queue="s02.history",
        clocked=clocked,
        one_off=True,
        args=json.dumps([product.pk]),
    )
    interval, _ = IntervalSchedule.objects.get_or_create(every=30, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.update_or_create(
        name="s02.reconcile",
        defaults=dict(
            interval=interval, task="observations.dispose", queue="s02.history", enabled=True
        ),
    )
    return job


def private_dir():
    path = Path(settings.S02_STAGE_DIR)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise OSError("stage_directory_unavailable")
    # In the deployed Worker this must be its own bounded tmpfs, never the image layer.
    if str(path) == "/run/s02-history" and not os.path.ismount(path):
        raise OSError("stage_mount_unavailable")
    return path


def clear_stage(stage):
    path = private_dir() / str(stage.pk)
    path.unlink(missing_ok=True)
    try:
        path.lstat()
    except FileNotFoundError:
        stage.state = "cleared"
        stage.save(update_fields=["state"])
        return
    raise OSError("stage_still_present")


def covers(product, op, scope, fields, now, grants):
    try:
        applicable(product, op, scope, fields, now, grants)
        return True
    except Denied:
        return False


def lost_at(code_periods, code, observed_at):
    return observed_at is not None and any(
        observed_at in period for period in code_periods.get(code, ())
    )


def reconcile_data(product, grants, now, *, lost_cities=(), lost_epochs=(), lost_identifiers=None):
    lost_identifiers = lost_identifiers or {}
    key_model = CityObservation if product.level == "city" else StationObservation
    keys = key_model.objects.filter(product=product).select_related(
        "city" if product.level == "city" else "site_epoch__city"
    )
    drop = []
    strip = []
    for key in keys.iterator():
        scope = key_scope(key)
        missing_identity = (
            key.city_id in lost_cities
            if product.level == "city"
            else key.site_epoch_id in lost_epochs
        )
        if (
            missing_identity
            or lost_at(lost_identifiers, key.object_code, key.observed_at_utc)
            or not covers(product, "retain_normalized", scope, REQUIRED, now, grants)
        ):
            drop.append(key.pk)
        elif not covers(product, "retain_normalized", scope, RAW, now, grants):
            strip.append(key.pk)
    _, rev, _, _, rf = tables(product.level)
    receipts = Receipt.objects.filter(batch__product=product, **{rf + "__key_id__in": drop + strip})
    # A batch digest describes the WHOLE material. It must not survive a partial purge.
    affected = Batch.objects.filter(pk__in=receipts.values("batch_id"))
    affected.update(identity=None, redacted=True)
    if product.family == "H" and not covers(
        product, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"}, now, grants
    ):
        Batch.objects.filter(product=product).update(identity=None, redacted=True)
    receipts.update(locator=None)
    rev.objects.filter(key_id__in=strip).update(raw_value=None, raw_unit=None)
    key_model.objects.filter(pk__in=drop).delete()
    # A batch can have authorized structural issues and no observation rows.
    for issue in QualityIssue.objects.filter(product=product, city_key=None, station_key=None):
        if not covers(
            product,
            "retain_normalized",
            Scope.from_issue(issue.scope),
            batch_issue_fields(product),
            now,
            grants,
        ):
            Batch.objects.filter(pk=issue.batch_id).update(identity=None, redacted=True)
            issue.delete()
    from collection.retention import reconcile

    reconcile(
        product,
        grants,
        now,
        lost_cities=lost_cities,
        lost_epochs=lost_epochs,
        lost_identifiers=lost_identifiers,
    )
    Batch.objects.filter(product=product, receipts__isnull=True, qualityissue__isnull=True).delete()


def catalog_scope(code, geography, period):
    zone = ZoneInfo("Asia/Shanghai")
    start = period.lower.astimezone(zone).date()
    end = (
        (period.upper - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1)
        if period.upper
        else date.max
    )
    return Scope(geography, code, "*", start, end)


def contains_period(outer, inner):
    # Directory periods are [lower, upper); either upper bound may be unbounded.
    return outer.lower <= inner.lower and (
        outer.upper is None or (inner.upper is not None and inner.upper <= outer.upper)
    )


def reconcile_catalog(product, siblings, now):
    """Check each full directory period against the remaining independent bases."""
    from collection.models import Job, WorkWindow

    source_id = product.source_id
    grants = {p.pk: list(p.usegrant_set.filter(revoked_at=None)) for p in siblings}

    def retain(code, geography, period, *, station=False):
        scope = catalog_scope(code, geography, period)
        return any(
            covers(p, "retain_normalized", scope, {"catalog"}, now, grants[p.pk])
            for p in siblings
            if not station or p.level == "station"
        )

    # Establish Source -> Product -> Job -> Window -> Station order before inspecting epochs.
    list(Job.objects.select_for_update().filter(product__source_id=source_id).order_by("pk"))
    list(WorkWindow.objects.select_for_update().filter(product__source_id=source_id).order_by("pk"))
    stations = list(Station.objects.select_for_update().filter(source_id=source_id).order_by("pk"))
    cities = list(City.objects.filter(source_id=source_id).values_list("pk", flat=True))
    city_ids = list(CityIdentifier.objects.filter(source_id=source_id).select_related("city"))
    station_ids = list(StationIdentifier.objects.filter(source_id=source_id))
    epochs = list(SiteEpoch.objects.filter(station__source_id=source_id).select_related("city"))

    keep_cities = set()
    lost_city_ids = set()
    for ident in city_ids:
        if retain(ident.code, ident.city.geography, ident.active_period):
            if ident.kind == "code":
                keep_cities.add(ident.city_id)
        else:
            lost_city_ids.add(ident.pk)
    lost_cities = set(cities) - keep_cities
    lost_city_ids.update(ident.pk for ident in city_ids if ident.city_id in lost_cities)

    by_station = {}
    for ident in station_ids:
        by_station.setdefault(ident.station_id, []).append(ident)
    keep_station_ids = set()
    lost_epochs = set()
    keep_stations = set()
    for epoch in epochs:
        allowed = []
        if epoch.city_id not in lost_cities:
            allowed = [
                ident
                for ident in by_station.get(epoch.station_id, ())
                if contains_period(ident.active_period, epoch.active_period)
                and retain(ident.code, epoch.city.geography, ident.active_period, station=True)
            ]
        if any(i.kind == "code" for i in allowed):
            keep_station_ids.update(i.pk for i in allowed)
            keep_stations.add(epoch.station_id)
        else:
            lost_epochs.add(epoch.pk)
    lost_station_ids = {i.pk for i in station_ids} - keep_station_ids

    def removed_periods(identifiers, lost):
        periods = {}
        for ident in identifiers:
            if ident.pk in lost and ident.kind == "code":
                periods.setdefault(ident.code, []).append(ident.active_period)
        return periods

    removed_city_periods = removed_periods(city_ids, lost_city_ids)
    removed_station_periods = removed_periods(station_ids, lost_station_ids)

    for sibling in siblings:
        lost_identifiers = (
            removed_city_periods if sibling.level == "city" else removed_station_periods
        )
        if sibling.pk == product.pk or lost_epochs or lost_cities or lost_identifiers:
            reconcile_data(
                sibling,
                grants[sibling.pk],
                now,
                lost_cities=lost_cities,
                lost_epochs=lost_epochs,
                lost_identifiers=lost_identifiers,
            )
            sibling.rights_version += 1
            sibling.save(update_fields=["rights_version"])
            Change.objects.create(
                product=sibling, kind="disposition", rights_version=sibling.rights_version
            )

    StationIdentifier.objects.filter(pk__in=lost_station_ids).delete()
    SiteEpoch.objects.filter(pk__in=lost_epochs).delete()
    Station.objects.filter(pk__in={s.pk for s in stations} - keep_stations).delete()
    CityIdentifier.objects.filter(pk__in=lost_city_ids).delete()
    # Frozen city-scoped Job foreign keys require an anonymous internal anchor.
    City.objects.filter(pk__in=lost_cities, job__isnull=False).update(name="", geography="")
    City.objects.filter(pk__in=lost_cities, job__isnull=True).delete()


@transaction.atomic
def reconcile_product(product_id):
    product, siblings = locked_catalog_product(product_id)
    now = clock()
    jobs = DispositionJob.objects.filter(product=product, completed_at=None, due_at__lte=now)
    if not jobs.exists():
        return
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    if jobs.exclude(reason__in=["stage_cleanup", "stage_expiry"]).exists():
        if siblings:
            reconcile_catalog(product, siblings, now)
        else:
            reconcile_data(product, grants, now)
            product.rights_version += 1
            product.save(update_fields=["rights_version"])
            Change.objects.create(
                product=product, kind="disposition", rights_version=product.rights_version
            )
    for stage in StagedInput.objects.filter(product=product).exclude(state="cleared"):
        if (
            stage.expires_at <= now
            or stage.state == "cleanup_pending"
            or not covers(product, "retain_raw", PACKAGE_SCOPE, {"archive"}, now, grants)
        ):
            clear_stage(stage)
            ImportAttempt.objects.filter(stage=stage, state="pending").update(
                state="rejected", error_code="invalid_stage", finished_at=now
            )
    jobs.update(completed_at=clock(), error_code="")


def dispose_due(product_id=None):
    due = DispositionJob.objects.filter(completed_at=None, due_at__lte=clock())
    if product_id is not None:
        due = due.filter(product_id=product_id)
    failed = False
    for pk in list(due.order_by("product_id").values_list("product_id", flat=True).distinct()):
        try:
            reconcile_product(pk)
        except OSError:
            due.filter(product_id=pk).update(error_code="cleanup_failed")
            failed = True
    if failed:
        raise Denied("cleanup_failed")
