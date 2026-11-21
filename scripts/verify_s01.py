#!/usr/bin/env python3
"""Run the S01 acceptance matrix on two fresh, task-owned local Compose projects."""

import argparse
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from s01_runtime import AUTH, FIXTURE_POST, OBS, ROOT, RUNS, Client, Project, source_hash, wait_for


def deployment(p):
    p.compose("config", "-q")
    p.up(build=True)
    ids = {
        svc: p.compose("ps", "-a", "-q", svc)
        for svc in ("db", "redis", "migrate", "web", "worker", "beat")
    }
    assert all(len(i.splitlines()) == 1 for i in ids.values())

    def inspect(svc, template):
        return p.cmd(["docker", "inspect", "-f", template, ids[svc]])

    p.equal(
        "V01",
        "migrate exit",
        inspect("migrate", "{{.State.Status}}/{{.State.ExitCode}}"),
        "exited/0",
    )
    for svc in ["db", "redis"]:
        p.equal("V01", svc + " health", inspect(svc, "{{.State.Health.Status}}"), "healthy")
    for svc in ["web", "worker", "beat"]:
        p.equal("V01", svc + " running", inspect(svc, "{{.State.Running}}"), "true")
    p.record("V01", "compose ps -a", p.compose("ps", "-a"))
    p.record("V01", "service images", {svc: inspect(svc, "{{.Image}}") for svc in ids})
    p.equal(
        "V01", "ready HTTP", Client(p.base).call("/health/ready/"), (200, {"web": "ok", "pg": "ok"})
    )
    migrations = p.compose("exec", "-T", "web", "python", "backend/manage.py", "showmigrations")
    assert "[ ]" not in migrations and "[X] 0002_local_grants" in migrations
    p.record("V01", "showmigrations", migrations)
    repeated = p.compose("exec", "-T", "web", "python", "backend/manage.py", "migrate", "--noinput")
    assert "No migrations to apply" in repeated
    p.record("V01", "repeat migrate", repeated)
    registered = p.compose(
        "exec", "-T", "web", "celery", "-A", "config", "inspect", "registered", "--timeout=8"
    )
    assert "samples.process_fixture" in registered and "1 node online" in registered
    p.record("V01", "registered Worker", registered)
    beat = p.compose("logs", "--no-color", "beat")
    assert "DatabaseScheduler" in beat
    p.record(
        "V01", "one Beat scheduler", [line for line in beat.splitlines() if "scheduler ->" in line]
    )
    p.equal("V01", "fresh runs/observations/queued", p.snapshot(), [0, 0, 0])
    p.equal(
        "V01",
        "independent installed F grants",
        p.sql(
            "SELECT string_agg(operation, ',' ORDER BY operation) FROM samples_fixturegrant WHERE enabled"
        ),
        "display,process_local,retain",
    )
    p.equal(
        "V01",
        "app role superuser/createdb",
        p.sql("SELECT rolsuper,rolcreatedb FROM pg_roles WHERE rolname='s01_app'"),
        "f|f",
    )
    p.record(
        "V01",
        "PG / Redis server versions",
        [
            p.sql("SHOW server_version"),
            p.compose("exec", "-T", "redis", "redis-server", "--version"),
        ],
    )


