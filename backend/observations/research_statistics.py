"""PostgreSQL aggregations of the shared, already-authorized research slot relation."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .research_coverage import merge_windows

# Versioned s05.v1 histogram cutoffs, in the canonical unit of each indicator.
CONCENTRATION_EDGES = (0, 10, 25, 50, 100, 200, 500)
CO_EDGES = (0, 0.5, 1, 2, 4, 8, 16)
HOUR = timedelta(hours=1)
TICK = timedelta(microseconds=1)

SAMPLES = "effective AND expected IS DISTINCT FROM false"
COUNT_COLUMNS = f"""
    count(*) FILTER (WHERE expected IS TRUE) AS "E",
    count(key_id) FILTER (WHERE expected IS DISTINCT FROM false) AS "O",
    count(*) FILTER (WHERE {SAMPLES}) AS "V",
    count(key_id) FILTER (WHERE expected IS FALSE) AS outside_schedule,
    count(*) FILTER (WHERE expected IS TRUE AND key_id IS NULL) AS gap,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'missing') AS missing,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'censored') AS censored,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'invalid') AS invalid,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'suspect') AS suspect,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'disputed') AS disputed,
    count(*) FILTER (WHERE expected IS DISTINCT FROM false AND quality = 'valid' AND NOT effective) AS unknown
"""
# Exact interpolation at (n−1)q. Ordered numeric arrays are bounded by QuerySpec.
QUANTILES_SQL = """
    sample_values[1 + floor(last_index * 0.25)::int]
      + (sample_values[1 + ceil(last_index * 0.25)::int] - sample_values[1 + floor(last_index * 0.25)::int])
        * mod(last_index * 0.25, 1) AS q1,
    sample_values[1 + floor(last_index * 0.5)::int]
      + (sample_values[1 + ceil(last_index * 0.5)::int] - sample_values[1 + floor(last_index * 0.5)::int])
        * mod(last_index * 0.5, 1) AS median,
    sample_values[1 + floor(last_index * 0.75)::int]
      + (sample_values[1 + ceil(last_index * 0.75)::int] - sample_values[1 + floor(last_index * 0.75)::int])
        * mod(last_index * 0.75, 1) AS q3
