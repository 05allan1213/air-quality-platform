"""V12/V17 real Session HTTP limits and cost evidence for disposable PostgreSQL."""

import csv
import hashlib
import json
import math
import os
import platform
import resource
import statistics
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from itertools import count
from pathlib import Path
from time import perf_counter, sleep

import httpx
import pytest
from accounts.models import ExportTicket
from accounts.research_exports import export_fields
from django.db import connection, transaction
from observations.models import Product
from observations.normalization import CONCENTRATIONS
from s02_support import wait_for_block
from s05_http import TRACE_HEADER, RequestTrace, loopback
from s05_support import authorize, city, grant, product
from s05_volume import add_rows, coordinates, require_owned_database
from test_s05_selection import parent

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not (os.environ.get("S05_VERIFY_RUN") or os.environ.get("S06_RUN")),
        reason="requires an owned S05 or S06 verification cluster",
    ),
]
STAT = "/api/v1/analysis/statistics/"
COMPARE = "/api/v1/analysis/compare/"
DETAIL = "/api/v1/analysis/details/"
EXPORT = "/api/v1/me/exports/"
INDICATORS = ("AQI", *CONCENTRATIONS)
CORRELATIONS = count(1)


@pytest.fixture
def identities(django_user_model):
    require_owned_database()
    return (
        django_user_model.objects.create_superuser("cost-writer"),
        django_user_model.objects.create_user("cost-reader", password="s05-test-only"),
    )


def make_cities(op, p, codes):
    authorize(op, p)
    return [city(p, code) for code in codes]


