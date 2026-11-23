from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from psycopg.types.range import Range

from .models import CityIdentifier, SiteEpoch, Station, StationIdentifier
from .policy import Denied, Scope

DIRECTORY_RULE = "s06.f.directory.v1"


def directory_span(product, code, geography, period):
    zone = ZoneInfo(product.source_timezone)
    start = period.lower.astimezone(zone).date()
    end = (
        (period.upper - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1)
        if period.upper
        else date.max
    )
    return Scope(geography, code, "*", start, end)


def catalog_change_span(product, detail):
    if (
        detail.epoch.station.source_id != product.source_id
        or detail.epoch.city.source_id != product.source_id
        or detail.identifier.source_id != product.source_id
        or detail.identifier.station_id != detail.epoch.station_id
        or (detail.label_id and detail.label.station_id != detail.epoch.station_id)
    ):
        raise Denied()
    period = detail.label.active_period if detail.label_id else detail.active_period
    return directory_span(product, detail.identifier.code, detail.epoch.city.geography, period)


def reconcile_catalog_changes(product, now, grants):
    from .models import CatalogChange, Change
    from .policy import applicable

    details = CatalogChange.objects.filter(change__product=product).select_related(
        "epoch__station",
        "epoch__city",
        "identifier",
        "label",
    )
    doomed = []
    for detail in details.iterator():
        try:
            applicable(
                product,
                "retain_normalized",
                catalog_change_span(product, detail),
                {"catalog"},
                now,
                grants,
            )
        except Denied:
            doomed.append(detail.change_id)
    if doomed:
        Change.objects.filter(pk__in=doomed).delete()
    Change.objects.filter(
        product=product, kind="catalog_changed", catalogchange__isnull=True
    ).delete()
    StationIdentifier.objects.filter(
        source_id=product.source_id,
        kind="alias",
        evidence=DIRECTORY_RULE,
        label_changes__isnull=True,
    ).delete()


def identity_instant(at, end):
    # A known right-labelled window belongs to the interval immediately before its end.
    return end - timedelta(microseconds=1) if end is not None and at == end else at


def resolve_city(source, code, at, start=None, end=None):
    found = (
        CityIdentifier.objects.select_related("city")
        .filter(
            source=source,
            kind="code",
            code=code,
            active_period__contains=identity_instant(at, end),
            city__source=source,
        )
        .first()
    )
    if not found:
        raise Denied("identity_unresolved")
    if start and not covers(found.active_period, start, end):
        raise Denied("identity_window_crossing")
    return found.city


def resolve(product, code, at, start=None, end=None, *, for_write=False):
    if product.level == "city":
        city = resolve_city(product.source, code, at, start, end)
        return city, city.geography
    at = identity_instant(at, end)
    ident = StationIdentifier.objects.filter(
        source=product.source,
        kind="code",
        code=code,
        active_period__contains=at,
        station__source=product.source,
    ).first()
    if ident:
        if for_write:
            Station.objects.select_for_update().get(pk=ident.station_id)
        epoch = (
            SiteEpoch.objects.select_related("city")
            .filter(
                station_id=ident.station_id,
                city__source=product.source,
                active_period__contains=at,
            )
            .first()
        )
        if epoch:
            if start and not all(
                covers(p, start, end) for p in [ident.active_period, epoch.active_period]
            ):
                raise Denied("identity_window_crossing")
            return epoch, epoch.city.geography
    raise Denied("identity_unresolved")


def covers(period, start, end):
    return start >= period.lower and (period.upper is None or end <= period.upper)


def boundary_conflicts(epoch, at, *, code=None):
    from .models import StationRevision

    # The widest registered reporting window is a local day, which can span 25 hours.
    rows = StationRevision.objects.filter(
        key__site_epoch=epoch, key__observed_at_utc__gte=at - timedelta(days=2)
    ).select_related("key")
    if code is not None:
        rows = rows.filter(key__object_code=code)
    for row in rows.iterator():
        end = row.provenance.get("window_end")
        if (end and datetime.fromisoformat(end) > at) or (
            end is None and row.key.observed_at_utc >= at
        ):
            yield row.key


@transaction.atomic
def relocate(station, at, **new_epoch):
    """Close and open together. Changing historical observation identity needs a correction."""
    Station.objects.select_for_update().get(pk=station.pk)
    old = SiteEpoch.objects.select_for_update().get(station=station, active_period__contains=at)
    if next(boundary_conflicts(old, at), None) is not None:
        raise Denied("mapping_review_required")
    if old.active_period.lower >= at or new_epoch["city"].source_id != station.source_id:
        raise Denied("invalid_epoch")
    upper = old.active_period.upper
    old.active_period = Range(old.active_period.lower, at, "[)")
    old.save(update_fields=["active_period"])
    return SiteEpoch.objects.create(
        station=station, active_period=Range(at, upper, "[)"), **new_epoch
    )
