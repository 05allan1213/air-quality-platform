from datetime import datetime, timedelta

from django.db import transaction
from psycopg.types.range import Range

from .models import CityIdentifier, SiteEpoch, Station, StationIdentifier
from .policy import Denied


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


@transaction.atomic
def relocate(station, at, **new_epoch):
    """Close and open together. Changing historical observation identity needs a correction."""
    from .models import StationRevision

    Station.objects.select_for_update().get(pk=station.pk)
    old = SiteEpoch.objects.select_for_update().get(station=station, active_period__contains=at)
    for row in StationRevision.objects.filter(
        key__site_epoch=old, key__observed_at_utc__gte=at - timedelta(hours=24)
    ).select_related("key"):
        end = row.provenance.get("window_end")
        if (end and datetime.fromisoformat(end) > at) or (
            end is None and row.key.observed_at_utc >= at
        ):
            raise Denied("mapping_review_required")
    if old.active_period.lower >= at or new_epoch["city"].source_id != station.source_id:
        raise Denied("invalid_epoch")
    upper = old.active_period.upper
    old.active_period = Range(old.active_period.lower, at, "[)")
    old.save(update_fields=["active_period"])
    return SiteEpoch.objects.create(
        station=station, active_period=Range(at, upper, "[)"), **new_epoch
    )