def evidence(subdir, filename, data):
    dest = require_owned_database() / subdir
    dest.mkdir(exist_ok=True)
    (dest / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def call(web, records, verb, path, payload=None, *, observe=None):
    correlation = str(next(CORRELATIONS))
    options = {"headers": {TRACE_HEADER: correlation}}
    if payload is not None:
        options["json"] = payload
    start = perf_counter()
    try:
        response = web.request(verb, path, **options)
    except httpx.TransportError as exc:
        if observe is not None:
            trace = records.get(correlation, RequestTrace())
            observe(request_sample(correlation, trace, start) | {"exception": type(exc).__name__})
        raise
    trace = records[correlation]
    sample = request_sample(correlation, trace, start) | {
        "response_bytes": len(response.content),
        "status": response.status_code,
    }
    if observe is not None:
        if response.headers.get("content-type", "").startswith("text/csv"):
            sample["csv_bytes"] = len(response.content)
        if response.status_code >= 400 and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            try:
                code = response.json().get("code")
            except (AttributeError, ValueError):
                pass
            else:
                sample["code"] = code
        observe(sample)
    return response, sample, trace.queries


def request_sample(correlation, trace, start):
    queries = tuple(trace.queries)
    return {
        "correlation": int(correlation),
        "http_ms": (perf_counter() - start) * 1000,
        "sql_count": len(queries),
        "sql_ms": sum(query[2] for query in queries),
        "max_statement_ms": max((query[2] for query in queries), default=0),
        "sqlstates": sorted(set(trace.sqlstates)),
        "response_started": trace.response_started,
    }


def csv_observer(record_property, phase, authorized_rows):
    def store(sample):
        from s06_observed import safe_csv_probe

        resource_sample = {
            "load_1m": os.getloadavg()[0],
            "test_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        probe = {"phase": phase, "authorized_rows": authorized_rows} | sample | resource_sample
        record_property("s07_csv_probe", json.dumps(safe_csv_probe(probe)))

    return store


def nodes(plan):
    keep = (
        "Node Type",
        "Relation Name",
        "Index Name",
        "Join Type",
        "Actual Rows",
        "Actual Loops",
        "Rows Removed by Filter",
        "Actual Total Time",
        "Sort Method",
        "Sort Space Type",
        "Shared Hit Blocks",
        "Shared Read Blocks",
        "Temp Read Blocks",
        "Temp Written Blocks",
    )
    yield {key: plan[key] for key in keep if key in plan}
    for child in plan.get("Plans", []):
        yield from nodes(child)


def explain(queries):
    plans = []
    grant_index = next(
        i for i, (sql, _, _) in enumerate(queries) if 'FROM "observations_usegrant"' in sql
    )
    with connection.cursor() as cursor:
        for index, (sql, params, _) in enumerate(queries):
            if not sql.lstrip().startswith("WITH cols AS"):
                continue
            assert grant_index < index
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            plan = cursor.fetchone()[0][0]
            plans.append(
                {
                    "statement_index": index,
                    "grant_statement_index": grant_index,
                    "planning_ms": plan["Planning Time"],
                    "execution_ms": plan["Execution Time"],
                    "nodes": list(nodes(plan["Plan"])),
                }
            )
    assert plans
    return plans


def profile(web, records, path, request, *, repeat=20):
    response, first, trace = call(web, records, "POST", path, request)
    assert response.status_code == 200, response.content[:1024]
    warm = [call(web, records, "POST", path, request)[1] for _ in range(repeat)]
    return {
        "first_http": first,
        "warm": summarize_samples(warm),
        "explain_analyze_buffers": explain(trace),
    }


def summarize_samples(samples):
    elapsed = sorted(x["http_ms"] for x in samples)
    return {
        "samples": samples,
        "runs": len(samples),
        "mean_ms": statistics.mean(elapsed),
        "p95_ms": elapsed[math.ceil(0.95 * len(elapsed)) - 1],
        "max_sql_count": max(x["sql_count"] for x in samples),
        "error_count": sum(x["status"] >= 400 for x in samples),
    }


def machine():
    cpu_info = Path("/proc/cpuinfo").read_text()
    memory_info = Path("/proc/meminfo").read_text()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('server_version'), current_setting('shared_buffers'), current_setting('work_mem')"
        )
        pg, buffers, work_mem = cursor.fetchone()
    return {
        "cpu": next(
            line.split(":", 1)[1].strip()
            for line in cpu_info.splitlines()
            if line.startswith("model name")
        ),
        "logical_cpus": os.cpu_count(),
        "allowed_cpus": len(os.sched_getaffinity(0)),
        "ram": next(
            line.split(":", 1)[1].strip()
            for line in memory_info.splitlines()
            if line.startswith("MemTotal")
        ),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "postgres": pg,
        "shared_buffers": buffers,
        "work_mem": work_mem,
        "concurrency": 1,
        "cache_conditions": "First HTTP after seeding and ANALYZE, then repeated identical HTTP. Writes may populate PG/OS caches. No cache eviction or server restart.",
        "source": "F templates via ingest_batch, PG bulk clones of key, revision and receipt",
    }


def csv_environment():
    from s06_observed import safe_csv_environment

    host = machine()
    meminfo = Path("/proc/meminfo").read_text().splitlines()
    return safe_csv_environment(
        {
            **host,
            "memory_total_kib": int(host["ram"].split()[0]),
            "memory_available_kib": int(
                next(line.split()[1] for line in meminfo if line.startswith("MemAvailable:"))
            ),
            "transaction_budget_ms": 10_000,
            "statement_budget_ms": 5_000,
            "lock_budget_ms": 1_000,
            "http_client_timeout_ms": 12_000,
        }
    )


def lock_probe(web, records, p, query):
    with ThreadPoolExecutor(max_workers=1) as workers:
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=p.pk)
            future = workers.submit(call, web, records, "POST", STAT, query)
            # The application backend is the only other connection in this runner DB.
            with connection.cursor() as cur:
                deadline = perf_counter() + 5
                while perf_counter() < deadline:
                    cur.execute("SELECT pg_stat_clear_snapshot()")
                    cur.execute("""SELECT pid FROM pg_stat_activity WHERE datname=current_database()
                        AND pg_backend_pid() = ANY(pg_blocking_pids(pid))""")
                    if blocked := cur.fetchone():
                        break
                    sleep(0.01)
                else:
                    pytest.fail("Expected research request waiting on the product lock")
            wait_for_block(blocked[0])
            sleep(0.06)
        response, sample, trace = future.result(timeout=10)
    assert response.status_code == 200
    product_attempts = [
        (sql, ms) for sql, _, ms in trace if 'FOR UPDATE OF "observations_product"' in sql
    ]
    assert (
        len(product_attempts) == 2
        and "NOWAIT" in product_attempts[0][0]
        and "NOWAIT" not in product_attempts[1][0]
    )
    product_locks = [product_attempts[1][1]]
    assert product_locks[0] >= 60
    return sample | {
        "product_lock_statement_ms": product_locks[0],
        "pg_blocking_pids_observed": True,
    }


