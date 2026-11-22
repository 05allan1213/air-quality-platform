"""Shared S04 read transaction. PostgreSQL joins current keys to authorized intervals.

An interval is specific to an entity, a time-qualified source code and an indicator.
Its existence is independent of whether a value was ever published in that interval.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models.expressions import RawSQL

from .models import CityIdentifier, Product, SiteEpoch, StationIdentifier, UseGrant
from .normalization import WINDOWS
from .policy import REQUIRED, Denied, Scope, actor, applicable, clock
from .revisions import tables

TICK = timedelta(microseconds=1)


def grant_bins(products):
    bins = {key: [] for key in products}
    for row in UseGrant.objects.filter(product_id__in=products, revoked_at__isnull=True).order_by(
        "pk"
    ):
        bins[row.product_id].append(row)
    return bins


def read_basis(product, scope, fields, now, grants, *, purpose="display", summary=False):
    if any(g.product_id != product.pk for g in grants):
        raise ValueError("grant bucket does not belong to the selected product")
    wanted = set(fields) or REQUIRED
    applicable(product, "retain_normalized", scope, wanted, now, grants)
    basis = applicable(product, purpose, scope, wanted, now, grants)
    if summary:
        eligible = [
            g
            for g in grants
            if g.effect == "deny" or g.restrictions or "overview_summary" in g.fields
        ]
        for operation in ("process_local", "display"):
            applicable(product, operation, scope, REQUIRED | {"overview_summary"}, now, eligible)
    return basis


@dataclass(frozen=True, slots=True)
class Segment:
    entity_id: int
    code: str
    geography: str
    indicator: str
    start: datetime
    end: datetime
    day_start: date
    day_end: date


class Visibility:
    def __init__(self, product, segments):
        self.product = product
        self.segments = segments

    @property
    def permitted(self):
        return bool(self.segments)

    def revisions(self, quality="valid"):
        obs, rev, entity, _, _ = tables(self.product.level)
        query = rev.objects.select_related("key").filter(
            is_current=True, candidate=False, key__product=self.product, key__retired=False
        )
        if not self.segments:
            return query.none()
        # A semijoin, not a row per grant. Identifiers below come only from the two model schemas.
        allowed = json.dumps(
            [
                [
                    s.entity_id,
                    s.code,
                    s.indicator,
                    s.start.isoformat(),
                    s.end.isoformat(),
                    s.day_start.isoformat(),
                    s.day_end.isoformat(),
                ]
                for s in self.segments
            ]
        )
        keys = RawSQL(
            f"""SELECT k.id FROM {obs._meta.db_table} k
            JOIN jsonb_array_elements(%s::jsonb) AS s(v)
              ON k.{entity}_id = (s.v->>0)::bigint AND k.object_code = s.v->>1
             AND k.indicator = s.v->>2 AND k.observed_at_utc >= (s.v->>3)::timestamptz
             AND k.observed_at_utc < (s.v->>4)::timestamptz
             AND k.source_local_date >= (s.v->>5)::date AND k.source_local_date < (s.v->>6)::date
            WHERE k.product_id = %s""",
            (allowed, self.product.pk),
        )
        query = query.filter(key_id__in=keys)
        if quality == "all":
            return query
        if quality == "disputed":
            return query.filter(key__disputed=True)
        query = query.filter(key__disputed=False, quality=quality)
        return query.filter(value__isnull=False) if quality == "valid" else query


def midnight(day, zone):
    if day == date.min:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.combine(day, time.min, zone).astimezone(UTC)


class ReadContext:
    def __init__(self, user_id, product_ids, synthetic):
        if not connection.in_atomic_block:
            raise RuntimeError("an observation read requires a transaction")
        ids = set(product_ids)
        self.products = {
            p.pk: p
            for p in Product.objects.select_for_update(of=("self",))
            .select_related("source")
            .filter(pk__in=ids, synthetic=synthetic)
            .order_by("pk")
        }
        if ids != self.products.keys():
            raise Denied()
        self.user_id = user_id
        actor(user_id)
        self.now = clock()
        self.grants = grant_bins(self.products)
        self.boundaries = {
            g_time
            for rows in self.grants.values()
            for g in rows
            for g_time in (g.valid_from, g.valid_until, g.retain_until)
            if g_time and g_time > self.now
        }
        self._checks = {}
        self._identities = {}
        self._geographies = {}

    def basis(self, product, scope, fields=(), *, summary=False):
        key = (product.pk, scope, frozenset(fields), summary)
        if key not in self._checks:
            self._checks[key] = self._decide(key, self.now)
        return self._checks[key]

    def _decide(self, key, now):
        product_id, scope, fields, summary = key
        try:
            return read_basis(
                self.products[product_id],
                scope,
                fields,
                now,
                self.grants[product_id],
                summary=summary,
            )
        except Denied:
            return None

    def finish(self):
        actor(self.user_id)
        end = clock()
        if any(boundary <= end for boundary in self.boundaries):
            if any(self._decide(key, end) != old for key, old in self._checks.items()):
                raise Denied()

    def identities(self, product, ids):
        cache_key = (
            product.source_id,
            product.level,
            tuple(sorted(set(ids))) if ids is not None else None,
        )
        if cache_key in self._identities:
            return self._identities[cache_key]
        if product.level == "city":
            codes = CityIdentifier.objects.filter(
                source_id=product.source_id, city__source_id=product.source_id, kind="code"
            )
            if ids is not None:
                codes = codes.filter(city_id__in=ids)
            rows = [
                (entity, code, geo, period.lower, period.upper)
                for entity, code, geo, period in codes.values_list(
                    "city_id", "code", "city__geography", "active_period"
                )
            ]
        else:
            epochs = SiteEpoch.objects.filter(
                city__source_id=product.source_id, station__source_id=product.source_id
            )
            if ids is not None:
                epochs = epochs.filter(pk__in=ids)
            by_station = defaultdict(list)
            for epoch, station, geo, period in epochs.values_list(
                "pk", "station_id", "city__geography", "active_period"
            ):
                by_station[station].append((epoch, geo, period))
            codes = StationIdentifier.objects.filter(
                source_id=product.source_id, station_id__in=by_station, kind="code"
            )
            rows = []
            for station, code, period in codes.values_list("station_id", "code", "active_period"):
                for entity, geo, epoch in by_station[station]:
                    lower = max(period.lower, epoch.lower)
                    upper = min((v for v in (period.upper, epoch.upper) if v), default=None)
                    if upper is None or lower < upper:
                        rows.append((entity, code, geo, lower, upper))
        self._identities[cache_key] = rows
        for entity, _, geo, _, _ in rows:
            self._geographies[(product.source_id, product.level, entity)] = geo
        return rows

    def scope(
        self,
        product,
        indicators,
        ids=None,
        *,
        start=None,
        end=None,
        window="hourly_label_unspecified",
        fields=(),
        summary=False,
    ):
        rows = self.grants[product.pk]
        cuts = sorted({day for g in rows for day in (g.data_from, g.data_until)})
        zone = ZoneInfo(product.source_timezone)
        days = [
            (a, b, midnight(a, zone), midnight(b, zone))
            for a, b in zip(cuts, cuts[1:], strict=False)
        ]
        segments = []
        hours, position = WINDOWS[window]
        width = timedelta(hours=hours)
        for entity, code, geo, low, high in self.identities(product, ids):
            # Full known windows must fit the same identifier and site epoch, including a right endpoint.
            low += width if position == "right" else timedelta(0)
            if high and position in ("left", "right"):
                high += TICK - (width if position == "left" else timedelta(0))
            for a, b, day_lo, day_hi in days:
                lo = max(low, day_lo, start or day_lo)
                hi = min(high or day_hi, day_hi, end or day_hi)
                if lo >= hi:
                    continue
                for ind in indicators:
                    if self.basis(product, Scope(geo, code, ind, a, b), fields, summary=summary):
                        segments.append(Segment(entity, code, geo, ind, lo, hi, a, b))
        return Visibility(product, segments)

    @staticmethod
    def latest(revisions, product):
        _, model, entity, _, _ = tables(product.level)
        series = [
            f"key__{entity}_id",
            "key__indicator",
            "key__report_kind",
            "key__window_definition",
            "unit",
            "key__standard_series",
        ]
        winners = (
            revisions.order_by(*series, "-key__observed_at_utc", "-key_id")
            .distinct(*series)
            .values("pk")
        )
        return model.objects.select_related("key").filter(pk__in=winners)

    def project(self, product, revisions):
        from .projection import public_rows

        return public_rows(self, product, list(revisions))
