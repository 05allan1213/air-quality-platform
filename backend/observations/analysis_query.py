"""Authorized PostgreSQL relation shared by statistics, drill-down and export."""

import hashlib
import hmac
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection

from .detail import unit_for
from .normalization import INDICATORS, LEVELS, REPORT_KIND, STANDARD_2012, STANDARD_2026, WINDOWS
from .policy import REQUIRED, Denied
from .research_access import DERIVED, LOCAL, Access, ResearchContext
from .research_coverage import coverage
from .research_spec import METHOD_VERSION, QuerySpec, parse_spec  # noqa: F401 -- public service interface
from .revisions import tables
from .selection import TICK


def encoded(data):
    def canonical(value):
        if isinstance(value, datetime):
            return value.isoformat()
        return DjangoJSONEncoder().default(value)

    return json.dumps(
        data, default=canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def token(domain, payload):
    return hmac.new(
        settings.SECRET_KEY.encode(), encoded([domain, payload]).encode(), hashlib.sha256
    ).hexdigest()


def compatible(product, spec):
    if spec.method_version != METHOD_VERSION:
        raise Denied("method_changed")
    invalid = (
        spec.method != "detail"
        and product.shape not in {"C", "S", "H"}
        or set(spec.indicators) - set(INDICATORS[product.shape])
        or REPORT_KIND.get(spec.window, "hourly") != spec.report
        or (product.shape == "D") != (spec.window == "local_daily")
        or product.shape == "H"
        and spec.window != "hourly_label_unspecified"
        or spec.report.startswith("rolling")
        and (spec.indicators != ("O3",) or product.shape not in {"C", "S"})
    )
    if product.shape == "D":
        zone = ZoneInfo(product.source_timezone)
        day = spec.start.astimezone(zone).date()
        invalid = invalid or (
            spec.start != datetime.combine(day, time.min, zone)
            or spec.end != datetime.combine(day + timedelta(days=1), time.min, zone)
        )
    if invalid:
        raise Denied("incompatible_series")
    for ind in spec.indicators:
        indexed = ind == "AQI" or ind in LEVELS
        allowed = {STANDARD_2012, STANDARD_2026, "unknown"} if indexed else {"not_applicable"}
        if (
            spec.units[ind] != unit_for(ind)
            or spec.standards[ind] not in allowed
            or spec.method == "pearson"
            and indexed
            or spec.method == "compare"
            and spec.standards[ind] == "unknown"
        ):
            raise Denied("incompatible_series")


def real_intervals(identities, spec):
    width, position = WINDOWS[spec.window]
    duration = timedelta(hours=width)
    result = []
    for entity, code, _, low, high in identities:
        if position == "date" and (low > spec.start or (high and high < spec.end)):
            continue
        if position == "right":
            low += duration
            if high:
                high += TICK
        elif high and position == "left":
            high += TICK - duration
        start, end = max(spec.start, low), min(spec.end, high or spec.end)
        if start < end:
            result.append(dict(entity=entity, code=code, low=start, high=end))
    return result


def relation_sql(product, spec, identities, profiles, first_day, end_day):
    obs, rev, entity, _, _ = tables(product.level)
    columns, windows, unknown = [], [], []
    for entity_id in spec.ids:
        for ind in spec.indicators:
            pos = len(columns)
            profile = profiles[pos]
            columns.append(
                dict(
                    pos=pos,
                    entity=entity_id,
                    indicator=ind,
                    unit=spec.units[ind],
                    standard=spec.standards[ind],
                    known=profile["known"],
                )
            )
            windows.extend(dict(pos=pos, lo=a, hi=b) for a, b in profile["windows"])
            unknown.extend(
                dict(pos=pos, code=code, lo=a, hi=b) for code, a, b in profile["unknown_by_code"]
            )
    # Identifiers originate only from the two fixed model schemas. All conditions are parameters.
    anchor = (
        "(k.local_report_date::timestamp AT TIME ZONE %s)"
        if spec.report == "daily"
        else "k.observed_at_utc"
    )
    anchor_args = [product.source_timezone] if spec.report == "daily" else []
    expected_anchor = (
        "date_bin(interval '1 hour', w.lo, date_trunc('day', w.lo AT TIME ZONE %s) AT TIME ZONE %s)"
    )
    sql = f"""WITH cols AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(%s::jsonb) AS c(pos int, entity bigint, indicator text, unit text, standard text, known bool)
    ), ranges AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(%s::jsonb) AS s(entity bigint, code text, low timestamptz, high timestamptz)
    ), unknown_calendar AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(%s::jsonb) AS w(pos int, code text, lo timestamptz, hi timestamptz)
    ), expected AS (
        SELECT DISTINCT w.pos, t.at FROM jsonb_to_recordset(%s::jsonb) AS w(pos int, lo timestamptz, hi timestamptz)
        CROSS JOIN LATERAL generate_series({expected_anchor}, w.hi, interval '1 hour') t(at)
        WHERE t.at >= w.lo AND t.at < w.hi
    ), actual AS (
        SELECT c.pos, {anchor} AS at, k.id AS key_id, r.id AS revision, k.qualification_version,
            k.watermark,k.trusted_scheme,r.rule_version,r.kind,k.object_code,
            CASE WHEN k.disputed THEN 'disputed' ELSE r.quality END AS quality,
            CASE WHEN NOT k.disputed THEN r.value END AS value,
            CASE WHEN NOT k.disputed THEN r.limit_value END AS censor_limit,
            CASE WHEN NOT k.disputed THEN r.limit_operator END AS censor_direction,
            NOT k.disputed AND r.quality='valid' AND r.value IS NOT NULL
            AND (c.indicator <> 'AQI' OR (c.standard <> 'unknown' AND r.provenance->>'comparable'='true')) AS effective
        FROM cols c JOIN {obs._meta.db_table} k ON k.{entity}_id=c.entity AND k.indicator=c.indicator AND k.standard_series=c.standard
        JOIN {rev._meta.db_table} r ON r.key_id=k.id AND r.is_current AND NOT r.candidate AND r.unit=c.unit
        WHERE k.product_id=%s AND NOT k.retired AND k.window_definition=%s AND k.report_kind=%s
        AND k.source_local_date >= %s AND k.source_local_date < %s
        AND EXISTS (SELECT FROM ranges s WHERE s.entity=c.entity AND s.code=k.object_code AND {anchor}>=s.low AND {anchor}<s.high)
    ), cells AS (
        SELECT c.pos,c.entity,c.indicator,c.unit,c.standard,COALESCE(e.at,a.at) AS at,
            CASE WHEN e.pos IS NOT NULL THEN true
                 WHEN c.known OR NOT EXISTS (SELECT FROM unknown_calendar u WHERE u.pos=c.pos AND (u.code IS NULL OR u.code=a.object_code) AND a.at>=u.lo AND a.at<u.hi)
                 THEN false END AS expected,
            a.key_id,a.revision,a.qualification_version,a.watermark,a.trusted_scheme,a.rule_version,a.kind,a.object_code,
            COALESCE(a.quality,'gap') AS quality,a.value,a.censor_limit,a.censor_direction,COALESCE(a.effective,false) AS effective
        FROM expected e FULL JOIN actual a USING(pos,at) JOIN cols c ON c.pos=COALESCE(e.pos,a.pos)
    ), slots AS (
        SELECT *, count(*) OVER by_time = %s AND bool_and(expected IS TRUE) OVER by_time AS expected_common,
            count(*) OVER by_time = %s AND bool_and(effective AND expected IS DISTINCT FROM false) OVER by_time AS paired
        FROM cells WINDOW by_time AS (PARTITION BY at)
    )"""
    return sql, [
        encoded(columns),
        encoded(real_intervals(identities, spec)),
        encoded(unknown),
        encoded(windows),
        product.source_timezone,
        product.source_timezone,
        *anchor_args,
        product.pk,
        spec.window,
        spec.report,
        first_day,
        end_day,
        *anchor_args,
        *anchor_args,
        len(columns),
        len(columns),
    ]


class Relation:
    def __init__(self, ctx, spec, access):
        self.ctx, self.spec, self.access = ctx, spec, access
        self.product = p = access.product
        compatible(p, spec)
        self.profiles, self.metadata = coverage(p, spec, access.identities, ctx.epochs)
        self.cte, self.params = relation_sql(
            p, spec, access.identities, self.profiles, access.first, access.last
        )
        self.basis = access.basis
        self.series = False
        self._versions()

    def query(self, sql, params=()):
        with connection.cursor() as cur:
            cur.execute(self.cte + " " + sql, [*self.params, *params])
            names = [col.name for col in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def _versions(self):
        # PG hashes all authorized slots, including expected gaps, in a fixed order. Only two digests cross the DB boundary.
        digests = self.query("""SELECT
          encode(sha256(convert_to(COALESCE(string_agg(jsonb_build_array(pos,at,key_id,revision,qualification_version,watermark,trusted_scheme,rule_version)::text, E'\\n' ORDER BY at,pos),''),'UTF8')),'hex') AS inputs,
          encode(sha256(convert_to(COALESCE(string_agg(jsonb_build_array(pos,at,expected,quality,value,unit,censor_direction,censor_limit,effective,paired)::text,E'\\n' ORDER BY at,pos),''),'UTF8')),'hex') AS content
          FROM slots""")[0]
        # Clip the identity part to this query. A hidden future edition cannot invalidate a historical window.
        spans = sorted(
            real_intervals(self.access.identities, self.spec),
            key=lambda s: (s["entity"], s["low"], s["code"]),
        )
        identity = [
            (e.pk, e.station_id, e.city_id, e.certainty, e.location_evidence)
            for e in sorted(self.ctx.epochs.values(), key=lambda e: e.pk)
        ]
        shared = [
            self.spec.canonical,
            self.product.contract_version,
            self.product.source_timezone,
            self.metadata,
            self.profiles,
            spans,
            identity,
        ]
        self.content_version = token("s05-statistical-content", [shared, digests["content"]])
        self.input_version = token("s05-input", [self.content_version, digests["inputs"]])

    @property
    def attributions(self):
        return sorted(
            {item for row in self.basis for item in row["attributions"]}
            | ({self.product.attribution} if self.product.attribution else set())
        )

    @property
    def until(self):
        return min(
            (datetime.fromisoformat(row["until"]) for row in self.basis if row["until"]),
            default=None,
        )

    def allow_baseline(self):
        return self.access.optionally([("retain_normalized", "platform", {"statistics_baseline"})])

    def after(self, now=None):
        self.access.after(now)


def build_relation(
    user_id, spec, *, purpose="analysis", fields=(), now=None, ctx=None, access=None, nowait=False
):
    if purpose not in {"details", "analysis", "export", "export_aggregate"}:
        raise Denied()
    ctx = ctx or ResearchContext(user_id, spec, now, nowait=nowait)
    access = access or Access(ctx, spec, purpose, fields)
    rel = Relation(ctx, spec, access)
    if purpose == "analysis":
        rel.series = access.optionally([("display", LOCAL, REQUIRED | DERIVED)])
    rel.source_description = (
        purpose == "details"
        and "AQI" in spec.indicators
        and access.optionally(
            [
                ("retain_normalized", "platform", REQUIRED | {"source_description"}),
                ("display", LOCAL, REQUIRED | {"source_description"}),
            ]
        )
    )
    return rel


def detail_rows(relation, support="observed", limit=10001, bucket=None, after=None):
    spec, p = relation.spec, relation.product
    predicates, args = ["key_id IS NOT NULL"], []
    if support == "effective":
        predicates.append("effective AND expected IS DISTINCT FROM false")
    elif support == "paired":
        predicates.append("paired")
    elif support != "observed":
        raise Denied("invalid_request")
    if bucket:
        predicates.append("at >= %s AND at < %s")
        args.extend((bucket["start"], bucket["end"]))
        if "pos" in bucket:
            predicates.append("pos=%s")
            args.append(bucket["pos"])
        if "local_hour" in bucket:
            predicates.append("extract(hour from (at AT TIME ZONE %s)) = %s")
            args.extend((p.source_timezone, bucket["local_hour"]))
    if after:
        predicates.append("(at,pos,key_id)>(%s,%s,%s)")
        args.extend(after)
    # Project original contributing receipts once per page. Unknown/ambiguous lineage remains null.
    _, rev, _, _, fk = tables(p.level)
    # Optional source descriptions are separate from the research grant and from version-specific grades.
    description_select = description_join = description_column = ""
    if relation.source_description:
        description_select = """, CASE WHEN COUNT(*)=1 THEN min(CASE WHEN r.collection_item_id IS NULL
            THEN original.source_description ELSE r.collection_metadata->>'source_description' END) END AS description"""
        description_join = f"JOIN {rev._meta.db_table} original ON original.id=s.revision"
        description_column = (
            ", CASE WHEN quality <> 'disputed' THEN description END AS source_description"
        )
    rows = relation.query(
        f""", page AS MATERIALIZED (
        SELECT * FROM slots WHERE {" AND ".join(predicates)} ORDER BY at,pos,key_id LIMIT %s
    ), lineage AS (
        SELECT r.{fk}_id AS revision, CASE WHEN COUNT(*)=1 THEN min(b.fetched_at) END AS fetched_at {description_select}
        FROM page s JOIN observations_receipt r ON r.{fk}_id=s.revision
        JOIN observations_batch b ON b.id=r.batch_id AND b.product_id=%s AND NOT b.redacted
        {description_join}
        LEFT JOIN collection_run cr ON cr.batch_id=b.id LEFT JOIN collection_runitem i ON i.id=r.collection_item_id
        WHERE r.outcome IN ('first','content_changed') AND s.kind IN ('initial','source_revision')
        AND ((cr.id IS NULL AND i.id IS NULL AND NOT %s) OR
          (cr.id=i.run_id AND cr.window_id=i.window_id AND i.object_code=s.object_code AND i.indicator=s.indicator AND i.observed_at=s.at))
        GROUP BY r.{fk}_id
    ) SELECT s.pos,s.entity,s.indicator,s.unit,s.standard AS standard_series,at AS observed_at,s.key_id AS key,s.revision,
      qualification_version,quality,value,censor_limit,censor_direction,fetched_at {description_column} FROM page s LEFT JOIN lineage USING(revision) ORDER BY at,pos,key_id""",
        [*args, limit, p.pk, p.source.namespace in {"cnemc", "cnemc.fixture"}],
    )
    for row in rows:
        if spec.report == "daily":
            # A D report carries a source-local date, not a measured observation instant.
            row["local_report_date"] = (
                row["observed_at"].astimezone(ZoneInfo(p.source_timezone)).date()
            )
            row["observed_at"] = None
        row.update(
            source=p.source.namespace,
            product=p.pk,
            synthetic=p.synthetic,
            source_timezone=p.source_timezone,
            window_definition=spec.window,
            report_kind=spec.report,
            fetched_at_basis="contributing_batch" if row["fetched_at"] else "unknown",
            attribution=p.attribution,
        )
        for key in ("value", "censor_limit"):
            if row[key] is not None:
                row[key] = str(row[key])
    return rows