def quality(p):
    # Only this disposable test login can create pytest's separate test_s01 database.
    p.sql(f"CREATE ROLE s01_pytest LOGIN CREATEDB PASSWORD '{p.test_password}'")
    versions = p.compose(
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        "import sys,importlib.metadata as m;print(sys.version); print({p:m.version(p) for p in ['django','djangorestframework','celery','kombu','redis','psycopg','django-celery-beat','pytest','pytest-django','mypy','ruff']})",
    )
    p.record(
        "V11", "locked tool versions", [p.compose("exec", "-T", "web", "uv", "--version"), versions]
    )
    commands = [
        ["uv", "lock", "--check"],
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
        ["mypy"],
        ["python", "backend/manage.py", "check"],
        ["python", "backend/manage.py", "makemigrations", "--check", "--dry-run"],
        ["pytest", "-v"],
    ]
    try:
        for cmd in commands:
            guest = (
                {"POSTGRES_USER": "s01_pytest", "POSTGRES_PASSWORD": p.test_password}
                if cmd[0] == "pytest"
                else {}
            )
            output = p.compose(
                "exec",
                "-T",
                *(arg for k in guest for arg in ("-e", k)),
                "web",
                *cmd,
                env=guest,
                timeout=120,
            )
            p.record("V11", " ".join(cmd), {"exit": 0, "stdout": output})
            if cmd[0] == "pytest":
                for section, fragment in [
                    ("V02", "test_normalization.py"),
                    ("V02", "test_pg_rejects"),
                    ("V07", "test_pg_failure_after_first_insert"),
                    ("V07", "test_conflict_never_replaces"),
                ]:
                    matches = [
                        line
                        for line in output.splitlines()
                        if fragment in line and "PASSED" in line
                    ]
                    assert matches, fragment
                    p.record(section, fragment + " (isolated test_s01)", matches)
    finally:
        p.sql("DROP ROLE s01_pytest")
    p.equal("V02", "acceptance DB still empty", p.snapshot(), [0, 0, 0])


def http_case(p, section, label, response, status, code=None):
    actual, body = response
    p.equal(
        section,
        label + " HTTP",
        [actual, body.get("code") if isinstance(body, dict) else None],
        [status, code],
    )
    if code in {"not_authenticated", "use_denied"}:
        p.equal(section, label + " denial has no data or count", list(body), ["code"])
    return body


def sessions(p):
    c = Client(p.base)
    auth = {"username": "reader", "password": p.password}

    def count_user():
        return int(p.sql("SELECT count(*) FROM auth_user"))

    def session_count():
        return int(p.sql("SELECT count(*) FROM django_session"))

    http_case(p, "V03", "anonymous observation", c.call(OBS), 403, "not_authenticated")
    old_token = c.csrf()
    for label, token, headers, data, status, code in [
        ("register missing CSRF", False, {}, auth, 403, "csrf_failed"),
        ("register wrong CSRF", "A" * 32, {}, auth, 403, "csrf_failed"),
        (
            "register cross-origin",
            True,
            {"Origin": "http://untrusted.invalid"},
            auth,
            403,
            "csrf_failed",
        ),
        (
            "register privilege field",
            True,
            {},
            auth | {"is_superuser": True},
            400,
            "invalid_request",
        ),
    ]:
        before = count_user()
        http_case(p, "V03", label, c.call(AUTH + "register/", data, token, headers), status, code)
        p.equal("V03", label + " PG user delta", count_user() - before, 0)
    body = http_case(p, "V03", "register", c.call(AUTH + "register/", auth), 201)
    p.equal("V03", "registered role", body["role"], "USER")
    p.equal(
        "V03",
        "PG active/staff/superuser/user-perms/groups",
        p.sql(
            "SELECT is_active,is_staff,is_superuser,(SELECT count(*) FROM auth_user_user_permissions),(SELECT count(*) FROM auth_user_groups) FROM auth_user WHERE username='reader'"
        ),
        "t|f|f|0|0",
    )
    for label, token, data, code in [
        ("missing CSRF", False, auth, "csrf_failed"),
        ("wrong CSRF", "A" * 32, auth, "csrf_failed"),
        ("wrong credentials", True, auth | {"password": "wrong"}, "invalid_credentials"),
    ]:
        http_case(p, "V03", "login " + label, c.call(AUTH + "login/", data, token), 403, code)
        p.equal("V03", "no session after " + label, session_count(), 0)
    http_case(p, "V03", "login", c.call(AUTH + "login/", auth), 200)
    cookie = next(k for k in c.cookies if k.name == "sessionid")
    p.equal(
        "V03",
        "local session HttpOnly/SameSite/Secure/explicit expiry",
        [
            cookie.has_nonstandard_attr("HttpOnly"),
            cookie.get_nonstandard_attr("SameSite"),
            cookie.secure,
            cookie.expires is not None,
        ],
        [True, "Lax", False, True],
    )
    replay = Client(p.base)
    replay.cookies.set_cookie(cookie)
    c.csrf()
    for label, token in [
        ("missing CSRF", False),
        ("wrong CSRF", "A" * 32),
        ("pre-login token", old_token),
    ]:
        http_case(
            p, "V03", "logout " + label, c.call(AUTH + "logout/", {}, token), 403, "csrf_failed"
        )
        http_case(p, "V03", "still authenticated " + label, c.call(OBS), 200)
    http_case(p, "V03", "rotated token logout", c.call(AUTH + "logout/", {}), 204)
    p.equal("V03", "server session removed", session_count(), 0)
    http_case(p, "V03", "replayed session", replay.call(OBS), 403, "not_authenticated")
    p.shell(f"""from django.contrib.auth.models import User,Permission
for name in ('operator','staff','disabled','supervisor'):
    u=User.objects.create_user(name,password='{p.password}',is_staff=name=='staff',is_superuser=name=='supervisor',is_active=name!='disabled')
    if name=='operator': u.user_permissions.add(Permission.objects.get(content_type__app_label='samples', codename='run_fixture'))
""")
    http_case(
        p,
        "V03",
        "inactive login",
        c.call(AUTH + "login/", auth | {"username": "disabled"}),
        403,
        "invalid_credentials",
    )
    p.equal("V03", "inactive login session count", session_count(), 0)
    return [Client(p.base).login(name, p.password) for name in ("reader", "operator", "staff")]


