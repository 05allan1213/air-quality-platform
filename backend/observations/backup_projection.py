"""Versioned, scoped observation projection for logical backup and rehearsal."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.backends.postgresql.psycopg_any import DateTimeTZRange

from .backup_authority import wire
from .catalog import covers
from .models import CityIdentifier, MappingLink, StationIdentifier
from .policy import Denied, Scope
from .revisions import key_scope, tables

FORMAT = "s06.logical.v2"
KEY_COLUMNS = (
    "indicator",
    "object_code",
    "report_kind",
    "window_definition",
    "standard_series",
    "observed_at_utc",
    "local_report_date",
    "source_local_date",
    "watermark",
    "trusted_scheme",
    "disputed",
    "retired",
    "qualification_version",
    "rule_floor",
)
REVISION_COLUMNS = (
    "revision_no",
    "is_current",
    "candidate",
    "value",
    "unit",
    "quality",
    "limit_value",
    "limit_operator",
    "rule_version",
    "kind",
    "source_token",
    "created_at",
)
PROVENANCE_FIELDS = (
    "source_timezone",
    "timezone_basis",
    "label_position",
    "window_start",
    "window_end",
    "standard_basis",
    "grade",
    "color",
    "comparable",
    "issue",
    "mapping",
    "source_published_at",
)
SITE_COLUMNS = ("name", "location_evidence", "certainty", "longitude", "latitude", "crs")


def projection(model, columns):
    return {key: getattr(model, key) for key in columns}


def period_wire(period):
    return [period.lower, period.upper]


def period_from_wire(data):
    return DateTimeTZRange(*(datetime.fromisoformat(x) if x else None for x in data), bounds="[)")


def relevant_identifier(model, source, start, end, *, alias="default", **filters):
    candidates = list(
        model.objects.using(alias)
        .filter(
            source=source,
            kind="code",
            active_period__contains=start,
            **filters,
        )
        .order_by("pk")[:2]
    )
    if len(candidates) != 1 or not covers(candidates[0].active_period, start, end):
        raise Denied("backup_dependency_unresolved")
    return candidates[0]


def directory_scope(product, geography, code, period):
    zone = ZoneInfo(product.source_timezone)
    if period.upper is None:
        raise Denied("backup_dependency_unresolved")
    end = (period.upper - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1)
    return Scope(geography, code, "*", period.lower.astimezone(zone).date(), end)


def identifier_wire(ident):
    return projection(ident, ("code", "evidence")) | {"period": period_wire(ident.active_period)}


def catalog_row(product, key, revisions, *, alias="default"):
    zone = ZoneInfo(product.source_timezone)
    start = key.observed_at_utc or datetime.combine(
        key.local_report_date, datetime.min.time(), zone
    )
    end = start + timedelta(microseconds=1)
    for revision in revisions:
        for field in ("window_start", "window_end"):
            if raw := revision.provenance.get(field):
                try:
                    instant = datetime.fromisoformat(raw)
                    start, end = min(start, instant), max(end, instant)
                except (ValueError, TypeError):
                    raise Denied("backup_dependency_unresolved") from None
    if product.level == "station":
        epoch = key.site_epoch
        city = epoch.city
        if epoch.station.source_id != product.source_id or not covers(
            epoch.active_period, start, end
        ):
            raise Denied("backup_dependency_unresolved")
        ident = relevant_identifier(
            StationIdentifier,
            product.source,
            start,
            end,
            station=epoch.station,
            code=key.object_code,
            alias=alias,
        )
        scopes = [
            directory_scope(product, city.geography, ident.code, ident.active_period),
            directory_scope(product, city.geography, ident.code, epoch.active_period),
        ]
        station = {
            "name": epoch.station.name,
            "identifier": identifier_wire(ident),
            "epoch": projection(epoch, SITE_COLUMNS) | {"period": period_wire(epoch.active_period)},
        }
    else:
        city = key.city
        scopes, station = [], None
    if city.source_id != product.source_id:
        raise Denied("backup_dependency_unresolved")
    ident = relevant_identifier(
        CityIdentifier,
        product.source,
        start,
        end,
        city=city,
        alias=alias,
        **({"code": key.object_code} if station is None else {}),
    )
    scopes.append(directory_scope(product, city.geography, ident.code, ident.active_period))
    return {
        "city": projection(city, ("name", "geography")) | {"identifier": identifier_wire(ident)},
        "station": station,
    }, scopes


def rows(product, scope, fields, subject_tokens, catalog_check):
    key_model, rev_model, entity, _, _ = tables(product.level)
    relation = "city" if entity == "city" else "site_epoch__city"
    query = (
        key_model.objects.filter(
            product=product,
            object_code=scope.object_code,
            indicator=scope.indicator,
            source_local_date__gte=scope.start,
            source_local_date__lt=scope.end,
            **{relation + "__geography": scope.geography},
        )
        .select_related(relation)
        .order_by("pk")
    )
    for key in query.iterator(chunk_size=64):
        # Version 1 cannot split a cross-key correction or import its governance evidence.
        links = (
            ("city_old_id", "city_new_id")
            if entity == "city"
            else ("station_old_id", "station_new_id")
        )
        if any(
            MappingLink.objects.filter(product=product, **{link: key.pk}).exists() for link in links
        ):
            raise Denied("backup_dependency_unresolved")
        revisions = list(rev_model.objects.filter(key=key).order_by("revision_no")[:10001])
        if not revisions or len(revisions) > 10000:
            raise Denied("backup_projection_limit")
        directory, dependencies = catalog_row(product, key, revisions)
        for dependency in dependencies:
            catalog_check(dependency)
        projected = []
        for revision in revisions:
            subject = subject_tokens.get(str(revision.actor_id))
            if subject is None:
                raise Denied("backup_history_subject_unmapped")
            item = projection(revision, REVISION_COLUMNS) | {
                "subject": subject,
                "provenance": {
                    k: revision.provenance[k] for k in PROVENANCE_FIELDS if k in revision.provenance
                },
            }
            if "raw_field" in fields:
                item.update(projection(revision, ("raw_value", "raw_unit")))
            projected.append(item)
        if key_scope(key).geography != scope.geography:
            raise Denied("backup_dependency_unresolved")
        yield wire(
            {"directory": directory, "key": projection(key, KEY_COLUMNS), "revisions": projected}
        )
