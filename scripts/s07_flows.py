"""Exercise the S03 F/I failure scenarios against an explicitly owned S07 Compose project."""

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from traceback import extract_tb

from s01_runtime import wait_for
from s03_checks import (
    backoff_and_pause,
    beat_race,
    city,
    historical_boundary,
    kill_after_commit,
    lease,
    lost_confirmation,
    lost_delivery,
    no_new,
    partial_block,
    permission_change,
    request_scope_and_budget,
)
from s03_runtime import S03Project, prepare, register_job
from s06_guard import require
from s07_compose_runtime import applications


class FlowChecks(S03Project):
    """Adapter for the existing F origin, deterministic barriers and independent SQL oracles."""

    def __init__(self, project, apps):
        # The S07 guard already owns the project lifecycle. Only reuse S03's checks.
        self.project, self.apps = project, apps
        self.base = apps.http
        self.name, self.evidence = project.name, project.root
        self.password = secrets.token_urlsafe(28)
        self.test_password = secrets.token_urlsafe(28)
        self.env = project.values
        self.events = []

    def sql(self, statement):
        p = self.project
        db = p.containers["db"]
        p.owned_container("db", db)
        return p.docker(
            "exec",
            "-i",
            "--env",
            "PGPASSWORD",
            db,
            "psql",
            "-XAtq",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-U",
            p.role,
            "-d",
            p.db,
            stdin=statement,
            extra_env={"PGPASSWORD": p.values["POSTGRES_PASSWORD"]},
        )

    def compose(self, *args, **kwargs):
        p = self.project
        if args == ("up", "-d", "--wait", "redis"):
            cid = p.containers["redis"]
            p.owned_container("redis", cid)
            p.docker("start", cid)
            wait_for(
                "flow broker restart",
                lambda: p.owned_container("redis", cid)["State"]["Health"]["Status"] == "healthy",
            )
            require(
                p.docker("exec", cid, "redis-cli", "DBSIZE") == "0", "restarted_broker_not_empty"
            )
            require(
                p.docker("exec", cid, "redis-cli", "SET", "airquality:s07:instance", p.name, "NX")
                == "OK",
                "restarted_broker_marker",
            )
            return ""
        require(args[0] in {"exec", "logs", "start", "stop"}, "unsupported_flow_command")
        # No commands are supplied by HTTP clients. Every fixture target must still be ours.
        for service in set(args).intersection(self.apps.ids):
            self.apps.check(service, self.apps.ids[service])
        if "redis" in args:
            p.owned_container("redis", p.containers["redis"])
        if args[:3] == ("exec", "-T", "web"):
            # Web's namespace bootstrap starts as root, all application commands use its final UID.
            args = ("exec", "-T", "--user", "10001:10001", *args[2:])
        return p.compose(*args, **kwargs)

    def record(self, section, check, actual):
        # S03 also records full authorized F projections. V08 needs the bounded counters,
        # state, correlation and assertions, never tokens, headers or original payloads.
        def scalar(value):
            return isinstance(value, (int, float, type(None))) or (
                isinstance(value, str) and bool(re.fullmatch(r"[-0-9a-z_:|,. +T]{1,200}", value))
            )

        payload = actual if scalar(actual) else None
        if isinstance(actual, list) and len(actual) <= 20 and all(scalar(x) for x in actual):
            payload = actual
        if isinstance(actual, dict):
            keys = {
                "E",
                "O",
                "V",
                "id",
                "status",
                "error_code",
                "request_state",
                "reserved_at",
                "next_retry_at",
                "dispatch_due",
                "attempt",
                "predecessor_id",
                "generation",
                "deliveries",
                "period_seconds",
                "max_requests",
                "min_interval_seconds",
                "max_concurrent",
            }
            payload = {k: v for k, v in actual.items() if k in keys and scalar(v)}
        if (
            isinstance(actual, (list, tuple))
            and len(actual) == 2
            and type(actual[0]) is int
            and isinstance(actual[1], dict)
        ):
            payload = {
                k: v
                for k, v in actual[1].items()
                if k in {"run_id", "window_id", "dispatch", "code"} and scalar(v)
            } | {"http": actual[0]}
        self.events.append({"case": section, "assertion": check, "observed": payload})


def verify_flows(project):
    result = {"status": "FAIL", "input": ["F", "I"], "run": project.name, "cases": {}}
    p = None
    try:
        with applications(project) as apps:
            apps.start("migrate")
            apps.start("web")
            apps.ready()
            apps.start("s03-origin")
            apps.start("worker")
            apps.start("worker-b")
            apps.start("beat")
            p = FlowChecks(project, apps)
            user_chain(p, apps)
            admin, reader, archivist = prepare(p)
            job = register_job(p, "C")
            for name, fn in (
                ("city", lambda: city(p, admin, reader)),
                ("scheduler_manual_replay", lambda: beat_race(p, admin, job)),
                ("broker_loss", lambda: lost_delivery(p, admin, job)),
                ("unconfirmed_dispatch", lambda: lost_confirmation(p, job)),
                ("committed_before_ack", lambda: kill_after_commit(p, admin, job)),
                ("expired_owner", lambda: lease(p, admin, job)),
                ("killed_before_commit", lambda: partial_block(p, admin)),
                ("changed_partial_batch", lambda: partial_block(p, admin, changed=True)),
                ("latest_only_boundary", lambda: historical_boundary(p, admin)),
                ("rate_limit_and_pause", lambda: backoff_and_pause(p, admin, job)),
                ("withdrawn_rights", lambda: permission_change(p, admin, reader, archivist, job)),
                ("scope_and_quota", lambda: request_scope_and_budget(p, admin, job)),
                ("empty_and_no_new", lambda: no_new(p, admin, job)),
            ):
                result["case"] = name
                p.stop_beat()
                p.release_budget()
                fn()
                result["cases"][name] = "PASS"
                print(f"{p.name}/flows/{name} PASS", flush=True)
            result["independent_sql"] = json.loads(
                project.app_sql("""SELECT json_build_object(
                'windows',(SELECT count(*) FROM collection_workwindow),
                'runs',(SELECT count(*) FROM collection_run),
                'accepted',(SELECT count(*) FROM collection_runitem WHERE accepted),
                'duplicate_accepted',(SELECT count(*) FROM (SELECT window_id,item_key FROM collection_runitem WHERE accepted GROUP BY 1,2 HAVING count(*)>1) d))""")
            )
            require(
                result["independent_sql"]["duplicate_accepted"] == 0, "duplicate_window_acceptance"
            )
        result["status"] = "PASS"
    except Exception as error:
        result["failure"] = {
            "type": type(error).__name__,
            "frames": [
                (Path(f.filename).name, f.lineno) for f in extract_tb(error.__traceback__)[-8:]
            ],
        }
        raise
    finally:
        result["checks"] = p.events if p else []
        result["finished_utc"] = datetime.now(UTC).isoformat()
        (project.root / "flows.json").write_text(json.dumps(result, indent=2) + "\n")


def user_chain(project, apps):
    """Replay the participant's published example through the real Gunicorn listener."""
    from s07_user_flow import exercise

    receipt = exercise(project, apps)
    (project.evidence / "participant.json").write_text(json.dumps(receipt, indent=2) + "\n")
