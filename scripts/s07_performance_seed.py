"""S07 volume fixture. Run only inside a V01-owned, freshly migrated A or B."""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic

from s06_guard import require
from verify_s07 import identities

START = datetime(2024, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
HOURS = 10_000
BLOCK_HOURS = 12
WRITE_HOURS = BLOCK_HOURS * 20
SEED = 707
SOURCE = "s07.fixture"
CITIES = tuple(f"s07-city-{i}" for i in range(5))
CITY_INDICATORS = ("CO", "PM2.5", "PM10", "SO2", "NO2", "O3", "AQI")
STATION_INDICATORS = ("CO", "PM2.5", "NO2", "O3")
STATIONS = tuple(f"s07-site-{i:02d}" for i in range(20))
USERS = tuple(f"s07-reader-{i}" for i in range(5))
COMPARE_START = 31 * 24
# Offsets are relative to the 30-day R3 window. The first CO series has an
# all-invalid chart interval containing both absent observations and a missing value.
MISSING = {0: (208, 310), 1: (341,), 3: (534,)}
NO_KEY = {0: (206, 207, 486), 2: (318, 319), 4: (542,)}
PEAK = {0: (100, 28.5), 2: (321, 22.75), 4: (541, 19.5)}
PG_MARKER = "airquality.s07_instance"
REDIS_MARKER = "airquality:s07:instance"


def guard():
    from django.conf import settings
    from django.db import connection

    env = os.environ
    run = env.get("AQ_INSTANCE_ID", "")
    require(not env.get("PGSERVICE") and not env.get("PGOPTIONS"), "database_override")
    require(settings.CNEMC_MODE == "off", "source_mode")
    require(
        all(env.get(key, "0") == "0" for key in ("CNEMC_FIXTURE_HTTP", "S03_FIXTURE_HTTP")),
        "source_fixture_enabled",
    )
    if run.startswith("s07a-"):
        from redis import Redis

        database, role = identities(run)
        require(
            connection.settings_dict["HOST"] == "db"
            and str(connection.settings_dict["PORT"]) == "5432"
            and settings.CELERY_BROKER_URL == "redis://redis:6379/0",
            "foreign_compose_endpoint",
        )
        default_routes = any(
            row.split()[1] == "00000000"
            for row in Path("/proc/net/route").read_text().splitlines()[1:]
        )
        require(not default_routes, "external_route")
        with Redis(host="redis", socket_timeout=2, socket_connect_timeout=2) as redis:
            require(redis.get(REDIS_MARKER) == run.encode(), "broker_identity")
    else:
        from s07_host_guard import check_broker, check_process_network, identity

        require(re.fullmatch(r"s07-b-[a-f0-9]{12}", run), "foreign_host_instance")
        database, role = identity(run)
        guard_env = {k: v for k, v in env.items() if k != "DJANGO_SETTINGS_MODULE"}
        check_process_network(guard_env.get("S06_HOST_NET_NS"))
        check_broker(guard_env, empty=False)
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT current_database(), current_user, current_setting(%s, true),
                rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
                FROM pg_roles WHERE rolname=current_user""",
            (PG_MARKER,),
        )
        require(cursor.fetchone() == (database, role, run, False), "database_identity")
    return run


def hour_at(hour):
    return START + hour * HOUR


def sample_value(indicator, series, hour):
    base = (hour * 11 + series * 13 + SEED) % 113
    return Decimal(100 + base).scaleb(-2) if indicator == "CO" else 2 + base


def special_rows():
    """Each entry gives (city, input hour, moved hour or None, quality, value)."""
    rows = []
    for city, offsets in NO_KEY.items():
        for ordinal, offset in enumerate(offsets):
            rows.append((city, COMPARE_START + offset, HOURS + ordinal, "valid", None))
    for city, offsets in MISSING.items():
        rows.extend((city, COMPARE_START + offset, None, "missing", None) for offset in offsets)
    for city, (offset, value) in PEAK.items():
        rows.append((city, COMPARE_START + offset, None, "valid", value))
    return sorted(rows)


def series_for(level):
    if level == "city":
        return [
            (code, indicator)
            for i, code in enumerate(CITIES)
            for indicator in CITY_INDICATORS[: 7 if i < 2 else 2]
        ]
    return [(code, indicator) for code in STATIONS for indicator in STATION_INDICATORS]


def clone_columns(model, replacements, alias):
    """SQL names come from static model metadata and internal expressions only."""
    columns = [f.column for f in model._meta.concrete_fields if not f.primary_key]
    return ", ".join(columns), ", ".join(replacements.get(c, f"{alias}.{c}") for c in columns)


def insert_cells(cursor, product, level, templates, specials, *, hours=HOURS):
    from django.db import transaction
    from observations.models import Receipt
    from observations.revisions import tables

    key, rev, _, _, fk = tables(level)
    table = key._meta.db_table
    revision = rev._meta.db_table
    receipt_fk = fk + "_id"
    key_columns, key_values = clone_columns(
        key, {"observed_at_utc": "cell.at", "source_local_date": "cell.at::date"}, "template"
    )
    rev_columns, rev_values = clone_columns(
        rev,
        {
            "key_id": "new_key.id",
            "value": "cell.value",
            "quality": "cell.quality",
            "created_at": "statement_timestamp()",
            "provenance": """original.provenance || jsonb_build_object(
                'source_time', to_char(cell.at, 'YYYY-MM-DD\"T\"HH24:MI:SS') || '+00:00',
                'issue', CASE WHEN cell.quality='missing' THEN 'missing_na' ELSE '' END)""",
        },
        "original",
    )
    receipt_columns, receipt_values = clone_columns(
        Receipt,
        {
            "batch_id": "batch.id",
            receipt_fk: "new_rev.id",
            "ordinal": "1 + (cell.destination %% %s) * %s + cell.series",
        },
        "original_receipt",
    )
    cursor.execute("""CREATE TEMP TABLE v13_template (series int, key_id bigint,
        rev_id bigint, code text, indicator text) ON COMMIT PRESERVE ROWS""")
    cursor.executemany("INSERT INTO v13_template VALUES (%s,%s,%s,%s,%s)", templates)
    cursor.execute("""CREATE TEMP TABLE v13_special (code text, hour int, moved_to int,
        quality text, value numeric) ON COMMIT PRESERVE ROWS""")
    cursor.executemany("INSERT INTO v13_special VALUES (%s,%s,%s,%s,%s)", specials)
    try:
        for lower in range(0, hours, WRITE_HOURS):
            upper = min(lower + WRITE_HOURS, hours)
            with transaction.atomic():
                cursor.execute(
                    f"""WITH assigned AS (
                        SELECT t.*, h.hour, COALESCE(s.moved_to, h.hour) AS destination,
                            COALESCE(s.quality, 'valid') AS quality, s.value AS special_value
                        FROM v13_template t CROSS JOIN generate_series(%s, %s) h(hour)
                        LEFT JOIN v13_special s ON t.code=s.code AND t.indicator='CO'
                            AND h.hour=s.hour
                    ), cell AS MATERIALIZED (
                        SELECT a.*, %s::timestamptz + destination * interval '1 hour' AS at,
                            CASE WHEN quality='missing' THEN NULL ELSE COALESCE(special_value,
                                CASE WHEN indicator='CO' THEN 1+((hour*11+series*13+%s) %% 113)/100.0
                                ELSE 2+((hour*11+series*13+%s) %% 113) END) END AS value
                        FROM assigned a
                    ), new_batch AS (
                        INSERT INTO observations_batch(product_id, identity, profile, acquired_at,
                            fetched_at, redacted)
                        SELECT %s, 's07-f-hours-' || block, %s,
                            %s::timestamptz + block * interval '1 hour',
                            %s::timestamptz + (block+%s) * interval '1 hour', false
                        FROM (SELECT DISTINCT (destination / %s) * %s AS block FROM cell) b
                        ON CONFLICT (product_id, identity, profile) DO UPDATE
                        SET fetched_at=excluded.fetched_at
                        RETURNING id, acquired_at
                    ), new_key AS (
                        INSERT INTO {table} ({key_columns})
                        SELECT {key_values} FROM cell JOIN {table} template ON template.id=cell.key_id
                        RETURNING id, observed_at_utc, object_code, indicator
                    ), new_rev AS (
                        INSERT INTO {revision} ({rev_columns})
                        SELECT {rev_values} FROM cell
                        JOIN {revision} original ON original.id=cell.rev_id
                        JOIN new_key ON new_key.object_code=cell.code
                            AND new_key.indicator=cell.indicator AND new_key.observed_at_utc=cell.at
                        RETURNING id, key_id
                    )
                    INSERT INTO observations_receipt ({receipt_columns})
                    SELECT {receipt_values} FROM cell
                    JOIN new_key ON new_key.object_code=cell.code
                        AND new_key.indicator=cell.indicator AND new_key.observed_at_utc=cell.at
                    JOIN new_rev ON new_rev.key_id=new_key.id
                    JOIN observations_receipt original_receipt ON original_receipt.{receipt_fk}=cell.rev_id
                    JOIN new_batch batch ON batch.acquired_at=%s::timestamptz
                        + ((cell.destination / %s) * %s) * interval '1 hour'""",
                    (
                        max(1, lower),
                        min(hours, upper) - 1,
                        START,
                        SEED,
                        SEED,
                        product.pk,
                        product.contract_version,
                        START,
                        START,
                        BLOCK_HOURS,
                        BLOCK_HOURS,
                        BLOCK_HOURS,
                        BLOCK_HOURS,
                        len(templates),
                        START,
                        BLOCK_HOURS,
                        BLOCK_HOURS,
                    ),
                )
                require(
                    cursor.rowcount == len(templates) * max(0, min(hours, upper) - max(1, lower)),
                    "wrong_bulk_count",
                )
    finally:
        cursor.execute("DROP TABLE v13_template, v13_special")


def setup_product(operator, source, shape, cities, *, hours):
    from accounts.research_exports import export_fields
    from django.db import connection
    from observations.models import Batch, Receipt
    from observations.policy import REQUIRED
    from observations.revisions import tables
    from s05_support import ANALYZE, DISPLAY, RETAIN, candidate, grant, ingest, product, site

    profile = product(code=f"s07-f-{shape.lower()}", shape=shape, source=source)
    end = hour_at(hours + 24)
    grants = {
        operation: grant(operator, profile, operation, fields=fields, start=START, end=end)
        for operation, fields in {
            "process_local": REQUIRED,
            "retain_normalized": RETAIN | {"source_time"},
            "analyze": ANALYZE,
            "display": DISPLAY | {"source_time"},
            "export": export_fields("detail"),
        }.items()
    }
    code_to_entity = cities
    if shape == "S":
        code_to_entity = {
            code: site(profile, code, parent=cities[CITIES[i // 4]], end=end, name=f"F {code}")
            for i, code in enumerate(STATIONS)
        }
    series = series_for(profile.level)
    ingest(
        operator,
        profile,
        [
            candidate(
                profile,
                code,
                value=sample_value(indicator, pos, 0),
                indicator=indicator,
                fetched_at=hour_at(1),
            )
            for pos, (code, indicator) in enumerate(series)
        ],
    )
    key, rev, _, _, fk = tables(profile.level)
    revisions = {
        (code, ind): (key_id, rev_id)
        for code, ind, key_id, rev_id in rev.objects.filter(
            key__product=profile, is_current=True, key__observed_at_utc=START
        ).values_list("key__object_code", "key__indicator", "key_id", "id")
    }
    require(len(revisions) == len(series), "normalized_template_missing")
    originals = Receipt.objects.filter(batch__product=profile)
    require(originals.count() == len(series), "template_receipts_missing")
    # Keep the service-created start-hour template batch's actual observation bounds.
    Batch.objects.filter(product=profile).update(acquired_at=START)
    templates = [
        (pos, *revisions[(code, ind)], code, ind) for pos, (code, ind) in enumerate(series)
    ]
    specials = [
        (CITIES[city], hour, moved, quality, value)
        for city, hour, moved, quality, value in special_rows()
        if shape == "C" and hour < hours
    ]
    with connection.cursor() as cursor:
        insert_cells(cursor, profile, profile.level, templates, specials, hours=hours)
    return {
        "product": profile.pk,
        "level": profile.level,
        "shape": shape,
        "synthetic": profile.synthetic,
        "model": key._meta.db_table,
        "source": SOURCE,
        "contract_version": profile.contract_version,
        "calendar_evidence": profile.evidence,
        "calendar_period": ["2023-12-01T00:00:00Z", "2025-01-01T00:00:00Z"],
        "source_timezone": profile.source_timezone,
        "source_use": {
            op: {
                field: getattr(row, field)
                for field in (
                    "audience",
                    "geography",
                    "data_from",
                    "data_until",
                    "fields",
                    "terms_version",
                    "attribution",
                )
            }
            for op, row in grants.items()
        },
        "valid_period": [START.isoformat(), end.isoformat()],
        "series": [
            {"pos": pos, "code": code, "id": code_to_entity[code].pk, "indicator": indicator}
            for pos, (code, indicator) in enumerate(series)
        ],
        "special": specials,
    }


def table_evidence(cursor, config, hours):
    from observations.revisions import tables

    key, rev, _, _, fk = tables(config["level"])
    k, r = key._meta.db_table, rev._meta.db_table
    pk = config["product"]
    cursor.execute(
        f"""SELECT k.object_code, k.indicator, r.quality, count(*) FROM {k} k
            JOIN {r} r ON r.key_id=k.id WHERE k.product_id=%s
            GROUP BY 1,2,3 ORDER BY 1,2,3""",
        (pk,),
    )
    by_series = cursor.fetchall()
    totals = Counter()
    for code, indicator, _quality, count in by_series:
        totals[code, indicator] += count
    require(
        totals == Counter({(s["code"], s["indicator"]): hours for s in config["series"]}),
        "series_count",
    )
    cursor.execute(
        f"""SELECT count(*), count(DISTINCT (object_code,indicator,observed_at_utc)),
                   count(*) FILTER (WHERE date_trunc('hour',observed_at_utc)<>observed_at_utc)
            FROM {k} WHERE product_id=%s""",
        (pk,),
    )
    key_count, distinct_keys, nonhourly = cursor.fetchone()
    require(
        key_count == distinct_keys == len(config["series"]) * hours and not nonhourly, "key_count"
    )
    cursor.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE r.is_current AND NOT r.candidate),
                   count(DISTINCT r.key_id) FILTER (WHERE r.is_current),
                   count(c.id) FILTER (WHERE b.product_id=k.product_id AND c.outcome='first'
                       AND k.observed_at_utc>=b.acquired_at AND k.observed_at_utc<b.fetched_at),
                   count(DISTINCT b.id)
            FROM {k} k JOIN {r} r ON r.key_id=k.id
            JOIN observations_receipt c ON c.{fk}_id = r.id
            JOIN observations_batch b ON b.id=c.batch_id WHERE k.product_id=%s""",
        (pk,),
    )
    joined, currents, represented, receipts, batches = cursor.fetchone()
    require(joined == currents == represented == receipts == key_count, "current_or_receipt_count")
    cursor.execute(
        f"""SELECT k.observed_at_utc, count(*),
                   count(*) FILTER (WHERE r.quality='missing') FROM {k} k JOIN {r} r ON r.key_id=k.id
            WHERE k.product_id=%s GROUP BY 1 ORDER BY 1""",
        (pk,),
    )
    by_hour = cursor.fetchall()
    cursor.execute(
        f"""SELECT k.object_code,k.indicator,k.observed_at_utc,r.value,r.quality,
                   r.unit,k.standard_series,r.provenance->>'source_timezone',
                   r.provenance->>'source_time', b.acquired_at, b.fetched_at, c.outcome
            FROM {k} k JOIN {r} r ON r.key_id=k.id
            JOIN observations_receipt c ON c.{fk}_id=r.id
            JOIN observations_batch b ON b.id=c.batch_id
            WHERE k.product_id=%s AND k.observed_at_utc IN (%s,%s,%s,%s)
            ORDER BY k.object_code,k.indicator,k.observed_at_utc""",
        (pk, START, hour_at(1), hour_at(min(hours - 1, 844)), hour_at(hours)),
    )
    sample = cursor.fetchall()
    require(
        all(
            row[7] == "UTC"
            and isinstance(row[8], str)
            and datetime.fromisoformat(row[8]) == row[2]
            and row[9] <= row[2] < row[10]
            and row[11] == "first"
            for row in sample
        ),
        "fixture_provenance",
    )
    return {
        "keys": key_count,
        "unique_hour_keys": distinct_keys,
        "current_revisions": currents,
        "contributing_receipts": receipts,
        "contributing_batches": batches,
        "by_object_indicator_quality": by_series,
        "by_hour_count_missing": by_hour,
        "lineage_samples": sample,
    }


