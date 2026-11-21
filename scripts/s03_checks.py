"""Exercise S03 against real subprocesses and the owned PostgreSQL/Redis project."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from s01_runtime import Client, wait_for
from s03_runtime import (
    JOB_URL,
    RUN_URL,
    city_packet,
    hour,
    list_packet,
    register_job,
    urllib_query,
)
from verify_s01 import http_case


def identification(body):
    return str(uuid.UUID(body["run_id"])), int(body["window_id"])


def projection(p, tag, account, window, expected):
    output = p.window(tag, account, window)
    # Pin the public coverage counts to the independent fixture calculation.
    wanted = {"E": expected[0], "O": expected[1], "V": expected[2]}
    coverage = output.get("coverage", output)
    p.equal(tag, "window E/O/V", {key: coverage[key] for key in wanted}, wanted)


def city(p, account, reader):
    tag = "V03"
    at = hour(-72)
    packet = city_packet(at) | {"AQI": 0, "SO2": "<3", "NO2": None}
    job = register_job(p, "C")
    p.script("C", {"body": packet})
    start = p.count("C")
    run, window = identification(p.post(tag, account, job, at))
    p.equal(tag, "real Worker terminal status", p.settled(run)["status"], "finished")
    p.record(tag, "Worker receipt", p.worker_log(run))
    p.equal(tag, "SQL city key counts", p.accepted_count(window), 7)
    p.equal(
        tag,
        "SQL measured fields and value states",
        p.sql(f"""
        SELECT string_agg(k.indicator || ':' || COALESCE(v.value::text, v.quality) || ':' || v.unit,',' ORDER BY k.indicator)
        FROM observations_cityobservation k JOIN observations_cityrevision v ON v.key_id=k.id
        JOIN observations_receipt r ON r.city_revision_id=v.id
        JOIN collection_run run ON r.batch_id=run.batch_id WHERE run.id='{run}'
    """),
        "AQI:0.000000:1,CO:0.310000:mg/m³,NO2:missing:µg/m³,O3:34.000000:µg/m³,PM10:23.000000:µg/m³,PM2.5:12.000000:µg/m³,SO2:censored:µg/m³",
    )
    p.equal(tag, "HTTP request count", p.count("C") - start, 1)
    event = p.origin("/_events")[-1]
    p.equal(
        tag,
        "POST with exact capitalization and empty body",
        [event["method"], event["keys"], event["target"], event["empty_body"]],
        ["POST", ["cityCode"], ["001"], True],
    )
    city_id = int(p.sql("SELECT id FROM observations_city WHERE geography='f-001'"))
    url = urllib_query(p, "C", at.isoformat(), city=city_id)
    data = http_case(p, tag, "reader exact C AQI", reader.call(url), 200)
    p.equal(
        tag,
        "AQI zero with unknown standard and F flag",
        [data["results"][0][key] for key in ("value", "synthetic")],
        ["0.000000", True],
    )
    return job


def beat_race(p, account, job):
    tag = "V07"
    at = hour()
    p.stop_beat()
    p.script("C", {"status": 503}, {"body": city_packet(at), "gate": "beat-race"})
    start = p.count("C")
    first, window = identification(p.post(tag, account, job, at))
    row = p.settled(first)
    p.equal(
        tag,
        "retryable predecessor",
        [row["request_state"], bool(row["next_retry_at"])],
        ["responded", True],
    )
    p.sql(f"UPDATE collection_job SET next_due_at='{at.isoformat()}' WHERE id={int(job)}")
    p.sql(f"UPDATE collection_run SET next_retry_at=clock_timestamp() WHERE id='{first}'")
    equivalent_job = register_job(p, "C")
    key = str(uuid.uuid4())
    other = Client(p.base).login("collector", p.password)
    with ThreadPoolExecutor(max_workers=3) as pool:
        beat = pool.submit(p.compose, "start", "beat")
        one = pool.submit(p.post, tag, account, equivalent_job, at, request_id=key)
        two = pool.submit(p.post, tag, other, equivalent_job, at, request_id=key)
        p.hit("beat-race")
        p.release("beat-race")
        beat.result()
        id1, window1 = identification(one.result())
        id2, window2 = identification(two.result())
    p.equal(
        tag,
        "concurrent manual replay same UUID and window",
        [id1, window1, window2],
        [id2, window, window],
    )
    wait_for(
        "Beat window trigger",
        lambda: (
            p.sql(
                f"SELECT count(*) FROM collection_run WHERE window_id={window} AND scheduled_for IS NOT NULL"
            )
            != "0"
        ),
        seconds=60,
    )
    p.settled(id1)
    p.equal(tag, "same-window resulting SQL accepted count", p.accepted_count(window), 7)
    p.equal(tag, "one initial 503 plus one actual recovery acquisition", p.count("C") - start, 2)
    p.assert_unique(tag)
    p.record(
        tag,
        "all SQL runs in the exact window",
        p.sql(f"""
        SELECT string_agg(reason||':'||status||':'||request_state,',' ORDER BY created_at) FROM collection_run
        WHERE window_id={window}
    """),
    )
    p.record(
        tag,
        "Beat invokes PG reconciliation",
        [
            line
            for line in p.compose("logs", "--no-color", "beat").splitlines()
            if "collection.reconcile_due" in line
        ][-5:],
    )
    p.stop_beat()
    p.sql(f"UPDATE collection_job SET next_due_at=NULL WHERE id={job}")


def lost_delivery(p, account, job):
    tag = "V08"
    at = hour(-73)
    p.stop_beat()
    p.compose("stop", "-t", "5", "worker", "worker-b", "redis")
    hit_count = p.count("C")
    p.script("C", {"body": city_packet(at)})
    run, window = identification(p.post(tag, account, job, at, allowed=(202, 503)))
    row = p.row(run)
    p.equal(
        tag,
        "PG survives unavailable broker without reserving HTTP",
        [row["status"], row["request_state"], row["reserved_at"]],
        ["pending", "unsent", None],
    )
    p.record(tag, "broker outage dispatch state", row["dispatch"])
    p.compose("up", "-d", "--wait", "redis")
    p.sql(f"UPDATE collection_run SET dispatch_due=clock_timestamp() WHERE id='{run}'")
    p.reconcile()
    p.equal(tag, "Redis has message, PG still unsent", p.row(run)["request_state"], "unsent")
    p.equal(
        tag,
        "exactly one queued delivery before injecting loss",
        p.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s03.collection"),
        "1",
    )
    p.compose("exec", "-T", "redis", "redis-cli", "DEL", "s03.collection")
    p.sql(f"UPDATE collection_run SET dispatch_due=clock_timestamp() WHERE id='{run}'")
    p.reconcile()
    p.delivery(run)
    p.compose("start", "worker", "worker-b")
    p.equal(
        tag,
        "same UUID resolves after missing message and redelivery",
        p.settled(run)["status"],
        "finished",
    )
    p.record(tag, "actual Celery deliveries", p.worker_log(run))
    p.delivery(run)
    wait_for("duplicate message consumption", lambda: len(p.worker_log(run)) >= 2)
    p.equal(tag, "broker replay does not acquire again", p.count("C") - hit_count, 1)
    p.equal(
        tag,
        "one PG acquisition reservation and seven acceptances",
        [p.row(run)["request_state"], p.accepted_count(window)],
        ["responded", 7],
    )
    p.assert_unique(tag)


def lost_confirmation(p, job):
    tag = "V08"
    at = hour(-170)
    count = p.count("C")
    p.script("C", {"body": city_packet(at)})
    p.compose("stop", "-t", "3", "worker", "worker-b")
    try:
        # The real queue receives the message before the publisher sees an error.
        p.shell(f"""