def analyze_volume():
    with connection.cursor() as cursor:
        for table in ("cityobservation", "cityrevision", "receipt"):
            cursor.execute(f"ANALYZE observations_{table}")


def test_v17_research_and_drilldown_cost(identities):
    op, user = identities
    p = product("cost-comparison", "C")
    year = product("cost-year", "C")
    codes = ["101", "102", "103", "104", "105"]
    cities = make_cities(op, p, codes)
    y = make_cities(op, year, ["201"])[0]
    q24 = parent(p, [cities[0]], method="statistics")
    qyear = parent(year, [y], method="statistics", grain="month", hours=8784)
    qcompare = parent(p, cities, method="compare", hours=720)
    qpair = parent(p, cities[:2], method="compare", hours=48)
    qdetail = parent(p, [cities[0]], hours=720) | {"page_size": 100}
    shapes = {
        "single_24h": (STAT, q24),
        "detail_limit_100": (DETAIL, qdetail),
        "5_over_30d": (COMPARE, qcompare),
        "annual_months": (STAT, qyear),
        "two_columns_paired": (COMPARE, qpair),
    }
    report = {"machine": machine(), "tiers": {}}
    with loopback(user) as (web, records):
        for tier, city_window, year_window in (
            ("small", range(0, 24), range(0, 720)),
            ("large", range(24, 720), range(720, 8784)),
        ):
            add_rows(op, p, coordinates(codes, ("CO",), city_window))
            add_rows(op, year, coordinates(["201"], ("CO",), year_window))
            analyze_volume()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT product_id,count(*) FROM observations_cityobservation GROUP BY 1 ORDER BY 1"
                )
                counts = dict(cursor.fetchall())
            report["tiers"][tier] = {
                "rows": {"comparison": counts[p.pk], "annual": counts[year.pk]},
                "http": {name: profile(web, records, *args) for name, args in shapes.items()},
            }
        report["product_lock"] = lock_probe(web, records, p, q24)
    evidence("V17", "query-cost.json", report)
    small, large = (report["tiers"][tier]["http"] for tier in ("small", "large"))
    ceilings = dict(
        single_24h=17,
        detail_limit_100=12,
        annual_months=19,
        two_columns_paired=18,
        **{"5_over_30d": 18},
    )
    for name, limit in ceilings.items():
        for tier in (small, large):
            assert tier[name]["warm"]["error_count"] == 0
            assert tier[name]["warm"]["max_sql_count"] <= limit
        # The only extra query at scale can be a paired summary for a sufficient sample.
        assert large[name]["warm"]["max_sql_count"] <= small[name]["warm"]["max_sql_count"] + 2


def test_v12_json_response_budget_is_independent_of_candidate_cells(identities):
    op, user = identities
    p = product("cost-json", "C")
    target = make_cities(op, p, ["101"])[0]
    p.attribution = "合成署名" * 1024
    p.save(update_fields=["attribution"])
    add_rows(op, p, coordinates(["101"], ("CO",), range(100)))
    query = parent(p, [target], hours=100)
    with loopback(user) as (web, records):
        baseline, _, _ = call(web, records, "POST", DETAIL, query | {"page_size": 1})
        response, sample, _ = call(web, records, "POST", DETAIL, query | {"page_size": 100})
    assert baseline.status_code == 200 and len(baseline.json()["results"]) == 1
    assert (response.status_code, response.json()) == (400, {"code": "query_too_large"})
    assert "content-disposition" not in response.headers
    evidence(
        "V12", "json-byte-bound.json", sample | {"candidate_cells": 100, "code": "query_too_large"}
    )


