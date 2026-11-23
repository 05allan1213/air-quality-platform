"""Shared, real HTTP/PG/Redis assertions for the V14 offline process matrix."""

import json
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit

from s01_runtime import FIXTURE_POST, RUNS, Client, wait_for
from s06_guard import IsolationError, require
from s06_runtime import HEALTH, READY
from s07_offline_seed import USER, VALUES
from s07_startup_checks import http


@contextmanager
def report_at(path, run, entry):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    report = {
        "scope": "V14",
        "entry": entry,
        "run": run,
        "origin": "F/I",
        "status": "FAIL",
        "H": {"status": "BLOCKED", "execution": "NOT RUN", "reason": "authorization_missing"},
        "timeline": [],
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        yield report
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error) if isinstance(error, IsolationError) else type(error).__name__
        if getattr(error, "sqlstate", None):
            report["sqlstate"] = error.sqlstate
        report["frames"] = [
            [frame.filename.rsplit("/", 1)[-1], frame.lineno, frame.name]
            for frame in traceback.extract_tb(error.__traceback__)[-12:]
        ]
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(report, indent=2) + "\n")


class OfflineChecks:
    def __init__(self, runtime, report, password):
        self.rt = runtime
        self.report = report
        self.password = password
        self.data = None

    def note(self, event, **observations):
        self.report["timeline"].append(
            {"event": event, "at": datetime.now(UTC).isoformat(), **observations}
        )

    def login(self):
        return Client(self.rt.base).login(USER, self.password)

    def health(self, client):
        client.base = self.rt.base
        status, body = client.call(HEALTH)
        require(status == 200, "v14_ops_health_http")
        return body["components"]

    def states(self, client, states, *, after=None, since=None):
        def match():
            rows = self.health(client)
            if not all(rows[name]["state"] == state for name, state in states.items()):
                return None
            if after and any(rows[name]["observed_at"] == stamp for name, stamp in after.items()):
                return None
            if since and any(
                not rows[name]["observed_at"]
                or datetime.fromisoformat(rows[name]["observed_at"]) < since
                for name in states
            ):
                return None
            return {name: rows[name] for name in states}

        return wait_for("V14 service states", match, seconds=40)

    def session_id(self, client):
        keys = [cookie.value for cookie in client.cookies if cookie.name == "sessionid"]
        require(len(keys) == 1, "v14_session_cookie_missing")
        return keys[0]

    def query(self, client):
        client.base = self.rt.base
        status, who = client.call("/api/v1/me/")
        require(status == 200 and who["username"] == USER, "v14_session")
        query = urlencode(
            {
                "scope": "synthetic",
                "product": self.data["product"],
                "as_of": self.data["end"],
            }
        )
        status, city = client.call(f"/api/v1/cities/{self.data['city']}/?{query}")
        require(status == 200 and city["synthetic"], "v14_city_panel")
        paths = [
            path
            for path in city["links"]["history"]
            if path.startswith("/api/v1/observations/query/?")
            and parse_qs(urlsplit(path).query).get("window_definition")
            == ["hourly_label_unspecified"]
        ]
        require(len(paths) == 1, "v14_history_link")
        status, body = client.call(paths[0])
        require(status == 200, "v14_history_http")
        rows = body["results"]
        require(
            len(rows) == len(VALUES)
            and all(
                row["product"] == self.data["product"]
                and row["indicator"] == "CO"
                and row["synthetic"]
                and row["unit"] == "mg/m³"
                for row in rows
            ),
            "v14_history_rows",
        )
        independent = self.rt.sql("""
            SELECT json_build_object('cells', count(*), 'min', min(r.value), 'max', max(r.value),
                                     'mean', avg(r.value))::text
            FROM observations_cityobservation k
            JOIN observations_cityrevision r ON r.key_id=k.id AND r.is_current
        """)
        values = [Decimal(row["value"]) for row in rows]
        require(
            sorted(values) == list(VALUES)
            and independent == {"cells": 3, "min": 1, "max": 6, "mean": 3}
            and sum(values) / len(values) == independent["mean"],
            "v14_sql_values",
        )
        return independent

    def statistics(self, client, sql):
        client.base = self.rt.base
        status, result = client.call(
            "/api/v1/analysis/statistics/",
            {
                "method": "statistics",
                "grain": "day",
                "selection": {
                    "product": self.data["product"],
                    "source": self.data["source"],
                    "scope": "synthetic",
                    "report_kind": "hourly",
                    "window_definition": "hourly_label_unspecified",
                    "cities": [self.data["city"]],
                    "indicators": ["CO"],
                    "units": {"CO": "mg/m³"},
                    "standard_series": {"CO": "not_applicable"},
                    "start": self.data["start"],
                    "end": self.data["end"],
                },
            },
        )
        require(status == 200 and len(result["buckets"]) == 1, "v14_statistics_http")
        row = result["buckets"][0]
        require(
            row["E"] == row["O"] == row["V"] == sql["cells"]
            and (row["minimum"], row["maximum"], row["mean"])
            == (sql["min"], sql["max"], sql["mean"]),
            "v14_statistics_sql",
        )
        chart = result["chart"]
        points = chart["points"]
        start = datetime.fromisoformat(self.data["start"])
        require(
            chart["resolution"] == "exact"
            and len(points) == sql["cells"]
            and all(
                point["pos"] == 0
                and point["expected"] is True
                and point["key_id"] is not None
                and point["value"] == value
                and datetime.fromisoformat(point["at"]) == start + timedelta(hours=ix)
                for ix, (point, value) in enumerate(zip(points, VALUES, strict=True))
            ),
            "v14_chart_points",
        )
        return {k: row[k] for k in ("E", "O", "V", "minimum", "maximum", "mean")} | {
            "chart_points": [{k: point[k] for k in ("at", "value")} for point in points]
        }

    def ready(self, status):
        expect = {200: b'"ok"', 503: b'"unavailable"'}[status]

        def check():
            result, _, body = http(READY, self.rt.base)
            return result == status and expect in body

        require(wait_for("V14 Web readiness", check, seconds=25), "v14_http_ready")

    def db_stamp(self):
        return self.rt.sql("SELECT to_json(pg_postmaster_start_time())::text")

    def lease(self):
        target = self.data["window"]
        require(type(target) is int and target > 0, "v14_window_id")
        return self.rt.sql(f"""
            SELECT json_build_object('status', r.status, 'error', r.error_code,
                'released', w.owner IS NULL AND w.lease_until IS NULL,
                'retry_ordered', r.next_retry_at IS NOT NULL OR EXISTS (
                    SELECT 1 FROM collection_run s WHERE s.predecessor_id=r.id AND s.reason='retry'
                ),
                'all_source_requests', (SELECT count(*) FROM collection_run WHERE request_state <> 'unsent'),
                'batches', (SELECT count(*) FROM collection_run WHERE batch_id IS NOT NULL))::text
            FROM collection_run r JOIN collection_workwindow w ON w.id=r.window_id
            WHERE w.id={target} AND r.predecessor_id IS NULL
        """)

    def submit(self, client):
        client.base = self.rt.base
        status, data = client.call(RUNS, FIXTURE_POST)
        require(status == 202, "v14_enqueue_http")
        from uuid import UUID

        return str(UUID(data["run_id"]))

    def queued(self, client):
        run = self.submit(client)
        require(self.rt.queued_id(run), "v14_actual_message")
        require(self.fixture(run) == ["pending", "sent"], "v14_pending_sql")
        self.note("worker_stopped_real_queued_task", redis_task=run, postgres=self.fixture(run))
        return run

    def fixture(self, run):
        return self.rt.sql(
            f"SELECT json_build_array(state,dispatch)::text FROM samples_fixturerun WHERE id='{run}'"
        )

    def settled(self, run):
        def terminal():
            row = self.fixture(run)
            return row if row != ["pending", "sent"] else None

        state = wait_for("V14 Worker consumed fixture", terminal, seconds=20)
        require(state == ["succeeded", "sent"], "v14_worker_fixture_recovery")
        return state

    def run(self):
        rt = self.rt
        self.data = rt.seed()
        self.report["fixture"] = self.data
        rt.all_start()
        current = self.login()
        initial = self.states(current, {"redis": "ok", "worker": "ok", "beat": "ok"})
        original_session_id = self.session_id(current)
        volume, first_pg, first_redis = rt.volume(), self.db_stamp(), rt.broker_id()
        require(self.lease()["status"] == "running", "v14_lease_injection_before_cold")
        self.note(
            "initial",
            volume=volume,
            pg_started=first_pg,
            redis_id=first_redis,
            statistics=self.query(current),
            network=rt.network(),
        )

        rt.all_stop()
        rt.stop("redis")
        rt.stop("pg")
        self.note("all_stopped", components=rt.stopped(), volume=rt.volume())
        require(rt.volume() == volume, "v14_volume_changed_offline")
        # Dependency order matters. Redis is live, so PG is the isolated prerequisite.
        rt.start("redis")
        self.note("worker_beat_started_before_pg", **rt.reject_applications())
        rt.stop("redis")
        rt.start("pg")
        require(self.db_stamp() != first_pg and rt.volume() == volume, "v14_pg_cold_identity")
        rt.start("web")
        self.ready(200)
        self.note(
            "web_pg_before_redis",
            redis=self.states(current, {"redis": "unavailable"}),
            statistics=self.query(current),
        )
        rt.stop("web")
        rt.start("redis")
        require(rt.broker_id() != first_redis, "v14_redis_did_not_restart")
        self.note("injected_expired_lease", **rt.expire())
        rt.all_start()
        self.ready(200)
        self.states(
            current,
            {"redis": "ok", "worker": "ok", "beat": "ok"},
            after={name: initial[name]["observed_at"] for name in ("worker", "beat")},
        )
        released = self.lease()
        require(
            released
            == {
                "status": "expired",
                "error": "lease_expired",
                "released": True,
                "retry_ordered": True,
                "all_source_requests": 0,
                "batches": 0,
            },
            "v14_lease_recovery",
        )
        original_session = self.query(current)
        new_client = self.login()
        require(
            self.session_id(current) == original_session_id != self.session_id(new_client),
            "v14_session_replaced_or_reused",
        )
        self.note(
            "same_volume_cold",
            volume=rt.volume(),
            pg_started=self.db_stamp(),
            redis_id=rt.broker_id(),
            old_session=original_session,
            distinct_sessions_verified=True,
            new_session=self.query(new_client),
            research_statistics=self.statistics(new_client, original_session),
            lease=released,
            network=rt.network(),
        )

        # Switch to standalone application components for independent stop/start checks.
        rt.all_stop()
        for name in ("web", "worker", "beat"):
            rt.start(name)
        self.ready(200)
        self.states(current, {"redis": "ok", "worker": "ok", "beat": "ok"})

        rt.stop("web")
        require(http(READY, rt.base)[0] is None, "v14_web_still_accepting_http")
        self.note("web_stopped_pg_readable", pg_started=self.db_stamp(), redis_id=rt.broker_id())
        rt.start("web")
        self.ready(200)
        self.note("web_recovered", statistics=self.query(current))

        for name, peer in (("worker", "beat"), ("beat", "worker")):
            rt.stop(name)
            pending = self.queued(current) if name == "worker" else None
            rows = self.states(current, {name: "unknown", peer: "ok", "pg": "ok"})
            old = rows[name]
            require(old["reason_code"] == "stale_evidence", "v14_pulse_no_expiry")
            self.note(name + "_stale", components=rows)
            rt.start(name)
            fresh = self.states(current, {name: "ok"}, after={name: old["observed_at"]})
            self.note(name + "_recovered", components=fresh)
            if pending:
                self.note(
                    "worker_consumed_same_task", redis_task=pending, postgres=self.settled(pending)
                )

        rt.stop("redis")
        self.ready(200)
        self.note(
            "redis_stopped",
            components=self.states(current, {"redis": "unavailable", "pg": "ok"}),
            statistics=self.query(current),
        )
        redis_restart = datetime.now(UTC)
        rt.start("redis")
        rt.recover_applications()
        self.note(
            "redis_recovered",
            redis_id=rt.broker_id(),
            components=self.states(
                current, {"redis": "ok", "worker": "ok", "beat": "ok"}, since=redis_restart
            ),
        )

        pg_before_outage = self.db_stamp()
        rt.stop("pg")
        self.ready(503)
        self.note("pg_stopped", readiness=503, redis_id=rt.broker_id(), beat=rt.beat_lost())
        pg_restart = datetime.now(UTC)
        rt.start("pg")
        self.ready(200)
        rt.recover_applications()
        require(
            rt.volume() == volume and self.db_stamp() != pg_before_outage, "v14_pg_outage_volume"
        )
        final = self.lease()
        require(final["all_source_requests"] == final["batches"] == 0, "v14_source_access")
        recovered_task = self.submit(current)
        self.note(
            "post_recovery_task", redis_task=recovered_task, postgres=self.settled(recovered_task)
        )
        self.note(
            "pg_recovered",
            readiness=200,
            pg_started=self.db_stamp(),
            components=self.states(
                current, {"redis": "ok", "worker": "ok", "beat": "ok"}, since=pg_restart
            ),
            statistics=self.query(current),
            network=rt.network(),
            lease=final,
        )