def no_side_effect(p, section, label, client, data, status, code, token=True):
    before = p.snapshot()
    http_case(p, section, label, client.call(RUNS, data, token), status, code)
    after = p.snapshot()
    p.record(section, label + " counts before/after (runs, rows, queued)", [before, after])
    assert after == before


def empty_cases(p, reader, operator, staff):
    p.compose("stop", "worker")
    for label, client, token, code in [
        ("USER", reader, True, "permission_denied"),
        ("staff only", staff, True, "permission_denied"),
        ("operator missing CSRF", operator, False, "csrf_failed"),
        ("operator wrong CSRF", operator, "A" * 32, "csrf_failed"),
    ]:
        no_side_effect(p, "V04", label, client, FIXTURE_POST, 403, code, token)
    for op in ("process_local", "retain"):
        p.grant(op, False)
        try:
            no_side_effect(
                p, "V09", "submit without " + op, operator, FIXTURE_POST, 403, "use_denied"
            )
        finally:
            p.grant(op, True)
    for label, payload in [
        ("URL field", FIXTURE_POST | {"url": "http://example.invalid"}),
        ("valid JSON padded to 16385 bytes", json.dumps(FIXTURE_POST).encode().ljust(16385)),
        ("unknown key", FIXTURE_POST | {"fixture_key": "other"}),
    ]:
        no_side_effect(p, "V09", label, operator, payload, 400, "invalid_request")
    for condition, reason in [
        ("role", "permission_denied"),
        ("process_local", "process_local_denied"),
        ("retain", "retain_denied"),
    ]:
        before = p.snapshot()
        assert before[1:] == [0, 0]
        run_id = http_case(
            p,
            "V08",
            condition + " accepted before revocation",
            operator.call(RUNS, FIXTURE_POST),
            202,
        )["run_id"]
        p.equal("V08", condition + " queued", p.snapshot(), [before[0] + 1, 0, 1])
        if condition == "role":
            p.shell(
                "from django.contrib.auth.models import User; User.objects.get(username='operator').user_permissions.clear()"
            )
        else:
            p.grant(condition, False)
        p.compose("start", "worker")
        try:
            row = p.done(run_id, "rejected")
            p.equal(
                "V08",
                condition + " rejected in PG",
                [row["error_code"], row["observation_id"]],
                [reason, None],
            )
            p.record("V08", condition + " consumer", p.receive(run_id, reason))
            p.equal("V08", condition + " after consume", p.snapshot(), [before[0] + 1, 0, 0])
        finally:
            p.compose("stop", "worker")
            if condition == "role":
                p.shell(
                    "from django.contrib.auth.models import User,Permission; User.objects.get(username='operator').user_permissions.add(Permission.objects.get(content_type__app_label='samples',codename='run_fixture'))"
                )
            else:
                p.grant(condition, True)