from collection import services
from collection.tasks import execute_run
from kombu.exceptions import OperationalError
send = execute_run.apply_async
def fail_after_send(*args, **kwargs):
    send(*args, **kwargs)
    raise OperationalError('injected after real Redis publish')
execute_run.apply_async = fail_after_send
services.submit({int(p.sql("SELECT id FROM auth_user WHERE username='collector'"))},
                dict(job={job}, target={at.isoformat()!r}, reason='manual', request_id={str(uuid.uuid4())!r}))
""")
        run = p.sql(
            f"SELECT r.id FROM collection_run r JOIN collection_workwindow w ON w.id=r.window_id WHERE r.job_id={job} AND w.target_start='{at.isoformat()}'"
        )
        row = p.row(run)
        p.equal(
            tag,
            "real Redis publish with lost acknowledgement",
            [
                row["dispatch"],
                row["request_state"],
                p.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s03.collection"),
            ],
            ["unconfirmed", "unsent", "1"],
        )
        p.sql(f"UPDATE collection_run SET dispatch_due=clock_timestamp() WHERE id='{run}'")
        p.reconcile()
        p.equal(
            tag,
            "broker retains both copies of the same pending Run",
            p.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s03.collection"),
            "2",
        )
    finally:
        p.compose("start", "worker", "worker-b")
    p.equal(tag, "only one real acquisition and reservation", p.settled(run)["status"], "finished")
    wait_for("two copies received", lambda: len(p.worker_log(run)) >= 2)
    p.equal(
        tag,
        "one response and first acceptance per cell",
        [p.count("C") - count, p.accepted_count(row["window_id"])],
        [1, 7],
    )


def kill_after_commit(p, account, job):
    tag = "V10"
    at = hour(-171)
    count = p.count("C")
    p.script("C", {"body": city_packet(at)})
    p.compose("stop", "-t", "3", "worker")
    try:
        p.compose("exec", "-T", "worker-b", "sh", "-ec", "echo next > /tmp/s03-after-task")
        run, window = identification(p.post(tag, account, job, at))
        marker = wait_for(
            "prefork child stopped after PG commit",
            lambda: p.compose(
                "exec", "-T", "worker-b", "sh", "-c", "cat /tmp/s03-before-ack 2>/dev/null || true"
            ),
        )
        pid, stopped_id = marker.split()
        assert stopped_id == run
        row = p.row(run)
        closure = p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}")
        p.equal(
            tag,
            "commit precedes Redis ACK",
            [
                row["status"],
                bool(closure),
                p.accepted_count(window),
                p.compose("exec", "-T", "redis", "redis-cli", "HLEN", "unacked"),
            ],
            ["finished", True, 7, "1"],
        )
        p.compose("exec", "-T", "worker-b", "sh", "-ec", 'kill -KILL "$1"', "sh", str(int(pid)))
        wait_for(
            "same UUID redelivered from broker after worker child death",
            lambda: len(p.worker_log(run)) >= 2,
        )
        p.equal(
            tag,
            "ACK-loss redelivery preserves closure, Batch and accepted cells",
            [
                p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}"),
                p.row(run)["batch_id"],
                p.receipt_count(run),
                p.accepted_count(window),
                p.count("C") - count,
            ],
            [closure, row["batch_id"], 7, 7, 1],
        )
    finally:
        p.compose(
            "exec",
            "-T",
            "worker-b",
            "sh",
            "-c",
            'if [ -f /tmp/s03-before-ack ]; then read pid id < /tmp/s03-before-ack; kill -CONT "$pid" 2>/dev/null || true; fi; rm -f /tmp/s03-after-task /tmp/s03-before-ack',
        )
        p.compose("start", "worker")


def lease(p, account, job):
    tag = "V09"
    at = hour(-74)
    p.script(
        "C",
        {"gate": "expired-owner", "body": city_packet(at, aqi=52)},
        {"body": city_packet(at, aqi=53)},
    )
    recorded = p.count("C")
    p.compose("stop", "-t", "3", "worker")
    old_pid = None
    try:
        run, window = identification(p.post(tag, account, job, at))
        p.record(tag, "old owner reached HTTP", p.hit("expired-owner"))
        old_pid, task_id = p.compose(
            "exec", "-T", "worker-b", "cat", "/tmp/s03-active-task"
        ).split()
        assert task_id == run
        p.compose("exec", "-T", "worker-b", "sh", "-ec", 'kill -STOP "$1"', "sh", str(int(old_pid)))
        before = p.row(run)
        p.equal(
            tag,
            "old request has no response and budget is charged",
            [before["status"], before["request_state"], bool(before["reserved_at"])],
            ["running", "possible", True],
        )
        p.sql(
            f"UPDATE collection_workwindow SET lease_until=clock_timestamp()-interval '1 second' WHERE id={window}"
        )
        p.reconcile()
        p.equal(
            tag,
            "PG retires old owner and schedules a linked attempt",
            p.row(run)["status"],
            "expired",
        )
        # Keep the old request charged. Advance only its occupancy deadline and retry clock.
        p.sql(f"""UPDATE collection_run SET next_retry_at=clock_timestamp(),
                 occupied_until=clock_timestamp()-interval '1 second' WHERE id='{run}' AND status='expired'""")
        p.compose("start", "worker")
        p.reconcile()
        linked = p.successor(run)
        newer = p.settled(linked)
        p.equal(
            tag,
            "successor commits before old owner resumes",
            [
                newer["generation"] > before["generation"],
                newer["owner"] != before["owner"],
                newer["status"],
                newer["request_state"],
                bool(newer["batch_id"]),
            ],
            [True, True, "finished", "responded", True],
        )
        first_cells = p.accepted_count(window)
        closed = p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}")
        p.release("expired-owner")
        p.compose("exec", "-T", "worker-b", "sh", "-ec", 'kill -CONT "$1"', "sh", str(int(old_pid)))
        old_pid = None
        wait_for(
            "expired child resumes after the successor commits",
            lambda: any(
                f"collection.execute_run[{run}] succeeded" in line
                for line in p.compose("logs", "--no-color", "worker-b").splitlines()
            ),
        )
        p.equal(
            tag,
            "late predecessor cannot add a receipt or rewrite the closure",
            [
                first_cells,
                p.accepted_count(window),
                p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}") == closed,
                p.receipt_count(run),
            ],
            [7, 7, True, 0],
        )
        p.delivery(run)
        wait_for("old redelivery observed", lambda: len(p.worker_log(run)) >= 2)
        p.equal(
            tag,
            "separate attempts remain charged after replay",
            [p.count("C") - recorded, p.row(run)["reserved_at"], p.row(run)["request_state"]],
            [2, before["reserved_at"], "possible"],
        )
        p.record(tag, "independent old/new attempt rows", [p.row(run), p.row(linked)])
    finally:
        if old_pid is not None:
            p.release("expired-owner")
            p.compose(
                "exec",
                "-T",
                "worker-b",
                "sh",
                "-c",
                'kill -CONT "$1" || true',
                "sh",
                str(int(old_pid)),
            )
        p.compose("start", "worker")


def inject_second_block(p, product, *, crash=False):
    # Pause after the first INSERT of the second transaction. The worker child dies
    # before it can commit or handle the response. Other scenarios exercise a PG error.
    fault = "PERFORM pg_sleep(12);" if crash else "RAISE EXCEPTION 's03_verify_block_failure';"
    p.sql(f"""
        CREATE FUNCTION s03_verify_block_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.batch_id IN (SELECT id FROM observations_batch WHERE product_id={int(product)})
             AND coalesce((SELECT r.manifest_count FROM collection_run r WHERE r.batch_id=NEW.batch_id),0)>=0
             AND (SELECT count(*) FROM observations_receipt WHERE batch_id=NEW.batch_id) > 128
          THEN {fault} END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER s03_verify_block_failure AFTER INSERT ON observations_receipt
          FOR EACH ROW EXECUTE FUNCTION s03_verify_block_failure();
    """)


def partial_block(p, account, *, changed=False, switched=False):
    tag = "V14" if switched else "V10"
    at = hour(-75 - changed - (2 if switched else 0))
    job = register_job(p, "L", expected=[f"{pos:03d}" for pos in range(1, 201)])
    product = int(p.sql("SELECT id FROM observations_product WHERE code='L'"))
    first_packet = list_packet(at, 200)
    recovered = list_packet(
        at + timedelta(hours=1) if switched else at, 200, aqi=53 if changed else 43
    )
    start = p.count("L")
    p.script("L", {"body": first_packet}, {"body": recovered})
    crash = not changed and not switched
    if crash:
        p.compose("stop", "-t", "3", "worker")
    inject_second_block(p, product, crash=crash)
    try:
        first, window = identification(p.post(tag, account, job, at))
        if crash:
            pg_wait = "SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND wait_event='PgSleep' AND query LIKE 'INSERT INTO %observations_receipt%'"
            backend_pid = wait_for(
                "second PG transaction paused in trigger", lambda: p.sql(pg_wait), seconds=90
            )
            p.equal(
                tag,
                "SQL sees only committed receipts during the paused second block",
                p.receipt_count(first),
                128,
            )
            worker_pid, task_id = p.compose(
                "exec", "-T", "worker-b", "cat", "/tmp/s03-active-task"
            ).split()
            assert task_id == first
            p.compose(
                "exec", "-T", "worker-b", "sh", "-ec", 'kill -KILL "$1"', "sh", str(int(worker_pid))
            )
            wait_for(
                "dead child's PostgreSQL connection closed",
                lambda: (
                    p.sql(
                        f"SELECT NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid={int(backend_pid)})"
                    )
                    == "t"
                ),
                seconds=30,
            )
            wait_for("broker redelivers killed child's UUID", lambda: len(p.worker_log(first)) >= 2)
            p.equal(
                tag,
                "broker redelivery after worker death leaves first 128 receipts and HTTP count intact",
                [p.receipt_count(first), p.accepted_count(window), p.count("L") - start],
                [128, 128, 1],
            )
            p.sql(
                f"UPDATE collection_workwindow SET lease_until=clock_timestamp()-interval '1 second' WHERE id={window}"
            )
            p.reconcile()
            p.equal(
                tag,
                "scanner expires the terminated Worker and keeps the reservation",
                [p.row(first)["status"], bool(p.row(first)["reserved_at"])],
                ["expired", True],
            )
        failed = p.settled(first)
        p.equal(
            tag,
            "first completed block remains and failing receipt rolled back",
            [p.receipt_count(first), p.accepted_count(window)],
            [128, 128],
        )
        p.equal(
            tag,
            "after receiving an HTTP response the original attempt carries one batch",
            [failed["request_state"], bool(failed["batch_id"])],
            ["responded", True],
        )
    finally:
        p.sql(
            "DROP TRIGGER s03_verify_block_failure ON observations_receipt; DROP FUNCTION s03_verify_block_failure()"
        )
        if crash:
            p.compose("start", "worker")
    p.sql(
        f"UPDATE collection_run SET next_retry_at=clock_timestamp() WHERE id='{first}' AND next_retry_at IS NOT NULL"
    )
    p.reconcile()
    retry = p.successor(first)
    result = p.settled(retry)
    p.equal(
        tag,
        "new HTTP attempt preserves original count and has its own lineage",
        [
            p.receipt_count(first),
            p.count("L") - start,
            result["request_state"],
            result["batch_id"] is not None and result["batch_id"] != failed["batch_id"],
            result["predecessor_id"],
            p.receipt_count(retry),
        ],
        [128, 2, "responded", True, first, 0 if switched else 1400],
    )
    p.equal(
        tag,
        "accepted count for exact/changed/advance response",
        p.accepted_count(window),
        128 if switched else 1400,
    )
    p.delivery(first)
    p.delivery(retry)
    wait_for(
        "both old real deliveries logged",
        lambda: len(p.worker_log(first)) >= 2 and len(p.worker_log(retry)) >= 2,
    )
    p.equal(
        tag,
        "broker redelivery after user-visible commit cannot acquire again",
        p.count("L") - start,
        2,
    )
    p.assert_unique(tag)
    disputed = 0
    if not switched:
        disputed = int(
            p.sql(f"""SELECT count(*) FROM observations_cityobservation k
            WHERE product_id={product} AND k.disputed AND observed_at_utc='{at.isoformat()}'""")
        )
        p.record(tag, "same versus changed response SQL disputed count", disputed)
        assert bool(disputed) == changed
    projection(
        p,
        tag,
        account,
        window,
        (1400, 128 if switched else 1400, 128 if switched else 1400 - disputed),
    )


def no_new(p, account, job):
    tag = "V11"
    target = hour(-80)
    list_job = register_job(p, "L")
    start = p.count("L")
    p.script("L", {"body": []}, {"body": list_packet(target, 1)})
    empty, window = identification(p.post(tag, account, list_job, target))
    row = p.settled(empty)
    p.equal(
        tag,
        "empty collection is a fetched, legal no-new result",
        [row["request_state"], p.accepted_count(window)],
        ["responded", 0],
    )
    p.equal(
        tag,
        "empty window result",
        p.sql(f"SELECT result FROM collection_workwindow WHERE id={window}"),
        "no_new",
    )
    older, old_window = identification(p.post(tag, account, list_job, target + timedelta(hours=1)))
    p.settled(older)
    p.equal(
        tag,
        "old publication does not fill the new target",
        [p.accepted_count(old_window), p.count("L") - start],
        [0, 2],
    )
    # The same legal array envelope can carry no attributable data or a mix.
    roster_job = register_job(p, "L", expected=["001"])
    for days, result, received in [(1, "quality_only", 0), (2, "partial", 7)]:
        at = target - timedelta(days=days)
        packet = list_packet(at, 1) if received else []
        p.script(
            "L", {"body": [*packet, {"CityCode": "0", "Area": "F", "TimePoint": at.isoformat()}]}
        )
        run, window = identification(p.post(tag, account, roster_job, at))
        final = p.settled(run)
        codes = p.sql(
            f"SELECT code FROM observations_qualityissue WHERE batch_id={int(final['batch_id'])}"
        )
        p.equal(
            tag,
            "F02 Worker result and batch quality evidence",
            [final["result"], codes],
            [result, "invalid_city_code"],
        )
        projection(p, tag, account, window, (7, received, received))
    p.script("C", {"raw": ""})
    invalid, invalid_window = identification(p.post(tag, account, job, target))
    p.equal(
        tag, "true empty bytes are a format error", p.settled(invalid)["error_code"], "invalid_body"
    )
    p.equal(tag, "empty bytes accept nothing", p.accepted_count(invalid_window), 0)
    p.equal(
        tag,
        "Web remains ready during source failure",
        account.call("/health/ready/"),
        (200, {"web": "ok", "pg": "ok"}),
    )


def backoff_and_pause(p, account, job):
    tag = "V12"
    at = hour(-81)
    p.compose("start", "beat")
    start = p.count("C")
    p.script("C", {"status": 503}, {"status": 503}, {"status": 503})
    first, window = identification(p.post(tag, account, job, at))
    first_row = p.settled(first)
    second = p.successor(first)
    second_row = p.settled(second)
    third = p.successor(second)
    final = p.settled(third)
    p.equal(
        tag,
        "three attempts maximum for persistent temporary errors",
        [
            final["attempt"],
            final["error_code"],
            final["next_retry_at"],
            p.count("C") - start,
            p.accepted_count(window),
        ],
        [3, "upstream_transient", None, 3, 0],
    )
    for preceding, following in ((first_row, second_row), (second_row, final)):
        delta = datetime.fromisoformat(following["reserved_at"]) - datetime.fromisoformat(
            preceding["reserved_at"]
        )
        p.record(tag, "observed reservation interval seconds", delta.total_seconds())
        assert delta >= timedelta(seconds=2 ** preceding["attempt"])
    p.stop_beat()
    p.script("C", {"status": 429, "headers": {"Retry-After": "4"}})
    limited, _ = identification(p.post(tag, account, job, at + timedelta(hours=1)))
    p.equal(tag, "upstream 429 pauses", p.settled(limited)["error_code"], "upstream_rate_limited")
    p.record(
        tag,
        "shared source pause and retry-after PG",
        p.sql(
            "SELECT collection_pause, collection_resume_after FROM observations_source WHERE namespace='s03.fixture'"
        ),
    )
    p.post(tag, account, job, at + timedelta(hours=2), allowed=(400, 403, 409))
    p.equal(tag, "no HTTP while source pause applies", p.count("C") - start, 4)
    wait_for(
        "Retry-After",
        lambda: (
            p.sql(
                "SELECT collection_resume_after <= clock_timestamp() FROM observations_source WHERE namespace='s03.fixture'"
            )
            == "t"
        ),
    )
    resumed = account.call(f"{JOB_URL}{job}/", {"action": "resume_source"})
    p.record(tag, "controlled job resume HTTP", resumed)
    assert resumed[0] in {200, 202, 204}
    p.equal(
        tag,
        "resume identity recorded",
        p.sql(
            f"SELECT changed_by_id=(SELECT id FROM auth_user WHERE username='collector') FROM collection_job WHERE id={job}"
        ),
        "t",
    )


def permission_change(p, account, reader, archivist, job):
    tag = "V15"
    at = hour(-86)
    count = p.count("C")
    data = {
        "job": job,
        "target": at.isoformat(),
        "request_id": str(uuid.uuid4()),
        "reason": "manual",
    }
    http_case(p, tag, "missing CSRF", account.call(RUN_URL, data, token=False), 403, "csrf_failed")
    for disallowed in (reader, archivist):
        status, body = disallowed.call(RUN_URL, data)
        p.record(tag, "account without run_collection HTTP", [status, body])
        assert status == 403
    p.equal(tag, "refused requests did not reach provider", p.count("C"), count)
    p.compose("stop", "-t", "3", "worker", "worker-b")
    acquire = p.sql(
        """SELECT id FROM observations_usegrant WHERE product_id=(SELECT id FROM observations_product WHERE code='C') AND operation='acquire_online' AND revoked_at IS NULL"""
    )
    assert acquire.isdigit()
    run, window = identification(p.post(tag, account, job, at))
    p.sql("UPDATE auth_user SET is_active=false WHERE username='collector'")
    try:
        p.compose("start", "worker", "worker-b")
        row = p.settled(run)
        p.equal(
            tag,
            "queued account withdrawal before sending leaves reservation empty",
            [
                row["request_state"],
                row["reserved_at"],
                p.accepted_count(window),
                p.count("C") - count,
            ],
            ["unsent", None, 0, 0],
        )
    finally:
        p.sql("UPDATE auth_user SET is_active=true WHERE username='collector'")
    p.script("C", {"gate": "rights-withdrawn", "body": city_packet(at)})
    arrived, window2 = identification(p.post(tag, account, job, at))
    p.hit("rights-withdrawn")
    p.shell(f"""
