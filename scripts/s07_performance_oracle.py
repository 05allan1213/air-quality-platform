"""Independent PostgreSQL grid and assertion helpers for S07/V13."""

import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from s07_performance_seed import COMPARE_START, START, guard, hour_at, sample_value, setup_django


class Mismatch(Exception):
    """Only a fixed check identifier may appear in a measurement receipt."""


def check(condition, code):
    if not condition:
        raise Mismatch(code)


def iso(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC).isoformat()


def number(val):
    return Decimal(str(val)) if val is not None else None


def close(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)


def rows_for(cursor, product, series, start, end):
    # Do not reuse the application's CTE, calendar, SQL projection or aggregation.
    cursor.execute(
        """SELECT t.at,e.pos,e.id,k.id,r.id,r.quality,r.value,r.unit,
                  k.standard_series,b.fetched_at
           FROM generate_series(%s::timestamptz,%s::timestamptz - interval '1 hour', interval '1 hour') t(at)
           CROSS JOIN unnest(%s::bigint[]) WITH ORDINALITY e(id,pos)
           LEFT JOIN observations_cityobservation k ON k.product_id=%s AND k.city_id=e.id
             AND k.indicator='CO' AND k.observed_at_utc=t.at AND k.report_kind='hourly'
             AND k.window_definition='hourly_label_unspecified' AND k.standard_series='not_applicable'
           LEFT JOIN observations_cityrevision r ON r.key_id=k.id AND r.is_current
           LEFT JOIN observations_receipt c ON c.city_revision_id=r.id AND c.outcome='first'
           LEFT JOIN observations_batch b ON b.id=c.batch_id AND b.product_id=%s AND NOT b.redacted
           ORDER BY t.at,e.pos""",
        (start, end, [s["id"] for s in series], product, product),
    )
    return [
        {
            "at": iso(at),
            "pos": pos - 1,
            "entity": entity,
            "key": key,
            "revision": revision,
            "quality": quality,
            "value": str(value) if value is not None else None,
            "unit": unit,
            "standard_series": standard,
            "fetched_at": iso(fetched_at) if fetched_at else None,
        }
        for at, pos, entity, key, revision, quality, value, unit, standard, fetched_at in cursor
    ]


def validate_grid(rows, series, manifest, start, hours):
    check(len(rows) == len(series) * hours, "sql_hour_grid_size")
    special = {
        (code, hour): (moved, quality, value)
        for code, hour, moved, quality, value in manifest["special"]
    }
    start_hour = (start - START) // timedelta(hours=1)
    for i, row in enumerate(rows):
        hour = start_hour + i // len(series)
        pos = i % len(series)
        current = series[pos]
        check(
            (row["at"], row["pos"], row["entity"]) == (iso(hour_at(hour)), pos, current["id"]),
            "sql_hour_grid_order",
        )
        moved, quality, value = special.get((current["code"], hour), (None, "valid", None))
        if moved is not None:
            check(row["key"] is None and row["revision"] is None, "intended_absent_key")
        else:
            expected = (
                (value if value is not None else sample_value("CO", current["pos"], hour))
                if quality == "valid"
                else None
            )
            check(
                row["key"]
                and row["revision"]
                and row["fetched_at"]
                and row["unit"] == "mg/m³"
                and row["standard_series"] == "not_applicable"
                and row["quality"] == quality
                and close(row["value"], expected),
                "sql_fixture_value",
            )


def collect(manifest):
    from django.db import connection, transaction

    run = guard()
    check(manifest["run"] == run and manifest["logical_hours"] == 1_000_000, "volume_manifest")
    cf = manifest["cities"]
    series = [s for s in cf["series"] if s["indicator"] == "CO"]
    bounds = {
        "detail": (series[:1], START, 200),
        "compare": (series, hour_at(COMPARE_START), 720),
        "year": (series[:1], START, 8784),
    }
    result = {}
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        for name, (columns, begin, hours) in bounds.items():
            rows = rows_for(cur, cf["product"], columns, begin, begin + timedelta(hours=hours))
            validate_grid(rows, columns, cf, begin, hours)
            result[name] = rows
    return {"run": run, "f_fingerprint": manifest["data_fingerprint"], "grid": result}