def worker_failure(p, operator):
    # PostgreSQL sequence changes survive rollback, proving the failing branch ran.
    before = p.snapshot()
    assert before[1:] == [0, 0]
    p.sql("""CREATE SEQUENCE s01_injection_hit;
        GRANT USAGE ON SEQUENCE s01_injection_hit TO s01_app;
        CREATE FUNCTION s01_fail_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.state='succeeded' AND EXISTS (SELECT 1 FROM samples_fixtureobservation WHERE id=NEW.observation_id) THEN
                PERFORM nextval('s01_injection_hit');
                RAISE EXCEPTION 's01_private_error_probe';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER s01_fail_write BEFORE UPDATE OF state ON samples_fixturerun
        FOR EACH ROW EXECUTE FUNCTION s01_fail_write();""")
    try:
        key = http_case(
            p, "V07", "submit for injected PG failure", operator.call(RUNS, FIXTURE_POST), 202
        )["run_id"]
        p.compose("start", "worker")
        row = p.done(key, "failed")
        p.equal(
            "V07",
            "failure after first insert reached in Worker transaction",
            p.sql("SELECT is_called FROM s01_injection_hit"),
            "t",
        )
        p.equal(
            "V07",
            "Worker rolled back row and link",
            [row["error_code"], row["observation_id"], p.snapshot()],
            ["processing_failed", None, [before[0] + 1, 0, 0]],
        )
        p.record("V07", "sanitized Worker failure", p.receive(key, "processing_failed"))

        def reported():
            logs = p.compose("logs", "--no-color", "worker")
            if any(key in line and "ProcessingFailure" in line for line in logs.splitlines()):
                return logs
            return None

        logs = wait_for("Celery failed-task report", reported)
        p.equal(
            "V07", "database diagnostic in Worker log", "s01_private_error_probe" in logs, False
        )
    finally:
        p.compose("stop", "worker")
        p.sql(
            "DROP TRIGGER s01_fail_write ON samples_fixturerun; DROP FUNCTION s01_fail_write(); DROP SEQUENCE s01_injection_hit"
        )


def causal(p, operator):
    before = p.snapshot()
    assert before[1:] == [0, 0]
    run_id = http_case(
        p, "V05", "submit with stopped Worker", operator.call(RUNS, FIXTURE_POST), 202
    )["run_id"]
    row = p.run_row(run_id)
    p.equal(
        "V05",
        "PG pending/sent/observation",
        [row["state"], row["dispatch"], row["observation_id"]],
        ["pending", "sent", None],
    )
    p.equal("V05", "queued with Worker stopped", p.snapshot(), [before[0] + 1, 0, 1])
    p.record("V05", "baseline and submitted row", [before, row])
    p.compose("start", "worker")
    row = p.done(run_id, "succeeded")
    p.record("V05", "worker receipt and finish", p.receive(run_id, "succeeded"))
    p.equal("V05", "running Worker drained queue", p.snapshot(), [before[0] + 1, 1, 0])
    assert row["observation_id"] and row["finished_at"] >= row["started_at"] >= row["requested_at"]
    p.record("V05", "terminal PG row", row)
    return row