"""


def ratio(part, total):
    return Decimal(part) / total if total else None


def sufficient(part, total):
    return total is not None and total > 0 and 4 * part >= 3 * total


def denominator_state(total):
    if total is None:
        return "denominator_unknown"
    return "known" if total else "no_expected_slots"


def conclusion_state(total, numerator, full):
    state = denominator_state(total)
    if state != "known":
        return state
    if not sufficient(numerator, total):
        return "insufficient_coverage"
    return "sufficient" if full else "partial_coverage"


def bounds(start, end):
    return {"start": start, "end": end}


def support(start, end, kind, *, local_hour=None, pos=None):
    bucket = bounds(start, end)
    if local_hour is not None:
        bucket["local_hour"] = local_hour
    if pos is not None:
        bucket["pos"] = pos
    return {"bucket": bucket, "support": kind}


def window_coverage(row, known):
    expected = row["E"] if known else None
    return dict(
        E=expected,
        O=row["O"],
        V=row["V"],
        received_ratio=ratio(row["O"], expected),
        valid_ratio=ratio(row["V"], expected),
        outside_schedule=row["outside_schedule"],
        denominator_state=denominator_state(expected),
        exclusions={
            reason: row[reason]
            for reason in (
                "gap",
                "missing",
                "censored",
                "invalid",
                "suspect",
                "disputed",
                "unknown",
            )
        },
    )


def column_coverage(relation):
    rows = {
        row["pos"]: row
        for row in relation.query(f"SELECT pos, {COUNT_COLUMNS} FROM slots GROUP BY pos")
    }
    columns = []
    spec = relation.spec
    for entity in spec.ids:
        for indicator in spec.indicators:
            pos = len(columns)
            row = rows.get(pos)
            profile = relation.profiles[pos]
            if row is None:
                # The authorized relation has no eligible or expected rows for this column.
                count_fields = (
                    "E",
                    "O",
                    "V",
                    "outside_schedule",
                    "gap",
                    "missing",
                    "censored",
                    "invalid",
                    "suspect",
                    "disputed",
                    "unknown",
                )
                row = dict.fromkeys(count_fields, 0)
            columns.append(
                dict(
                    pos=pos,
                    entity=entity,
                    indicator=indicator,
                    unit=spec.units[indicator],
                    standard_series=spec.standards[indicator],
                    expected_windows=[bounds(*w) for w in profile["windows"]],
                    unknown_windows=[bounds(*w) for w in profile.get("unknown_windows", ())],
                    **window_coverage(row, profile["known"]),
                )
            )
    return columns


def common_windows(profiles):
    spans = profiles[0]["windows"]
    for profile in profiles[1:]:
        spans = merge_windows(
            (max(start, other_start), min(end, other_end))
            for start, end in spans
            for other_start, other_end in profile["windows"]
            if start < other_end and other_start < end
        )
    return spans


def paired_summary(relation, series):
    spec = relation.spec
    profile_known = all(p["known"] for p in relation.profiles)
    common = common_windows(relation.profiles)
    pair = relation.query(
        """, aligned AS (
        SELECT at, bool_or(expected_common) AS common, bool_or(paired) AS paired,
          max(value) FILTER (WHERE pos=0 AND paired) AS a,
          max(value) FILTER (WHERE pos=1 AND paired) AS b
        FROM slots GROUP BY at
    ) SELECT count(*) FILTER (WHERE common) AS expected,
        count(*) FILTER (WHERE common AND paired) AS paired,
        count(*) FILTER (WHERE paired) AS actual_pairs,
        avg(a-b) FILTER (WHERE common) AS mean_difference,
        corr(a,b) FILTER (WHERE common) AS pearson,
        (min(a) FILTER (WHERE common) <> max(a) FILTER (WHERE common)
         AND min(b) FILTER (WHERE common) <> max(b) FILTER (WHERE common)) AS variable
        FROM aligned"""
    )[0]
    expected = pair["expected"] if profile_known else None
    common_pairs = pair["paired"] if profile_known else None
    full = common == [[spec.start, spec.end]] and profile_known
    state = conclusion_state(expected, pair["paired"], full)
    result = {
        "E_common": expected,
        "P": common_pairs,
        "actual_pairs": pair["actual_pairs"],
        "paired_ratio": ratio(pair["paired"], expected),
        "expected_common_windows": [bounds(*w) for w in common],
        "paired_drilldown": support(spec.start, spec.end, "paired"),
        "conclusion": {"state": state},
    }
    if spec.method == "pearson":
        coefficient = None
        if state in {"sufficient", "partial_coverage"}:
            if pair["paired"] < 30:
                state = "insufficient_pairs"
            elif not pair["variable"] or pair["pearson"] is None:
                state = "zero_variance"
            else:
                coefficient = max(-1.0, min(1.0, pair["pearson"]))
        result.update(
            pearson=coefficient,
            interpretation="exploratory_pearson_seasonality_autocorrelation_missingness",
            conclusion={"state": state},
        )
    else:
        ranking = None
        mean_difference = None
        partial = None
        if state in {"sufficient", "partial_coverage"} and spec.indicators[0] != "AQI":
            ranked = relation.query(""", means AS (
                SELECT pos, entity, avg(value) AS mean FROM slots
                WHERE paired AND expected_common GROUP BY pos, entity
            ) SELECT *, rank() OVER (ORDER BY mean DESC) AS rank FROM means ORDER BY rank,pos""")
            if state == "sufficient":
                ranking, mean_difference = ranked, pair["mean_difference"]
            else:
                partial = {"ranking": ranked, "mean_difference": pair["mean_difference"]}
        result.update(ranking=ranking, mean_difference=mean_difference, partial_summary=partial)
    if series and spec.method == "compare":
        # Preserve ordered A−B and the B=0 case at an exact authorized pair.
        # For large queries the aggregate series remains bounded independently below.
        if pair["actual_pairs"] <= 1000:
            result["paired_points"] = relation.query(""", pairs AS (
                SELECT at, max(value) FILTER (WHERE pos=0) AS a,
                  max(value) FILTER (WHERE pos=1) AS b FROM slots WHERE paired GROUP BY at
            ) SELECT at, a-b AS difference, 100*(a-b)/nullif(b,0) AS percent_change
            FROM pairs ORDER BY at""")
    return result


def add_months(start, months):
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    return start.replace(year=year, month=month + 1)


def bucket_bounds(bucket, spec, zone, grain):
    if grain == "hour":
        start, end = bucket, bucket + HOUR
    else:
        start = bucket.replace(tzinfo=zone).astimezone(UTC)
        if grain == "day":
            limit = bucket + timedelta(days=1)
        else:
            limit = add_months(bucket, 3 if grain == "season" else 1)
        end = limit.replace(tzinfo=zone).astimezone(UTC)
    return max(spec.start, start), min(spec.end, end)


def statistics_buckets(relation, grain):
    spec, zone = relation.spec, ZoneInfo(relation.product.source_timezone)
    bucket_expression = {
        # A repeated local hour has two distinct UTC observation labels.
        "hour": "at",
        "day": "date_trunc('day', local_at)",
        "month": "date_trunc('month', local_at)",
        "season": "date_trunc('quarter', local_at + interval '1 month') - interval '1 month'",
    }[grain]
    # All output keys and grouping expressions are fixed in code. The timezone is a parameter.
    rows = relation.query(
        f""", labelled AS MATERIALIZED (
        SELECT *, {bucket_expression} AS bucket,
          CASE WHEN {SAMPLES} THEN value END AS sample
        FROM (SELECT *, at AT TIME ZONE %s AS local_at FROM slots) local_slots
    ), measures AS (
        SELECT pos, bucket, {COUNT_COLUMNS},
          avg(sample) AS mean, min(sample) AS minimum, max(sample) AS maximum,
          array_agg(sample ORDER BY sample) FILTER (WHERE sample IS NOT NULL) AS sample_values,
          count(sample)-1 AS last_index
        FROM labelled GROUP BY pos,bucket
    ), quartiles AS (
        SELECT *, {QUANTILES_SQL} FROM measures
    ) SELECT q.pos,q.bucket,q."E",q."O",q."V",q.outside_schedule,q.gap,q.missing,q.censored,
      q.invalid,q.suspect,q.disputed,q.unknown,q.mean,q.minimum,q.maximum,q.q1,q.median,q.q3,
      min(s.at) FILTER (WHERE s.sample=q.minimum) AS minimum_at,
      min(s.at) FILTER (WHERE s.sample=q.maximum) AS maximum_at,
      count(s.sample) FILTER (WHERE s.sample=q.minimum) AS minimum_ties,
      count(s.sample) FILTER (WHERE s.sample=q.maximum) AS maximum_ties,
      min(s.sample) FILTER (WHERE s.sample >= q.q1 - 1.5*(q.q3-q.q1)) AS lower_whisker,
      max(s.sample) FILTER (WHERE s.sample <= q.q3 + 1.5*(q.q3-q.q1)) AS upper_whisker
    FROM quartiles q JOIN labelled s USING(pos,bucket)
    GROUP BY q.pos,q.bucket,q."E",q."O",q."V",q.outside_schedule,q.gap,q.missing,q.censored,
      q.invalid,q.suspect,q.disputed,q.unknown,q.mean,q.minimum,q.maximum,q.q1,q.median,q.q3
    ORDER BY q.bucket,q.pos""",
        [zone.key],
    )
    results = []
    for row in rows:
        pos, bucket = row["pos"], row["bucket"]
        start, end = bucket_bounds(bucket, spec, zone, grain)
        profile = relation.profiles[pos]
        known = profile["known"] or (
            profile["has_calendar"]
            and not any(a < end and start < b for a, b in profile.get("unknown_windows", ()))
        )
        coverage = window_coverage(row, known)
        whole_window = any(a <= start and end <= b for a, b in profile["windows"])
        whole_calendar_bucket = (
            grain not in {"month", "season"}
            or start == bucket.replace(tzinfo=zone).astimezone(UTC)
            and end
            == add_months(bucket, 3 if grain == "season" else 1)
            .replace(tzinfo=zone)
            .astimezone(UTC)
        )
        state = conclusion_state(
            coverage["E"], coverage["V"], whole_window and whole_calendar_bucket
        )
        result = dict(
            pos=pos,
            start=start,
            end=end,
            **coverage,
            conclusion={"state": state},
            drilldown=support(start, end, "effective", pos=pos),
            observed_drilldown=support(start, end, "observed", pos=pos),
        )
        if spec.indicators[pos % len(spec.indicators)] != "AQI":
            result.update(
                mean=row["mean"],
                median=row["median"],
                minimum=row["minimum"],
                maximum=row["maximum"],
                minimum_at=row["minimum_at"],
                maximum_at=row["maximum_at"],
                minimum_ties=row["minimum_ties"],
                maximum_ties=row["maximum_ties"],
                box={k: row[k] for k in ("q1", "median", "q3", "lower_whisker", "upper_whisker")}
                | {"n": coverage["V"]},
            )
        if grain == "month":
            result["complete_month"] = whole_calendar_bucket and whole_window
            result["all_received"] = result["complete_month"] and coverage["E"] == coverage["O"]
            result["all_effective"] = result["complete_month"] and coverage["E"] == coverage["V"]
        if grain == "season":
            result["complete_season"] = whole_calendar_bucket and whole_window
            month = bucket.month
            result["season"] = {3: "spring", 6: "summer", 9: "autumn", 12: "winter"}[month]
            result["season_year"] = bucket.year + (month == 12)
        results.append(result)
    return results


def distribution(relation):
    edges = CO_EDGES if relation.spec.indicators[0] == "CO" else CONCENTRATION_EDGES
    rows = relation.query(
        f"""SELECT width_bucket(value,%s::numeric[]) AS bin, count(*) AS n
        FROM slots WHERE {SAMPLES} GROUP BY bin ORDER BY bin""",
        [[Decimal(str(edge)) for edge in edges]],
    )
    counts = {row["bin"]: row["n"] for row in rows}
    return [
        dict(lower=lower, upper=upper, n=counts.get(i, 0))
        for i, (lower, upper) in enumerate(zip((None, *edges), (*edges, None), strict=True))
    ]


def heatmap(relation):
    rows = relation.query(
        f""", local_slots AS (
        SELECT *, at AT TIME ZONE %s AS local_at FROM slots
    ) SELECT date_trunc('month',local_at)::date AS month, extract(hour FROM local_at)::int AS hour,
      {COUNT_COLUMNS}, avg(value) FILTER (WHERE {SAMPLES}) AS mean
    FROM local_slots GROUP BY month,hour ORDER BY month,hour""",
        [relation.product.source_timezone],
    )
    zone = ZoneInfo(relation.product.source_timezone)
    spec = relation.spec
    first_month = spec.start.astimezone(zone).date().replace(day=1)
    last_month = (spec.end - TICK).astimezone(zone).date().replace(day=1)
    indexed = {(r["month"], r["hour"]): r for r in rows}
    profile = relation.profiles[0]
    empty = dict.fromkeys(
        (
            "E",
            "O",
            "V",
            "outside_schedule",
            "gap",
            "missing",
            "censored",
            "invalid",
            "suspect",
            "disputed",
            "unknown",
        ),
        0,
    ) | {"mean": None}
    cells = []
    month = first_month
    while month <= last_month:
        label = datetime.combine(month, time.min)
        start, end = bucket_bounds(label, spec, zone, "month")
        known = profile["known"] or (
            profile["has_calendar"]
            and not any(a < end and start < b for a, b in profile.get("unknown_windows", ()))
        )
        for hour in range(24):
            row = indexed.get((month, hour), empty)
            cells.append(
                dict(
                    month=month,
                    hour=hour,
                    mean=row["mean"],
                    **window_coverage(row, known),
                    drilldown=support(start, end, "effective", local_hour=hour),
                    observed_drilldown=support(start, end, "observed", local_hour=hour),
                )
            )
        month = add_months(label, 1).date()
    return cells


def anomalies(relation, column, disclose_points):
    # Compute exact quartiles and test the IQR with numeric arithmetic in PostgreSQL.
    eligible = sufficient(column["V"], column["E"]) and column["V"] >= 30
    if not eligible:
        return {"state": "insufficient_samples", "count": None}
    query = f""", measures AS (
      SELECT array_agg(value ORDER BY value) AS sample_values, count(value)-1 AS last_index
      FROM slots WHERE {SAMPLES}
    ), quartiles AS (
      SELECT {QUANTILES_SQL} FROM measures
    ), outliers AS MATERIALIZED (
      SELECT at,key_id,value FROM slots CROSS JOIN quartiles WHERE {SAMPLES} AND q3>q1
        AND (value < q1-1.5*(q3-q1) OR value > q3+1.5*(q3-q1))
    ) """
    stats = relation.query(
        query
        + """SELECT q1, q3, (SELECT count(*) FROM outliers) AS n
      FROM quartiles"""
    )[0]
    if stats["q1"] == stats["q3"]:
        return {"state": "zero_iqr", "count": None}
    result = {"state": "ok", "count": stats["n"], "q1": stats["q1"], "q3": stats["q3"]}
    if disclose_points:
        rows = relation.query(query + "SELECT at,key_id,value FROM outliers ORDER BY at LIMIT 1000")
        spec = relation.spec
        result["candidates"] = [
            row
            | dict(
                drilldown=support(
                    max(spec.start, row["at"] - HOUR),
                    min(spec.end, row["at"] + 2 * HOUR),
                    "effective",
                )
            )
            for row in rows
        ]
        result["truncated"] = stats["n"] > len(rows)
    return result


def chart(relation):
    # Use a shared time axis so every column is downsampled into the same bins.
    spec = relation.spec
    row_count = relation.query("SELECT count(*) AS n FROM slots")[0]["n"]
    if row_count <= 1000:
        return {
            "resolution": "exact",
            "points": relation.query("""SELECT pos,at,quality,value,expected,
            key_id,censor_limit,censor_direction FROM slots ORDER BY at,pos"""),
        }
    column_count = len(relation.profiles)
    bins = 1000 // column_count
    microseconds = (spec.end - spec.start) // TICK
    width = ((microseconds + bins - 1) // bins) * TICK
    points = relation.query(
        f""", compressed AS (
      SELECT *, date_bin(%s::interval,at,%s::timestamptz) AS chart_bin FROM slots
    ) SELECT pos,chart_bin AS at,
      min(value) FILTER (WHERE {SAMPLES}) AS minimum,
      max(value) FILTER (WHERE {SAMPLES}) AS maximum,
      count(*) FILTER (WHERE {SAMPLES}) AS valid_count,
      count(*) FILTER (WHERE expected IS TRUE AND key_id IS NULL) AS gaps
    FROM compressed GROUP BY pos,chart_bin ORDER BY chart_bin,pos LIMIT 1000""",
        [width, spec.start],
    )
    return {"resolution": "range", "interval_seconds": width.total_seconds(), "points": points}


def summarize(relation, *, series):
    spec = relation.spec
    columns = column_coverage(relation)
    result = {
        "columns": columns,
        "state": "ok" if any(c["O"] or c["outside_schedule"] for c in columns) else "no_data",
        "expected_windows": [c["expected_windows"] for c in columns],
    }
    if spec.method in {"compare", "pearson"}:
        result.update(paired_summary(relation, series))
        if spec.method == "compare":
            result["buckets"] = statistics_buckets(relation, spec.grain)
    else:
        column = columns[0]
        state = conclusion_state(column["E"], column["V"], relation.profiles[0]["full"])
        result["conclusion"] = {"state": state}
        result["buckets"] = statistics_buckets(relation, spec.grain)
        if spec.indicators[0] != "AQI":
            column_stats = relation.query(f"SELECT avg(value) AS mean FROM slots WHERE {SAMPLES}")[
                0
            ]
            result.update(
                whole_period_mean=column_stats["mean"] if state == "sufficient" else None,
                partial_mean=column_stats["mean"] if state == "partial_coverage" else None,
                distribution=distribution(relation),
                anomalies=anomalies(relation, column, series),
            )
            if spec.grain in {"month", "season"}:
                result["heatmap"] = heatmap(relation)
                result["months"] = (
                    result["buckets"]
                    if spec.grain == "month"
                    else statistics_buckets(relation, "month")
                )
    if series:
        result["chart"] = chart(relation)
    return result