from django.contrib.auth.models import User
from observations.policy import revoke_grant
revoke_grant(User.objects.get(username='collector').pk,{int(acquire)},'synthetic withdrawal while in flight')
""")
    try:
        p.release("rights-withdrawn")
        result = p.settled(arrived)
        p.equal(
            tag,
            "no writes from response returned after acquire withdrawal",
            [p.accepted_count(window2), p.receipt_count(arrived), p.count("C") - count],
            [0, 0, 1],
        )
        p.record(
            tag,
            "late response after rights change",
            [result["status"], result["error_code"], result["request_state"]],
        )
        assert result["status"] in ("failed", "paused")
        if result["request_state"] == "possible":
            assert result["reserved_at"] and result["occupied_until"]
            # Move past the conservative in-flight deadline for the next independent scenario.
            p.sql(
                f"UPDATE collection_run SET occupied_until=clock_timestamp() WHERE id='{arrived}'"
            )
    finally:
        # Restore authority for independent scenarios in this disposable dataset only.
        p.sql(
            f"UPDATE observations_usegrant SET revoked_at=NULL,revoked_by_id=NULL,revoke_reason='' WHERE id={int(acquire)}"
        )


def request_scope_and_budget(p, account, job):
    tag = "V16"
    at = hour(-90)
    list_job = register_job(p, "L")
    site_job = register_job(p, "S")
    trend_job = register_job(p, "T")
    starting = {shape: p.count(shape) for shape in "LCST"}
    for shape, target, replacement in (
        ("L", list_job, "geography='f-001'"),
        ("S", site_job, "object_code='101'"),
        (
            "T",
            trend_job,
            f"data_from='{at.date().isoformat()}',data_until='{(at.date() + timedelta(days=1)).isoformat()}'",
        ),
    ):
        selector = f"WHERE operation='acquire_online' AND product_id=(SELECT id FROM observations_product WHERE code='{shape}')"
        p.sql(f"UPDATE observations_usegrant SET {replacement} {selector}")
        try:
            # UTC midnight always makes the 24-label selection straddle two dates.
            target_at = at.replace(hour=0) if shape == "T" else at
            code, body = account.call(
                RUN_URL,
                {
                    "job": target,
                    "target": target_at.isoformat(),
                    "request_id": str(uuid.uuid4()),
                    "reason": "manual",
                },
            )
            p.record(tag, f"narrow {shape} acquisition rights", [code, body])
            assert code == 403
            p.equal(tag, f"narrow {shape} rights HTTP hits", p.count(shape), starting[shape])
        finally:
            p.sql(
                f"UPDATE observations_usegrant SET geography='*',object_code='*',data_from='0001-01-01',data_until='9999-12-31' {selector}"
            )
    source = "WHERE namespace='s03.fixture'"
    previous_limits = json.loads(
        p.sql(f"SELECT collection_limits FROM observations_source {source}")
    )
    limits = previous_limits | {"max_requests": 1, "period_seconds": 60, "min_interval_seconds": 0}
    reservations = f"""
        SELECT count(*) FROM collection_run r JOIN collection_job j ON j.id=r.job_id
        JOIN observations_product p ON p.id=j.product_id
        WHERE p.source_id=(SELECT id FROM observations_source {source})
          AND r.reserved_at IS NOT NULL
    """
    wait_for(
        "prior scenarios' reservations to age out naturally",
        lambda: (
            p.sql(reservations + " AND r.reserved_at > clock_timestamp()-interval '60 seconds'")
            == "0"
        ),
        seconds=75,
    )
    reservation_baseline = int(p.sql(reservations))
    event_baseline = len(p.origin("/_events"))
    p.sql(f"UPDATE observations_source SET collection_limits='{json.dumps(limits)}' {source}")
    p.equal(
        tag,
        "F01 actual source limits from PostgreSQL",
        json.loads(p.sql(f"SELECT collection_limits FROM observations_source {source}")),
        limits,
    )

    def receipt(row):
        return {
            field: row[field]
            for field in (
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
            )
        }

    try:
        p.script("C", {"gate": "source-occupancy", "body": city_packet(at)})
        p.script("L", {"body": list_packet(at, 1)})
        c_run, c_window = identification(p.post(tag, account, job, at))
        try:
            p.hit("source-occupancy")
            l_run, l_window = identification(p.post(tag, account, list_job, at))
            blocked = wait_for(
                "competing product's budget decision",
                lambda: row if (row := p.row(l_run))["error_code"] else None,
                seconds=12,
            )
            in_flight = p.row(c_run)
        finally:
            p.release("source-occupancy")
        p.record(tag, "F01 waiting Run in PostgreSQL", receipt(blocked))
        p.equal(
            tag,
            "F01 competing Run waits unreserved while first Worker holds the source slot",
            [
                blocked["status"],
                blocked["error_code"],
                blocked["request_state"],
                blocked["reserved_at"],
                blocked["attempt"],
                blocked["predecessor_id"],
                blocked["dispatch_due"],
                in_flight["status"],
                in_flight["request_state"],
                p.count("L") - starting["L"],
            ],
            [
                "pending",
                "budget_exhausted",
                "unsent",
                None,
                1,
                None,
                None,
                "running",
                "possible",
                0,
            ],
        )
        first_at = datetime.fromisoformat(in_flight["reserved_at"])
        due_at = datetime.fromisoformat(blocked["next_retry_at"])
        p.equal(
            tag,
            "F01 earliest recheck from reservation in seconds",
            (due_at - first_at).total_seconds(),
            60.0,
        )
        completed = p.settled(c_run)
        p.equal(
            tag,
            "released city response completes once and writes seven cells",
            [
                completed["status"],
                completed["request_state"],
                p.accepted_count(c_window),
                p.count("C") - starting["C"],
            ],
            ["finished", "responded", 7, 1],
        )
        blocked_worker = p.worker_log(l_run)
        owning_worker = p.worker_log(c_run)
        p.record(tag, "worker receipt for blocked competing product", blocked_worker)
        p.record(tag, "worker receipt for budget-owning product", owning_worker)
        p.equal(
            tag,
            "source-budget contenders consumed by two distinct Workers",
            [
                len(blocked_worker),
                len(owning_worker),
                len({line.split("|", 1)[0].strip() for line in blocked_worker + owning_worker}),
            ],
            [1, 1, 2],
        )

        # Compose can re-align service labels between calls. Bound the log query
        # by PG time so older successes cannot stand in for scans during this wait.
        scan_start = datetime.fromisoformat(p.sql("SELECT clock_timestamp()")).isoformat()
        p.compose("start", "beat")

        def new_scans():
            logs = p.compose("logs", "--no-color", "--since", scan_start, "worker", "worker-b")
            found = [
                line
                for line in logs.splitlines()
                if "Task collection.reconcile_due[" in line and "] succeeded in" in line
            ]
            return found if len(found) >= 2 else None

        scans = wait_for("two completed Beat reconciliation tasks", new_scans, seconds=35)
        p.record(tag, "F01 completed Beat reconciliations while budget is unavailable", scans)
        p.equal(
            tag,
            "F01 repeated scans preserve original wait without more HTTP or reservations",
            [
                p.sql(f"SELECT clock_timestamp() < '{due_at.isoformat()}'::timestamptz"),
                receipt(p.row(l_run)),
                int(p.sql(reservations)) - reservation_baseline,
                p.count("L") - starting["L"],
            ],
            ["t", receipt(blocked), 1, 0],
        )

        restored = p.settled(l_run)
        p.record(tag, "F01 recovered Run in PostgreSQL", receipt(restored))
        deliveries = p.worker_log(l_run)
        p.record(tag, "F01 Worker receives original UUID again", deliveries)
        p.equal(tag, "F01 Worker receipts for the original UUID", len(deliveries), 2)
        p.record(
            tag,
            "F01 Beat dispatches reconciliation tasks",
            [
                line
                for line in p.compose("logs", "--no-color", "beat").splitlines()
                if "Scheduler: Sending due task s03.reconcile (collection.reconcile_due)" in line
            ][-5:],
        )
        after = p.origin("/_events")[event_baseline:]
        p.record(tag, "F01 internal source HTTP receipts", after)
        p.equal(
            tag,
            "F01 Beat recovers same UUID, attempt and one reservation per request",
            [
                restored["id"],
                restored["status"],
                restored["request_state"],
                restored["attempt"],
                restored["predecessor_id"],
                restored["next_retry_at"],
                p.sql(f"SELECT count(*) FROM collection_run WHERE window_id={l_window}"),
                p.accepted_count(l_window),
                int(p.sql(reservations)) - reservation_baseline,
                [event["shape"] for event in after],
            ],
            [l_run, "finished", "responded", 1, None, None, "1", 7, 2, ["C", "L"]],
        )
        recovered_at = datetime.fromisoformat(restored["reserved_at"])
        p.record(
            tag,
            "F01 actual PG reservation interval seconds",
            (recovered_at - first_at).total_seconds(),
        )
        assert recovered_at >= due_at
    finally:
        p.stop_beat()
        p.sql(
            f"UPDATE observations_source SET collection_limits='{json.dumps(previous_limits)}' {source}"
        )


def historical_boundary(p, account):
    tag = "V14"
    at = hour(-100)
    job = register_job(p, "C", profile="f-latest-v1")
    start = p.count("C")
    run, window = identification(p.post(tag, account, job, at, reason="backfill"))
    row = p.settled(run)
    p.equal(
        tag,
        "latest-only profile cannot retrieve explicit history",
        [row["error_code"], row["request_state"], p.count("C") - start, p.accepted_count(window)],
        ["backfill_unavailable", "unsent", 0, 0],
    )


def closed_review(p, account, reader, job):
    tag = "V18"
    at = hour(-105)
    p.script(
        "C", {"body": city_packet(at)}, {"body": city_packet(at)}, {"body": city_packet(at, aqi=53)}
    )
    count = p.count("C")
    first, window = identification(p.post(tag, account, job, at))
    p.settled(first)
    closed = p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}")
    assert closed
    duplicate, _ = identification(p.post(tag, account, job, at))
    p.equal(
        tag, "ordinary repeat coalesces without HTTP", p.settled(duplicate)["status"], "coalesced"
    )
    p.equal(tag, "ordinary repeat source count", p.count("C") - count, 1)
    p.post(tag, reader, job, at, reason="review", allowed=(403,))
    for value in range(2):
        review, actual_window = identification(p.post(tag, account, job, at, reason="review"))
        result = p.settled(review)
        p.equal(
            tag,
            "authorized independent review uses same window",
            [actual_window, result["request_state"], p.accepted_count(window)],
            [window, "responded", 7],
        )
        expected_disputed = "f" if value == 0 else "t"
        p.equal(
            tag,
            "current AQI dispute state",
            p.sql(
                f"SELECT disputed FROM observations_cityobservation WHERE product_id=(SELECT id FROM observations_product WHERE code='C') AND indicator='AQI' AND observed_at_utc='{at.isoformat()}'"
            ),
            expected_disputed,
        )
    p.equal(
        tag,
        "initial closure timestamp remains frozen",
        p.sql(f"SELECT closed_at FROM collection_workwindow WHERE id={window}"),
        closed,
    )
    p.equal(
        tag, "three real acquisitions across all five trigger attempts", p.count("C") - count, 3
    )
    p.assert_unique(tag)
    projection(p, tag, account, window, (7, 7, 6))