# Independent hand calculation. No imports from application code or its fixture loader.
EXPECTED = {
    "fixture_key": "co-hour-v1",
    "fixture_version": "1",
    "product": "F",
    "source": "local.fixture.v1",
    "entity": "F:CITY:001",
    "synthetic": True,
    "purpose": "local_demo",
    "indicator": "CO",
    "raw_value": "1250",
    "raw_unit": "µg/m³",
    "source_time": "2001-02-03T08:00:00+08:00",
    "source_timezone": "Asia/Shanghai",
    "observed_at": "2001-02-03T00:00:00Z",
    "window_start": "2001-02-02T23:00:00Z",
    "window_end": "2001-02-03T00:00:00Z",
    "window_semantics": "previous_hour_right_label_[start,end)",
    "value": "1.250",
    "unit": "mg/m³",
    "quality": "valid",
    "aqi_standard": "not_applicable",
    "published_at": None,
}


def sql_observation(p):
    # row_to_json preserves PG precision. Override the JSON number with numeric::text.
    row = json.loads(
        p.sql(
            "SELECT to_jsonb(o) || jsonb_build_object('value', o.value::text) FROM samples_fixtureobservation o"
        )
    )
    for key in ("observed_at", "window_start", "window_end", "fetched_at"):
        row[key] = datetime.fromisoformat(row[key]).isoformat().replace("+00:00", "Z")
    return row


def reconcile(p, reader, run):
    fixture = json.loads((ROOT / "backend/samples/data/co-hour-v1.json").read_text())
    p.equal(
        "V06",
        "original fixed file",
        {k: v for k, v in fixture.items() if k != "author"},
        {
            k: v
            for k, v in EXPECTED.items()
            if k
            in (
                "fixture_key",
                "fixture_version",
                "product",
                "source",
                "entity",
                "synthetic",
                "purpose",
                "indicator",
                "raw_value",
                "raw_unit",
                "source_time",
                "source_timezone",
                "window_semantics",
            )
        },
    )
    db = sql_observation(p)
    http = http_case(p, "V06", "reader exact time", reader.call(OBS), 200)
    p.equal("V06", "exact count", http["count"], 1)
    assert http["filters"] == {"product": "F", "observed_at": EXPECTED["observed_at"]}
    (item,) = http["results"]
    for label, data in (("SQL vs hand calculation", db), ("HTTP vs hand calculation", item)):
        p.equal("V06", label, {k: data[k] for k in EXPECTED}, EXPECTED)
    assert item["observation_age_seconds"] > 0 and item["freshness_threshold_seconds"] is None
    p.equal("V06", "Worker fetch timestamp HTTP=PG", item["fetched_at"], db["fetched_at"])
    p.equal("V06", "Worker PG link", [db["id"], item["id"]], [run["observation_id"]] * 2)
    p.record("V06", "HTTP response", http)
    missing = http_case(
        p, "V06", "adjacent hour", reader.call(OBS.replace("00:00:00Z", "01:00:00Z")), 200
    )
    p.equal(
        "V06",
        "no substitution",
        [missing["count"], missing["results"], missing["reason"]],
        [0, [], "no_observation_at_time"],
    )
    http_case(p, "V06", "missing offset", reader.call(OBS.removesuffix("Z")), 400, "invalid_time")
    return db


