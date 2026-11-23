"""Dedicated V13 diagnostics outside the HTTP measurement window."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from time import monotonic_ns, sleep
from unittest.mock import patch

from s07_performance import API, cases_for
from s07_performance_oracle import check
from s07_performance_seed import USERS, guard, setup_django

PG_SETTINGS = (
    "work_mem",
    "shared_buffers",
    "max_connections",
    "autovacuum",
    "jit",
    "temp_file_limit",
    "random_page_cost",
    "effective_cache_size",
    "checkpoint_timeout",
    "max_wal_size",
)
PLAN_FIELDS = {
    "Node Type",
    "Parent Relationship",
    "Relation Name",
    "Index Name",
    "Join Type",
    "Plan Rows",
    "Actual Rows",
    "Actual Loops",
    "Actual Total Time",
    "Rows Removed by Filter",
    "Sort Method",
    "Sort Space Type",
    "Sort Space Used",
    "Shared Hit Blocks",
    "Shared Read Blocks",
    "Temp Read Blocks",
    "Temp Written Blocks",
}


def runtime():
    from django.conf import settings
    from django.db import connection
    from redis import Redis

    run = guard()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT name, setting, unit FROM pg_settings WHERE name=ANY(%s) ORDER BY name",
            (list(PG_SETTINGS),),
        )
        pg_settings = [list(row) for row in cur]
        cur.execute("""SELECT state, wait_event_type, wait_event, count(*),
            max(extract(epoch FROM (clock_timestamp() - query_start))) FILTER (WHERE state='active')
            FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
            GROUP BY 1,2,3 ORDER BY 1,2,3""")
        activity = [list(row) for row in cur]
        cur.execute("""SELECT l.locktype,l.mode,l.granted,count(*),
                max(extract(epoch FROM (clock_timestamp() - l.waitstart)))
                FROM pg_locks l JOIN pg_stat_activity a USING(pid)
                WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
                GROUP BY 1,2,3 ORDER BY 1,2,3""")
        locks = [list(row) for row in cur]
        cur.execute("""SELECT numbackends, temp_files, temp_bytes, deadlocks, blks_read, blks_hit,
            xact_commit, xact_rollback, stats_reset FROM pg_stat_database WHERE datname=current_database()""")
        database = dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))
        cur.execute("""SELECT checkpoints_timed, checkpoints_req, buffers_checkpoint,
            buffers_backend, buffers_clean FROM pg_stat_bgwriter""")
        checkpoints = dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))
        cur.execute("""SELECT relname, autovacuum_count, autoanalyze_count, last_autovacuum, last_autoanalyze
            FROM pg_stat_user_tables WHERE relname LIKE 'observations_%%' ORDER BY relname""")
        vacuums = [list(row) for row in cur]
        cur.execute("SELECT pg_current_wal_lsn()")
        wal = str(cur.fetchone()[0])
        cur.execute("SELECT status, count(*) FROM collection_run GROUP BY status ORDER BY status")
        run_status = dict(cur.fetchall())
        cur.execute("""SELECT component, extract(epoch FROM (clock_timestamp() - observed_at))
            FROM accounts_servicepulse WHERE component IN ('beat','worker') ORDER BY component""")
        pulses = {component: float(age) for component, age in cur.fetchall()}
    queues = ("s01.fixture", "s02.history", "s03.collection")
    broker_url = settings.CELERY_BROKER_URL.replace("redis+socket://", "unix://", 1)
    with Redis.from_url(broker_url, socket_timeout=2, socket_connect_timeout=2) as redis:
        backlog = {queue: redis.llen(queue) for queue in queues}
    return {
        "run": run,
        "observed_utc": datetime.now(UTC).isoformat(),
        "django": {
            "conn_max_age_s": connection.settings_dict["CONN_MAX_AGE"],
            "conn_health_checks": connection.settings_dict["CONN_HEALTH_CHECKS"],
            "source_mode": settings.CNEMC_MODE,
            "beat_schedule_s": {
                name: task["schedule"] for name, task in settings.CELERY_BEAT_SCHEDULE.items()
            },
        },
        "pg_settings": pg_settings,
        "pg_activity": activity,
        "pg_locks": locks,
        "pg_database": database,
        "pg_checkpoints": checkpoints,
        "pg_autovacuum": vacuums,
        "pg_wal_lsn": wal,
        "pg_collection_run_status": run_status,
        "redis_queue_depths": backlog,
        "service_pulse_age_s": pulses,
    }


def monitor():
    """Observe waits during a dedicated concurrent diagnostic batch."""
    start = monotonic_ns()
    observations = []
    for tick in range(40):
        due = start + tick * 250_000_000
        sleep(max(0, due - monotonic_ns()) / 1e9)
        observations.append(runtime())
    return {"interval_ms": 250, "samples": observations}


def sanitize_plan(node):
    trimmed = {key: value for key, value in node.items() if key in PLAN_FIELDS}
    if "Plans" in node:
        trimmed["Plans"] = [sanitize_plan(child) for child in node["Plans"]]
    return trimmed


def diagnose(manifest):
    from django.db import connection, transaction
    from django.test import Client
    from rest_framework.renderers import JSONRenderer

    run = guard()
    password = os.environ.get("S07_PERF_PASSWORD", "")
    check(run == manifest["run"] and len(password) >= 20, "diagnostic_fixture_identity")
    web = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1")
    csrf = web.get(API + "auth/csrf/").json()["csrf_token"]
    signed_in = web.post(
        API + "auth/login/",
        {"username": USERS[0], "password": password},
        content_type="application/json",
        headers={"X-CSRFToken": csrf},
    )
    check(signed_in.status_code == 200, "diagnostic_login")
    csrf = web.get(API + "auth/csrf/").json()["csrf_token"]
    headers = {"X-CSRFToken": csrf}
    check(web.get(API + "me/").json()["username"] == USERS[0], "diagnostic_owner")
    render = JSONRenderer.render
    output = {}
    for shape, case in cases_for(manifest).items():
        queries = []
        render_ns = 0

        def executed(execute, sql, params, many, context, queries=queries):
            started = monotonic_ns()
            try:
                return execute(sql, params, many, context)
            finally:
                queries.append((sql, params, monotonic_ns() - started))

        def rendered(*args, **kwargs):
            nonlocal render_ns
            started = monotonic_ns()
            try:
                return render(*args, **kwargs)
            finally:
                render_ns += monotonic_ns() - started

        with connection.execute_wrapper(executed), patch.object(JSONRenderer, "render", rendered):
            started = monotonic_ns()
            if case.payload is None:
                response = web.get(case.endpoint, case.params)
            else:
                response = web.post(
                    case.endpoint, case.payload, content_type="application/json", headers=headers
                )
            elapsed = monotonic_ns() - started
        check(response.status_code == 200, "diagnostic_status")
        plans = []
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            for ordinal, (sql, params, ns) in enumerate(queries, 1):
                actual = sql.lstrip()
                if not (
                    actual.startswith("WITH cols AS")
                    or actual.startswith('SELECT "observations_cityrevision".')
                    and "ORDER BY" in actual
                ):
                    continue
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
                plan = cursor.fetchone()[0][0]
                plans.append(
                    {
                        "ordinal": ordinal,
                        "initial_execute_ms": ns / 1e6,
                        "planning_ms": plan["Planning Time"],
                        "execution_ms": plan["Execution Time"],
                        "nodes": sanitize_plan(plan["Plan"]),
                    }
                )
        check(plans, "diagnostic_plan_missing")
        sql_ns = sum(query[2] for query in queries)
        output[shape] = {
            "sql_count": len(queries),
            "database_execute_ms": sql_ns / 1e6,
            "django_request_ms": elapsed / 1e6,
            "render_ms": render_ns / 1e6,
            "non_sql_and_render_ms": (elapsed - sql_ns - render_ns) / 1e6,
            "response_bytes": len(response.content),
            "plans": plans,
        }
    return {
        "run": run,
        "method": "equivalent in-process Django Session with SQL and render timing",
        "results": output,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("runtime", "monitor", "explain"))
    args = parser.parse_args()
    setup_django()
    if args.action == "explain":
        payload = diagnose(json.load(sys.stdin))
    else:
        payload = {"runtime": runtime, "monitor": monitor}[args.action]()
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