def seed(*, hours=HOURS):
    from django.contrib.auth import get_user_model
    from django.db import connection
    from observations.models import CityObservation, Product, Source, StationObservation
    from s05_support import city

    run = guard()
    fixture_secret = os.environ.get("S07_PERF_PASSWORD", "")
    require(len(fixture_secret) >= 20, "missing_ephemeral_test_password")
    users = get_user_model()
    require(
        not any(
            model.objects.exists()
            for model in (Product, CityObservation, StationObservation, users)
        ),
        "fixture_requires_empty_application_data",
    )
    begin = monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_current_wal_lsn()")
        initial_lsn = str(cursor.fetchone()[0])
    op = users.objects.create_superuser(username="s07-volume-writer", email="", password=None)
    require(
        op.is_active and op.is_superuser and not op.has_usable_password(), "fixture_writer_identity"
    )
    user_ids = {
        name: users.objects.create_user(username=name, password=fixture_secret).pk for name in USERS
    }
    source = Source.objects.create(namespace=SOURCE, publisher="S07 项目自制 F")
    # Product ownership is by source, so both products refer to exactly the same city IDs.
    fake = Product(source=source)
    cities = {code: city(fake, code, end=hour_at(hours + 24), name=f"F {code}") for code in CITIES}
    city_config = setup_product(op, source, "C", cities, hours=hours)
    station_config = setup_product(op, source, "S", cities, hours=hours)
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE")
        for config in (city_config, station_config):
            config["facts"] = table_evidence(cursor, config, hours)
        cursor.execute("""SELECT relname, pg_relation_size(oid), pg_indexes_size(oid),
            pg_total_relation_size(oid) FROM pg_class
            WHERE relname IN ('observations_cityobservation','observations_cityrevision',
                'observations_stationobservation','observations_stationrevision','observations_receipt',
                'observations_batch') ORDER BY relname""")
        relations = cursor.fetchall()
        cursor.execute(
            """SELECT pg_database_size(current_database()), temp_bytes,
                pg_current_wal_lsn(), pg_wal_lsn_diff(pg_current_wal_lsn(), %s::pg_lsn)
            FROM pg_stat_database WHERE datname=current_database()""",
            (initial_lsn,),
        )
        db_size, temp_bytes, final_lsn, wal_bytes = cursor.fetchone()
    digest_input = {
        "seed": SEED,
        "hours": hours,
        "source": SOURCE,
        "city_series": series_for("city"),
        "station_series": series_for("station"),
        "special": special_rows(),
        "start": START.isoformat(),
        "source_files": {
            str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), Path(sys.modules["s05_support"].__file__))
        },
    }
    return {
        "run": run,
        "evidence_type": "F",
        "purpose": "read performance only",
        "logical_hours": city_config["facts"]["keys"] + station_config["facts"]["keys"],
        "data_spec": digest_input,
        "data_fingerprint": hashlib.sha256(
            json.dumps(digest_input, sort_keys=True).encode()
        ).hexdigest(),
        "cities": city_config,
        "stations": station_config,
        "users": user_ids,
        "frozen_at": datetime.now(UTC).isoformat(),
        "accounts": {"active_readers": len(user_ids), "writer_has_usable_password": False},
        "relations_heap_index_total_bytes": relations,
        "database_bytes": db_size,
        "temp_bytes": temp_bytes,
        "wal": {
            "start_lsn": initial_lsn,
            "end_lsn": str(final_lsn),
            "generated_bytes": int(wal_bytes),
        },
        "elapsed_s": round(monotonic() - begin, 3),
    }


def setup_django():
    import django

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    require(
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings") == "config.settings",
        "foreign_settings",
    )
    django.setup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Only four hours per series in a separate disposable fixture",
    )
    args = parser.parse_args()
    setup_django()
    print(json.dumps(seed(hours=4 if args.smoke else HOURS), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