def rebuild(p, db, run):
    def containers():
        return {
            svc: p.compose("ps", "-aq", svc)
            for svc in ("db", "redis", "migrate", "web", "worker", "beat")
        }

    def volume(db_id):
        return p.cmd(
            [
                "docker",
                "inspect",
                "-f",
                '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}',
                db_id,
            ]
        )

    old = containers()
    old_vol = volume(old["db"])
    p.equal("V10", "project-owned PG volume", old_vol, p.name + "_pgdata")
    p.compose("down", "--timeout", "5")
    p.up()
    new = containers()
    assert all(new[k] and new[k] != old[k] for k in old)
    p.record("V10", "old/new containers", [old, new])
    p.equal("V10", "reattached volume", volume(new["db"]), old_vol)
    p.equal("V10", "SQL observation after rebuild", sql_observation(p), db)
    p.equal("V10", "SQL run after rebuild", p.run_row(run["id"]), run)
    reader = Client(p.base).login("reader", p.password)

    def same_row(label):
        (item,) = http_case(p, "V10", label, reader.call(OBS), 200)["results"]
        p.equal("V10", label + " row", {k: item[k] for k in db}, db)

    same_row("fresh login after recreation")
    p.compose("stop", "db")
    try:
        p.equal(
            "V10",
            "PG down ready",
            Client(p.base).call("/health/ready/"),
            (503, {"web": "ok", "pg": "unavailable"}),
        )
    finally:
        p.compose("start", "db")
        p.wait_ready()
    same_row("PG restored")
    return reader, Client(p.base).login("operator", p.password)


def redeliver(p, operator, run):
    run_id = str(uuid.UUID(run["id"]))
    before = p.snapshot()
    offset = len(p.compose("logs", "--no-color", "worker").splitlines())
    p.shell(
        f"from samples.tasks import process_fixture; process_fixture.apply_async(args=['{run_id}'],task_id='{run_id}',retry=False)"
    )
    p.record(
        "V07", "new receipt/duplicate finish after replay", p.receive(run_id, "duplicate", offset)
    )
    p.equal("V07", "counts after duplicate consumed", p.snapshot(), before)
    p.equal("V07", "terminal run unchanged", p.run_row(run_id), run)
    new_id = http_case(p, "V07", "independent request", operator.call(RUNS, FIXTURE_POST), 202)[
        "run_id"
    ]
    row = p.done(new_id, "succeeded")
    p.equal("V07", "new request same observation", row["observation_id"], run["observation_id"])
    p.equal("V07", "only run incremented", p.snapshot(), [before[0] + 1, before[1], 0])