def coverage(rows):
    return {
        "E": len(rows),
        "O": sum(row["key"] is not None for row in rows),
        "V": sum(row["quality"] == "valid" for row in rows),
        "gap": sum(row["key"] is None for row in rows),
        "missing": sum(row["quality"] == "missing" for row in rows),
    }


def verify_coverage(projected, expected):
    for field in ("E", "O", "V"):
        check(projected[field] == expected[field], f"coverage_{field}")
    for kind in ("gap", "missing"):
        check(projected["exclusions"][kind] == expected[kind], f"coverage_{kind}")
    for numerator, field in (("O", "received_ratio"), ("V", "valid_ratio")):
        check(
            close(projected[field], Decimal(expected[numerator]) / expected["E"]),
            f"coverage_{field}",
        )


def matches_row(actual, expected):
    check(iso(actual["observed_at"]) == expected["at"], "detail_timestamp")
    check(
        actual["value"] is None
        if expected["value"] is None
        else Decimal(str(actual["value"])) == Decimal(expected["value"]),
        "detail_value",
    )
    for field in ("key", "revision", "quality", "unit", "standard_series"):
        check(actual[field] == expected[field], f"detail_{field}")
    check(actual.get("entity", actual.get("city")) == expected["entity"], "detail_entity")
    check(iso(actual["fetched_at"]) == expected["fetched_at"], "detail_lineage")


def verify_detail(actual, expected, *, page_size, next_page):
    check(actual["state"] == "ok" and actual["synthetic"], "detail_state")
    check(actual["page"]["page_size"] == page_size, "detail_page_size")
    check(bool(actual["page"]["next_cursor"]) is next_page, "detail_cursor_presence")
    check(len(actual["results"]) == len(expected), "detail_count")
    for row, oracle in zip(actual["results"], expected, strict=True):
        matches_row(row, oracle)


def verify_buckets(actual, rows, grain):
    groups = defaultdict(list)
    for row in rows:
        groups[row["at"][: 10 if grain == "day" else 7], row["pos"]].append(row)
    check(len(actual) == len(groups), "statistical_bucket_count")
    for measured, ((_, pos), cells) in zip(actual, sorted(groups.items()), strict=True):
        check(
            (iso(measured["start"]), iso(measured["end"]), measured["pos"])
            == (
                cells[0]["at"],
                iso(datetime.fromisoformat(cells[-1]["at"]) + timedelta(hours=1)),
                pos,
            ),
            "statistical_bucket_bounds",
        )
        verify_coverage(measured, coverage(cells))
        values = [number(cell["value"]) for cell in cells if cell["quality"] == "valid"]
        check(
            close(measured["mean"], sum(values) / len(values) if values else None),
            "statistical_bucket_mean",
        )
        check(close(measured["minimum"], min(values, default=None)), "statistical_bucket_minimum")
        check(close(measured["maximum"], max(values, default=None)), "statistical_bucket_maximum")
    return len(groups)