def export_body(web, records, query, *, observe=None):
    response, _, _ = call(web, records, "POST", DETAIL, query | {"page_size": 1}, observe=observe)
    assert response.status_code == 200, response.content[:1024]
    return {
        "kind": "detail",
        "groups": [query | {"input_version": response.json()["input_version"]}],
    }


def assert_rejection(response, code, status):
    assert (response.status_code, response.json()) == (status, {"code": code})
    assert "content-disposition" not in response.headers
    assert response.headers["content-type"].startswith("application/json")


def download_path(ticket):
    return f"{EXPORT}{ticket}/download/?scope=synthetic"


def verify_csv(response, ticket, expected_rows):
    assert response.status_code == 200, response.content[:1024]
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    data = response.content
    parsed = list(csv.DictReader(StringIO(data.decode(), newline="")))
    assert len(parsed) == ticket.row_count == expected_rows
    assert len(data) == ticket.byte_count <= 4 * 1024 * 1024
    assert hashlib.sha256(data).hexdigest() == ticket.data_sha256
    assert len({(r["key"], r["revision"]) for r in parsed}) == expected_rows
    assert {r["input_version"] for r in parsed} == set(ticket.input_versions)


def test_v12_csv_rows_hidden_records_and_new_input(identities, record_property):
    op, user = identities
    p = product("v12-rows", "C")
    cities = make_cities(op, p, ["101", "102", "103"])
    for code in ("101", "102"):
        grant(op, p, "export", object_code=code, fields=export_fields("detail"))
    cells = list(coordinates(["101", "102"], INDICATORS, range(720)))
    add_rows(op, p, cells[:9999])
    query = parent(p, cities[:2], hours=720, indicators=INDICATORS)
    report = {"candidate_cells": len(cells), "responses": {}}

    def observe(phase, size):
        return csv_observer(record_property, phase, size)

    record_property("s07_csv_environment", json.dumps(csv_environment()))
    with loopback(user) as (web, records):
        body = export_body(web, records, query, observe=observe("input_version", 9999))
        previous = None
        for size in (9999, 10000, 10001):
            if previous:
                add_rows(op, p, cells[size - 1 : size])
                changed, sample, _ = call(
                    web, records, "GET", previous, observe=observe("stale", size)
                )
                assert_rejection(changed, "input_changed", 409)
                report["responses"][f"stale_at_{size}"] = sample
                body = export_body(web, records, query, observe=observe("input_version", size))
            count = ExportTicket.objects.count()
            prepared, sample, _ = call(
                web, records, "POST", EXPORT, body, observe=observe("prepare", size)
            )
            report["responses"][f"prepare_{size}"] = sample
            if size == 10001:
                assert_rejection(prepared, "query_too_large", 400)
                assert ExportTicket.objects.count() == count
                break
            assert prepared.status_code == 201, prepared.content
            assert ExportTicket.objects.count() == count + 1
            ticket = ExportTicket.objects.get(pk=prepared.json()["id"])
            previous = download_path(ticket.pk)
            if size == 10000:
                # 24 real current rows raise PG cardinality above the limit but are outside the
                # frozen two-city scope and have no export grant. The existing ticket still works.
                add_rows(op, p, coordinates(["103"], ("CO",), range(24)))
                assert (
                    export_body(web, records, query, observe=observe("hidden_version", size))
                    == body
                )
            downloaded, sample, _ = call(
                web, records, "GET", previous, observe=observe("download", size)
            )
            verify_csv(downloaded, ticket, size)
            assert ticket.format_version == "s05.csv.v2"
            report["responses"][f"download_{size}"] = sample | {
                "row_count": ticket.row_count,
                "format_version": ticket.format_version,
                "f_digest": ticket.data_sha256,
            }

        beyond = parent(p, cities, hours=720, indicators=INDICATORS)
        oversized = body | {"groups": [beyond | {"input_version": "0" * 64}]}
        failed, sample, trace = call(
            web, records, "POST", EXPORT, oversized, observe=observe("candidate_limit", 10001)
        )
        assert_rejection(failed, "query_too_large", 400)
        assert not any("observations_product" in sql for sql, _, _ in trace)
        assert ExportTicket.objects.count() == 2
        report["responses"]["candidate_15120"] = sample
    evidence("V12", "export-row-and-cell-bounds.json", report)