def publication_race(p, operator, original):
    """Hold only producer UPDATE *before* row locking; consumer uses a real free row."""
    lock = 73190
    p.sql(f"""CREATE FUNCTION s01_hold_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_advisory_xact_lock({lock}); RETURN NULL; END $$;
        CREATE TRIGGER s01_pause_producer BEFORE UPDATE OF dispatch ON samples_fixturerun
        FOR EACH STATEMENT EXECUTE FUNCTION s01_hold_dispatch();""")
    try:
        with subprocess.Popen(
            [*p.prefix, "exec", "-T", "db", "psql", "-XqtA", "-U", "postgres", "-d", "s01"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=p.env,
        ) as guard:
            guard.stdin.write(f"SELECT pg_advisory_lock({lock});\n")
            guard.stdin.flush()

            def locks(granted):
                return (
                    p.sql(
                        f"SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid={lock} AND granted={granted}"
                    )
                    == "1"
                )

            with ThreadPoolExecutor(max_workers=1) as thread:
                try:
                    wait_for("test session holds producer gate", lambda: locks("true"))
                    result = thread.submit(operator.call, RUNS, FIXTURE_POST)
                    wait_for("live Web publisher waiting", lambda: locks("false"), seconds=6)

                    def terminal():
                        return p.sql(
                            "SELECT id FROM samples_fixturerun WHERE dispatch='pending' AND state='succeeded'"
                        )

                    key = wait_for("Worker committed before HTTP reply", terminal, seconds=6)
                    before = p.run_row(key)
                    assert (
                        not result.done() and before["observation_id"] == original["observation_id"]
                    )
                    p.record("V07", "running Worker won race (Web UPDATE blocked)", before)
                finally:
                    guard.communicate(input="\\q\n", timeout=5)
                body = http_case(p, "V07", "publisher resumed", result.result(timeout=8), 202)
                p.equal("V07", "same UUID after Web response", body["run_id"], key)
                p.equal(
                    "V07",
                    "only dispatch changed after Worker commit",
                    p.run_row(key),
                    before | {"dispatch": "sent"},
                )
                p.record("V07", "race Worker logs", p.receive(key, "succeeded"))
    finally:
        p.sql(
            "DROP TRIGGER s01_pause_producer ON samples_fixturerun; DROP FUNCTION s01_hold_dispatch()"
        )


def broker_outage(p, reader, operator, run):
    before = p.snapshot()
    worker = p.compose("ps", "-q", "worker")
    offset = len(p.compose("logs", "--no-color", "worker").splitlines())
    p.compose("stop", "redis")
    try:
        start = time.monotonic()
        response = operator.call(RUNS, FIXTURE_POST)
        seconds = time.monotonic() - start
        p.record("V08", "Redis stopped: POST elapsed seconds", round(seconds, 4))
        body = http_case(p, "V08", "Redis unavailable", response, 503, "dispatch_unconfirmed")
        assert seconds < 5, f"publish exceeded 5s: {seconds:.4f}s"
        row = p.run_row(body["run_id"])
        p.equal(
            "V08",
            "unconfirmed PG",
            [row["state"], row["dispatch"], row["observation_id"]],
            ["pending", "unconfirmed", None],
        )
        p.record("V08", "unconfirmed committed request", row)
        (item,) = http_case(p, "V08", "stored F without Redis", reader.call(OBS), 200)["results"]
        p.equal("V08", "unaffected read", item["id"], run["observation_id"])
    finally:
        p.compose("start", "redis")

    def connection_logs():
        lines = p.compose("logs", "--no-color", "worker").splitlines()[offset:]
        return [line for line in lines if "Connected to redis://" in line] or None

    p.record(
        "V08",
        "same Worker reconnect",
        wait_for("Worker reconnect after broker outage", connection_logs, 50),
    )
    p.equal("V08", "same Worker container", p.compose("ps", "-q", "worker"), worker)
    next_id = http_case(p, "V08", "submit after reconnect", operator.call(RUNS, FIXTURE_POST), 202)[
        "run_id"
    ]
    p.record("V08", "real post-reconnect consumption", p.receive(next_id, "succeeded"))
    p.equal("V08", "post outage counts", p.snapshot(), [before[0] + 2, 1, 0])


def read_denials(p, reader, operator):
    privileged = Client(p.base).login("supervisor", p.password)
    for role, client in [("USER", reader), ("operator", operator), ("superuser", privileged)]:
        for product in ["C", "H", "unknown"]:
            http_case(
                p,
                "V09",
                role + " read " + product,
                client.call(OBS.replace("product=F", "product=" + product)),
                403,
                "use_denied",
            )
    p.grant("display", False)
    try:
        for role, client in [("USER", reader), ("operator", operator), ("superuser", privileged)]:
            http_case(p, "V09", role + " display revoked", client.call(OBS), 403, "use_denied")
        p.equal(
            "V09",
            "retain still permits existing row",
            p.sql("SELECT count(*) FROM samples_fixtureobservation"),
            "1",
        )
    finally:
        p.grant("display", True)
    cfg = json.loads(p.compose("config", "--format", "json"))  # Do not persist secrets.
    for svc in ("worker", "beat", "db", "redis"):
        spec = cfg["services"][svc]
        p.equal(
            "V09",
            svc + " networks and host ports",
            [list(spec["networks"]), spec.get("ports", [])],
            [["backend"], []],
        )
    p.equal("V09", "internal backend network", cfg["networks"]["backend"]["internal"], True)
    p.equal("V09", "HTTP loopback bind", cfg["services"]["web"]["ports"][0]["host_ip"], "127.0.0.1")
    tasks = p.sql(
        "SELECT task FROM django_celery_beat_periodictask WHERE enabled ORDER BY task"
    ).splitlines()
    p.record("V09", "enabled periodic tasks", tasks)
    assert set(tasks) <= {"celery.backend_cleanup"}
    http_case(p, "V09", "no admin CRUD route", operator.call("/admin/"), 404)
    routes = p.shell(
        "from django.urls import get_resolver; print([str(p.pattern) for p in get_resolver().url_patterns])"
    )
    p.record("V09", "root URL patterns", routes)


def rate_limits(p, operator):
    p.reset_web_limits()
    operator.base = p.base
    p.compose("stop", "worker")
    c = Client(p.base)
    c.csrf()
    for endpoint, limit, query in [
        ("register", 8, "SELECT count(*) FROM auth_user"),
        ("login", 12, "SELECT count(*) FROM django_session"),
    ]:
        for i in range(limit):
            if endpoint == "login":
                test = Client(p.base)
                test.csrf()
                data = {"username": "reader", "password": p.password}
            else:
                test = c
                data = {"username": f"rate{i}", "password": p.password}
            http_case(
                p,
                "V04",
                f"{endpoint} valid {i + 1}",
                test.call(AUTH + endpoint + "/", data),
                201 if endpoint == "register" else 200,
            )
        before = p.sql(query)
        # A new browser and spoofed forwarding header do not reset the direct-IP limit.
        test = Client(p.base)
        test.csrf()
        data = {
            "username": "rateoverflow" if endpoint == "register" else "reader",
            "password": p.password,
        }
        http_case(
            p,
            "V04",
            endpoint + " over limit",
            test.call(AUTH + endpoint + "/", data, headers={"X-Forwarded-For": "192.0.2.1"}),
            429,
            "rate_limited",
        )
        p.equal("V04", endpoint + " denied: no new user/session", p.sql(query), before)
    for i in range(12):
        http_case(p, "V04", f"trigger {i + 1}", operator.call(RUNS, FIXTURE_POST), 202)
    no_side_effect(p, "V04", "trigger over limit", operator, FIXTURE_POST, 429, "rate_limited")
    p.compose("start", "worker")
    wait_for(
        "authorized test requests all consumed",
        lambda: (
            p.sql(
                "SELECT count(*) FROM samples_fixturerun WHERE state='pending' AND dispatch='sent'"
            )
            == "0"
        ),
    )
    p.equal("V04", "one F after rate-limit exercise", p.snapshot()[1:], [1, 0])


def run_checks(p):
    p.claim()
    p.record(
        "V01",
        "source baseline and digest",
        [p.cmd(["git", "rev-parse", "HEAD"]), source_hash()],
    )
    p.record(
        "V01",
        "Docker Engine / Compose",
        [
            p.cmd(["docker", "version", "--format", "{{.Server.Version}}"]),
            p.cmd(["docker", "compose", "version", "--short"]),
        ],
    )
    print("RUN", p.name, flush=True)
    deployment(p)
    quality(p)
    reader, operator, staff = sessions(p)
    empty_cases(p, reader, operator, staff)
    worker_failure(p, operator)
    run = causal(p, operator)
    db = reconcile(p, reader, run)
    reader, operator = rebuild(p, db, run)
    redeliver(p, operator, run)
    publication_race(p, operator, run)
    broker_outage(p, reader, operator, run)
    read_denials(p, reader, operator)
    rate_limits(p, operator)


def verify():
    p = Project()
    try:
        try:
            run_checks(p)
        finally:
            p.cleanup()
    except BaseException as exc:
        first = f"{exc.__context__} / " if exc.__context__ else ""
        p.receipt("FAIL", first + f"{type(exc).__name__}: {exc}")
        raise
    p.receipt("PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, choices=(1, 2), default=2)
    if not __debug__:
        sys.exit("Assertions must be enabled")
    for _ in range(parser.parse_args().rounds):
        verify()