def verify_range(chart, rows, *, start, seconds):
    check(
        chart["resolution"] == "range" and chart["interval_seconds"] == seconds, "range_resolution"
    )
    groups = defaultdict(list)
    for row in rows:
        at = datetime.fromisoformat(row["at"])
        bucket = start + ((at - start) // timedelta(seconds=seconds)) * timedelta(seconds=seconds)
        groups[iso(bucket), row["pos"]].append(row)
    points = chart["points"]
    check(len(points) == len(groups) <= 1000, "range_point_count")
    for point, ((bucket, pos), cells) in zip(points, sorted(groups.items()), strict=True):
        check((iso(point["at"]), point["pos"]) == (bucket, pos), "range_point_index")
        values = [number(cell["value"]) for cell in cells if cell["quality"] == "valid"]
        check(close(point["minimum"], min(values, default=None)), "range_minimum")
        check(close(point["maximum"], max(values, default=None)), "range_maximum")
        check(
            point["valid_count"] == len(values)
            and point["gaps"] == sum(cell["key"] is None for cell in cells),
            "range_gap_or_valid_count",
        )
    return len(points)


def verify_exact(chart, rows):
    points = chart["points"]
    check(chart["resolution"] == "exact" and len(points) == len(rows) <= 1000, "exact_shape")
    required = {
        "pos",
        "at",
        "quality",
        "value",
        "expected",
        "key_id",
        "censor_limit",
        "censor_direction",
    }
    for point, cell in zip(points, rows, strict=True):
        check(required <= point.keys() and iso(point["at"]) == cell["at"], "exact_fields")
        check(
            point["pos"] == cell["pos"]
            and point["expected"] is True
            and point["key_id"] == cell["key"]
            and close(point["value"], cell["value"])
            and point["censor_limit"] is None
            and point["censor_direction"] in (None, "")
            and point["quality"] == (cell["quality"] if cell["key"] is not None else "gap"),
            "exact_slot",
        )
    return len(points)


def matched_hours(rows):
    hour_rows = defaultdict(list)
    for row in rows:
        hour_rows[row["at"]].append(row)
    paired = {
        at for at, cells in hour_rows.items() if all(row["quality"] == "valid" for row in cells)
    }
    return len(hour_rows), paired


def verify_compare(body, rows):
    check(body["state"] == "ok" and body["source_timezone"] == "UTC", "compare_state")
    columns = body["columns"]
    check(len(columns) == 5 and len(rows) == 3600, "compare_column_count")
    counts = []
    for pos, (column, example) in enumerate(zip(columns, rows[:5], strict=True)):
        check(
            (
                column["pos"],
                column["entity"],
                column["indicator"],
                column["unit"],
                column["standard_series"],
            )
            == (pos, example["entity"], "CO", "mg/m³", "not_applicable"),
            "compare_column_identity",
        )
        expected = coverage(rows[pos::5])
        verify_coverage(column, expected)
        counts.append(
            expected | {"O/E": expected["O"] / expected["E"], "V/E": expected["V"] / expected["E"]}
        )
    common, paired = matched_hours(rows)
    check(
        body["E_common"] == common == 720
        and body["P"] == len(paired)
        and close(body["paired_ratio"], len(paired) / common),
        "compare_pair_coverage",
    )
    check(body["input_version"], "compare_input_version")
    buckets = verify_buckets(body["buckets"], rows, "day")
    range_points = verify_range(body["chart"], rows, start=hour_at(COMPARE_START), seconds=12960)
    check(
        any(p["gaps"] and p["valid_count"] == 0 for p in body["chart"]["points"]),
        "compare_all_invalid_bucket",
    )
    return {
        "columns": counts,
        "E_common": common,
        "P": len(paired),
        "P/E_common": len(paired) / common,
        "days_by_column": buckets,
        "range_points": range_points,
    }


def verify_year(body, rows):
    check(
        body["state"] == "ok" and len(body["columns"]) == 1 and body["source_timezone"] == "UTC",
        "annual_state",
    )
    expected = coverage(rows)
    check(expected["E"] in (8760, 8784), "annual_hour_count")
    verify_coverage(body["columns"][0], expected)
    check(verify_buckets(body["buckets"], rows, "month") == 12, "annual_month_count")
    values = [number(cell["value"]) for cell in rows if cell["quality"] == "valid"]
    check(close(body["whole_period_mean"], sum(values) / len(values)), "annual_weighted_mean")
    return expected | {"months": 12, "weighted_mean": str(sum(values) / len(values))}


def main():
    setup_django()
    print(json.dumps(collect(json.load(sys.stdin)), ensure_ascii=False))


if __name__ == "__main__":
    main()