def test_v12_csv_byte_budget_at_low_row_count(identities):
    op, user = identities
    p = product("v12-bytes", "C")
    target = make_cities(op, p, ["101"])[0]
    grant(op, p, "export", fields=export_fields("detail"))
    add_rows(op, p, coordinates(["101"], ("CO",), range(100)))
    with loopback(user) as (web, records):
        payload = export_body(web, records, parent(p, [target], hours=100))
        accepted, _, _ = call(web, records, "POST", EXPORT, payload)
        assert accepted.status_code == 201
        path = download_path(accepted.json()["id"])
        assert call(web, records, "GET", path)[0].status_code == 200
        p.attribution = "项目合成" * 4096
        p.save(update_fields=["attribution"])
        rejected, sample, trace = call(web, records, "POST", EXPORT, payload)
        assert_rejection(rejected, "query_too_large", 400)
        assert any("lineage AS" in sql for sql, _, _ in trace)
        download, download_sample, trace = call(web, records, "GET", path)
        assert_rejection(download, "query_too_large", 400)
        assert any("lineage AS" in sql for sql, _, _ in trace)
        assert 100 * len(p.attribution.encode()) > 4 * 1024 * 1024
        assert ExportTicket.objects.count() == 1
    evidence(
        "V12",
        "csv-byte-bound.json",
        {
            "prepare": sample,
            "download": download_sample,
            "authorized_rows": 100,
            "code": "query_too_large",
        },
    )


def test_v17_detail_csv_cost(identities):
    op, user = identities
    p = product("cost-csv", "C")
    cities = make_cities(op, p, ["101", "102"])
    grant(op, p, "export", fields=export_fields("detail"))
    cells = list(coordinates(["101", "102"], INDICATORS, range(720)))
    query = parent(p, cities, hours=720, indicators=INDICATORS)
    report = {"machine": machine(), "tiers": {}}
    with loopback(user) as (web, records):
        for tier, rows, total in [("small", cells[:280], 280), ("large", cells[280:10000], 10000)]:
            add_rows(op, p, rows)
            analyze_volume()
            payload = export_body(web, records, query)
            prepared, prep, trace = call(web, records, "POST", EXPORT, payload)
            assert prepared.status_code == 201, prepared.content
            ticket = ExportTicket.objects.get(pk=prepared.json()["id"])
            path = download_path(ticket.pk)
            response, first, download_trace = call(web, records, "GET", path)
            verify_csv(response, ticket, total)
            samples = [call(web, records, "GET", path)[1] for _ in range(20)]
            report["tiers"][tier] = {
                "rows": total,
                "prepare": prep,
                "first_download": first,
                "warm_downloads": summarize_samples(samples),
                "prepare_explain": explain(trace),
                "download_explain": explain(download_trace),
            }
    evidence("V17", "csv-query-cost.json", report)
    small, large = (report["tiers"][tier] for tier in ("small", "large"))
    assert small["prepare"]["sql_count"] == large["prepare"]["sql_count"] <= 32
    assert (
        small["warm_downloads"]["max_sql_count"] == large["warm_downloads"]["max_sql_count"] <= 25
    )
    assert large["warm_downloads"]["error_count"] == small["warm_downloads"]["error_count"] == 0
