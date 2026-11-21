#!/usr/bin/env python3
"""Verify S03 product and disposition contracts with owned Compose resources (F+I)."""

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

from s01_runtime import source_hash, wait_for
from s03_checks import identification
from s03_runtime import (
    JOB_URL,
    WINDOW_URL,
    S03Project,
    city_packet,
    hour,
    list_packet,
    prepare,
    register_job,
    urllib_query,
)
from verify_s01 import http_case

LIST_MARKER = "F-S03-LIST-9376"
REJECTED_MARKER = "F-S03-REJECTED-1582"
DISPOSITION_MARKER = "F-S03-RETAIN-2849"


def collect(p, tag, account, shape, job, target, script, expected_status="finished"):
    p.script(shape, script)
    count = p.count(shape)
    result = p.post(tag, account, job, target)
    run, window = identification(result)
    row = p.settled(run)
    p.equal(tag, "terminal Run state", row["status"], expected_status)
    p.equal(tag, "exactly one HTTP request for this Run", p.count(shape) - count, 1)
    p.record(tag, "Worker receipt", p.worker_log(run))
    event = [event for event in p.origin("/_events") if event["shape"] == shape][-1]
    return run, window, row, event


def coverage(p, tag, account, window, eov):
    result = p.window(tag, account, window)
    totals = result["coverage"]
    p.equal(tag, "window E/O/V", [totals[key] for key in ("E", "O", "V")], list(eov))
    return result


def marker_absent(p, tag, marker):
    stored = p.compose(
        "exec",
        "-T",
        "db",
        "sh",
        "-euc",
        """set -o pipefail
pg_dump --data-only -U postgres -d "$POSTGRES_DB" |
    awk -v marker="$1" 'index($0,marker) {found=1} END {print found ? "found" : "absent"}'""",
        "scan",
        marker,
    )
    queued = p.compose(
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        """import base64
import json
import sys
from redis import Redis

marker = sys.argv[1].encode()
broker = Redis(host="redis")
messages = broker.lrange("s03.collection", 0, -1)
messages.extend(value for _, value in broker.hscan_iter("unacked"))
for raw in messages:
    payload = json.loads(raw)
    envelope = payload[0] if isinstance(payload, list) else payload
    if marker in raw or marker in base64.b64decode(envelope["body"], validate=True):
        raise SystemExit("synthetic marker in Redis delivery")
print("absent")
""",
        marker,
    )
    p.equal(
        tag,
        "marker absent in PostgreSQL logical data and Redis deliveries",
        [stored, queued],
        ["absent", "absent"],
    )
    p.equal(
        tag,
        "body marker absent from application logs",
        marker in p.compose("logs", "--no-color", "web", "beat", "worker", "worker-b"),
        False,
    )


def nationwide(p, account, _reader, _archivist):
    tag = "V02"
    target = hour(-110)
    valid, second, old, outside = list_packet(target, 4)
    valid.update(Quality=LIST_MARKER, AqiLevel=1, PrimaryPollutant="SO2")
    second["NO2Level"] = 7
    del second["SO2Level"]
    old["TimePoint"] = (target - timedelta(hours=1)).isoformat()
    outside["Quality"] = "F-S03-OUTSIDE"
    zero = valid | {"CityCode": 0}
    job = register_job(p, "L", expected=["001", "002"])
    run, window, _, event = collect(
        p, tag, account, "L", job, target, {"body": [valid, second, old, outside, zero, valid]}
    )
    p.equal(
        tag, "national HTTP method and parameters", [event["method"], event["keys"]], ["GET", []]
    )
    coverage(p, tag, account, window, (14, 13, 12))
    p.equal(
        tag,
        "PG retains only the two target cities and distinct levels",
        p.sql(f"""
        SELECT count(DISTINCT k.city_id),count(DISTINCT k.observed_at_utc),count(DISTINCT k.indicator),
               count(DISTINCT k.id) FILTER (WHERE r.unit='grade'),
               bool_and(k.indicator='AQI' OR k.indicator LIKE '%Level')
        FROM observations_cityobservation k JOIN observations_cityrevision r ON r.key_id=k.id
        WHERE k.product_id=(SELECT product_id FROM collection_workwindow WHERE id={window})
    """),
        "2|1|7|10|t",
    )
    p.equal(
        tag, "duplicate listed row keeps one accepted item per field", p.accepted_count(window), 12
    )
    p.equal(
        tag,
        "authorized source descriptions keep separate receipt fields",
        p.sql(f"""
        SELECT string_agg(DISTINCT r.collection_metadata->>'source_description',','),
               string_agg(DISTINCT r.collection_metadata->>'source_grade',','),
               string_agg(DISTINCT r.collection_metadata->>'primary_pollutant',',')
        FROM observations_receipt r WHERE r.batch_id=(SELECT batch_id FROM collection_run WHERE id='{run}')
    """),
        f"{LIST_MARKER}|1|SO2",
    )
    p.equal(
        tag,
        "out-of-target, outside-roster and zero-city issues keep fixed codes",
        p.sql(f"""
        SELECT string_agg(code,',' ORDER BY code) FROM observations_qualityissue
        WHERE batch_id=(SELECT batch_id FROM collection_run WHERE id='{run}') AND city_key_id IS NULL
    """),
        "invalid_city_code,outside_roster,outside_target",
    )
    p.assert_unique(tag)


def stations(p, account, reader, _archivist):
    tag = "V04"
    target = hour(-109)
    moved = target + timedelta(hours=1)
    p.shell(f"""
from datetime import datetime
from observations.catalog import relocate
from observations.models import StationIdentifier, City
station = StationIdentifier.objects.get(source__namespace='s03.fixture', code='101').station
relocate(station, datetime.fromisoformat({moved.isoformat()!r}),
    city=City.objects.get(source=station.source, geography='f-001'), name='Relocated Fixture Station',
    location_evidence='Synthetic new location', certainty='confirmed')
""")
    a = city_packet(target) | {
        "StationCode": "101",
        "PositionName": "Fixture Station",
        "CityCode": 0,
    }
    b = a | {"StationCode": "102"}
    hourly_absent = a | {"O3_8h": 300}
    del hourly_absent["O3"]
    job = register_job(p, "S")
    _, first_window, _, event = collect(
        p,
        tag,
        account,
        "S",
        job,
        target,
        {
            "body": [
                hourly_absent,
                b,
                b | {"TimePoint": moved.isoformat()},
                a | {"CityCode": "002"},
            ]
        },
    )
    p.equal(
        tag,
        "request uses canonical city name",
        [event["method"], event["keys"], event["target"]],
        ["GET", ["cityName"], ["Fixture 001"]],
    )
    coverage(p, tag, account, first_window, (14, 13, 13))
    _, second_window, _, _ = collect(
        p,
        tag,
        account,
        "S",
        job,
        moved,
        {
            "body": [
                city_packet(moved)
                | {"StationCode": "101", "PositionName": "Relocated Fixture Station"},
                b | {"TimePoint": moved.isoformat()},
            ]
        },
    )
    coverage(p, tag, account, second_window, (14, 14, 14))
    p.equal(
        tag,
        "raw station code kept and epoch boundary respected",
        p.sql(f"""
        SELECT count(DISTINCT k.site_epoch_id),count(DISTINCT k.observed_at_utc),count(DISTINCT k.object_code)
        FROM observations_stationobservation k
        JOIN observations_stationrevision v ON v.key_id=k.id
        JOIN observations_receipt r ON r.station_revision_id=v.id
        JOIN collection_runitem i ON i.id=r.collection_item_id WHERE i.window_id IN ({first_window},{second_window})
    """),
        "3|2|2",
    )
    epoch = p.sql("""SELECT e.id FROM observations_siteepoch e JOIN observations_stationidentifier i
        ON i.station_id=e.station_id WHERE i.code='101' ORDER BY lower(e.active_period) LIMIT 1""")
    query = urllib_query(
        p, "S", target.isoformat(), entity="site_epoch", indicator="O3", city=epoch
    )
    data = http_case(
        p, tag, "old station's hourly O3 has no rolling fallback", reader.call(query), 200
    )
    p.equal(tag, "no substituted rolling value", data["count"], 0)


def trend(p, account, reader, _archivist):
    tag = "V05"
    target = hour(-106)
    earlier = target - timedelta(hours=1)
    previous_day = (target - timedelta(hours=23)).date()
    row = city_packet(target)
    stamp = f"/Date({int(target.timestamp()) * 1000})/"
    ljob, cjob, tjob = [register_job(p, shape) for shape in "LCT"]
    collect(p, tag, account, "L", ljob, target, {"body": list_packet(target, 1, aqi=30)})
    collect(p, tag, account, "C", cjob, target, {"body": row | {"AQI": 40}})
    _, window, _, event = collect(
        p,
        tag,
        account,
        "T",
        tjob,
        target,
        {
            "body": [
                row | {"TimePoint": stamp, "TimePointStr": "11-21 10:00", "AQI": 50},
                row | {"AQI": 50},
                row | {"AQI": 51},
                city_packet(earlier) | {"AQI": 52},
                row | {"TimePoint": previous_day.isoformat(), "TimePointStr": "11-20 10:00"},
                city_packet(target - timedelta(hours=24)),
            ]
        },
    )
    totals = coverage(p, tag, account, window, (None, 2, 1))["coverage"]
    p.equal(tag, "unknown cadence gives no full-window ratio", totals["valid_ratio"], None)
    p.equal(
        tag,
        "trend query parameter keeps exact lowercase spelling",
        [event["method"], event["keys"], event["target"], event["empty_body"]],
        ["POST", ["citycode"], ["001"], True],
    )
    city = p.sql("SELECT id FROM observations_city WHERE geography='f-001'")
    for shape, expected in (("L", "30.000000"), ("C", "40.000000"), ("T", None)):
        query = urllib_query(p, shape, target.isoformat(), city=city)
        data = http_case(p, tag, f"{shape} same-hour authorized read", reader.call(query), 200)
        p.equal(
            tag,
            f"{shape} same-hour value or disputed emptiness",
            data["results"][0]["value"] if data["results"] else None,
            expected,
        )
    p.equal(
        tag,
        "three isolated AQI identities in PostgreSQL",
        p.sql(f"""
        SELECT string_agg(p.code || ':' || k.disputed::text,',' ORDER BY p.code)
        FROM observations_cityobservation k JOIN observations_product p ON p.id=k.product_id
        WHERE k.city_id={int(city)} AND k.observed_at_utc='{target.isoformat()}'::timestamptz
              AND k.indicator='AQI'
    """),
        "C:false,L:false,T:true",
    )
    p.equal(
        tag,
        "T only stores AQI",
        p.sql("""SELECT string_agg(DISTINCT k.indicator, ',')
        FROM observations_cityobservation k JOIN observations_product p ON p.id=k.product_id WHERE p.code='T'"""),
        "AQI",
    )


def transport_limits(p, account, _reader, _archivist):
    tag = "V06"
    cjob, ljob, sjob, tjob = [register_job(p, shape) for shape in "CLST"]
    cases = [
        ("C", cjob, {"body": []}, "invalid_shape"),
        ("S", sjob, {"raw": "{}", "headers": {"Content-Encoding": "gzip"}}, "unsupported_encoding"),
        ("T", tjob, {"raw": "[?" + REJECTED_MARKER + "]"}, "invalid_body"),
        ("L", ljob, {"body": [], "truncate": 10}, "invalid_body"),
        ("C", cjob, {"raw": "z" * (2**16 + 1), "omit_length": True}, "response_too_large"),
        (
            "S",
            sjob,
            {"status": 302, "body": {}, "headers": {"Location": "/synthetic-redirect-target"}},
            "unexpected_redirect",
        ),
    ]
    start_redirects = p.count("redirect")
    for index, (shape, job, script, code) in enumerate(cases):
        run, window, row, _ = collect(
            p, tag, account, shape, job, hour(-114 - index), script, "paused"
        )
        p.equal(
            tag,
            "fixed code, response state and zero writes",
            [
                row["error_code"],
                row["request_state"],
                p.receipt_count(run),
                p.accepted_count(window),
            ],
            [code, "responded", 0, 0],
        )
        http_case(
            p,
            tag,
            "resume after synthetic contract failure",
            account.call(
                f"{JOB_URL}{job}/",
                {"action": "resume_source" if code == "unexpected_redirect" else "resume_product"},
            ),
            200,
        )
    p.equal(tag, "no redirect target hit", p.count("redirect") - start_redirects, 0)
    start = datetime.now(UTC)
    run, window, result, _ = collect(
        p,
        tag,
        account,
        "T",
        tjob,
        hour(-122),
        {"raw": "[" + " " * 60 + "]", "chunk_size": 1, "chunk_delay": 0.33},
        "failed",
    )
    if result["reserved_at"] and result["finished_at"]:
        elapsed = (
            datetime.fromisoformat(result["finished_at"])
            - datetime.fromisoformat(result["reserved_at"])
        ).total_seconds()
    else:
        elapsed = (datetime.now(UTC) - start).total_seconds()
    p.record(tag, "drip deadline elapsed seconds", elapsed)
    assert 14.5 < elapsed < 20
    p.equal(
        tag,
        "drip feed has no write and ends with timeout",
        [result["error_code"], p.receipt_count(run), p.accepted_count(window)],
        ["timeout", 0, 0],
    )
    marker_absent(p, tag, REJECTED_MARKER)


def two_station_coverage(p, account, _reader, _archivist):
    tag = "V13"
    target = hour(-132)
    first = city_packet(target) | {
        "StationCode": "101",
        "PositionName": "Fixture Station",
        "CityCode": 0,
    }
    second = first | {"StationCode": "102", "CO": None}
    del second["SO2"]
    job = register_job(p, "S")
    run, window, _, _ = collect(p, tag, account, "S", job, target, {"body": [first, second]})
    totals = coverage(p, tag, account, window, (14, 13, 12))["coverage"]
    p.equal(
        tag,
        "object slots and CO/SO2 distinction",
        [totals["object_slots"], totals["by_indicator"]["CO"], totals["by_indicator"]["SO2"]],
        [{"E": 2, "O": 2}, {"E": 2, "O": 2, "V": 1}, {"E": 2, "O": 1, "V": 1}],
    )
    p.equal(
        tag,
        "independent SQL reconciles actual field states",
        p.sql(f"""
        SELECT count(*) FILTER (WHERE status<>'absent'),count(*) FILTER (WHERE accepted),
               count(*) FILTER (WHERE status='absent' AND indicator='SO2')
        FROM collection_runitem WHERE window_id={window}
    """),
        "13|13|1",
    )
    p.delivery(run)
    p.settled(run)
    coverage(p, tag, account, window, (14, 13, 12))
    unknown = register_job(p, "S")
    p.sql(f"UPDATE collection_job SET expected_codes=NULL,roster_basis='' WHERE id={unknown}")
    next_label = target + timedelta(hours=1)
    _, unknown_window, _, _ = collect(
        p,
        tag,
        account,
        "S",
        unknown,
        next_label,
        {"body": [row | {"TimePoint": next_label.isoformat()} for row in [first, second]]},
    )
    result = coverage(p, tag, account, unknown_window, (None, 13, 12))
    p.equal(
        tag,
        "unknown roster cannot imply ratios",
        [result["coverage"][k] for k in ("received_ratio", "valid_ratio")],
        [None, None],
    )
    code, body = account.call(f"{WINDOW_URL}{unknown_window}/?indicator=COLevel")
    p.equal(
        tag,
        "unsupported indicator excluded from coverage",
        [code, body["state"], body["coverage"]],
        [200, "not_applicable", None],
    )


def grant(p, shape, operation, fields, geography="*"):
    return int(
        p.shell(f"""
from datetime import date
from django.contrib.auth.models import User
from observations.models import Product
from observations.policy import OPERATIONS, register_grant
u = User.objects.get(username='collector')
product=Product.objects.get(source__namespace='s03.fixture', code={shape!r})
g = register_grant(u.pk,product.pk,operation={operation!r},audience=OPERATIONS[{operation!r}],
    geography={geography!r},object_code='*',indicator='*',fields={fields!r},data_from=date.min,
    data_until=date.max,terms='Synthetic verifier scope',terms_version='1')
print(g.pk)
""")
    )


def withdraw(p, grant_id):
    task_id = str(uuid.uuid4())
    p.shell(f"""
from django.contrib.auth.models import User
from observations.policy import revoke_grant
from config.celery import app
product = revoke_grant(User.objects.get(username='collector').pk,{int(grant_id)},'Synthetic verifier withdrawal')
app.send_task('observations.dispose', args=[product], task_id={task_id!r}, queue='s02.history', retry=False)
""")
    return wait_for(
        "S02 disposition completion in owning Worker",
        lambda: next(
            (
                line
                for line in p.compose("logs", "--no-color", "worker").splitlines()
                if f"observations.dispose[{task_id}] succeeded" in line
            ),
            None,
        ),
        seconds=75,
    )


def assert_disposed_coverage(p, tag, window):
    counts = window["coverage"]
    p.equal(
        tag,
        "authoritative survivor evidence under unknown disposed-window denominator",
        [window["state"], *(counts[key] for key in ("E", "O", "V", "accepted"))],
        ["disposed", None, 7, 7, 7],
    )
    p.equal(
        tag,
        "only the seven authorized cell keys retain recognition and present qualification",
        [[cell["object_code"], cell["accepted"], cell["valid"]] for cell in window["cells"]],
        [["002", True, True]] * 7,
    )
    p.equal(
        tag,
        "disposed projection has no Batch or manifest aggregates",
        any(
            row.get(field) is not None
            for row in window.get("runs", [])
            for field in ("batch_id", "manifest_count", "fetched_at")
        ),
        False,
    )


def disposition(p, account, reader, _archivist):
    tag = "V17"
    target = hour(-135)
    job = register_job(p, "L", expected=["001", "002"])
    packet = list_packet(target, 2)
    packet[0]["Quality"] = DISPOSITION_MARKER
    packet[0]["COLevel"] = 2
    run, window, row, _ = collect(p, tag, account, "L", job, target, {"body": packet})
    batch_id = int(row["batch_id"])
    coverage(p, tag, account, window, (14, 14, 14))
    p.equal(
        tag,
        "raw marker exists only in typed receipt metadata",
        p.sql(f"""
        SELECT count(*) FROM observations_receipt WHERE batch_id={batch_id}
          AND collection_metadata->>'source_description'='{DISPOSITION_MARKER}'
    """),
        "1",
    )
    grants = json.loads(
        p.sql("""SELECT json_object_agg(operation,id) FROM observations_usegrant
        WHERE operation IN ('display','retain_normalized') AND fields='["*"]'
            AND product_id=(SELECT id FROM observations_product WHERE code='L') AND revoked_at IS NULL""")
    )
    city = p.sql("SELECT id FROM observations_city WHERE geography='f-001'")
    url = urllib_query(p, "L", target.isoformat(), city=city)
    http_case(p, tag, "read authorization prior to withdrawal", reader.call(url), 200)
    p.record(tag, "display withdrawal completion", withdraw(p, grants["display"]))
    http_case(p, tag, "ordinary read hides withdrawn display", reader.call(url), 403, "use_denied")
    p.equal(tag, "display withdrawal preserves all retained receipts", p.receipt_count(run), 14)
    coverage(p, tag, account, window, (14, 14, 14))
    required = ["normalized", "provenance", "collection_ledger"]
    both = grant(p, "L", "retain_normalized", required)
    p.record(tag, "metadata withdrawal completion", withdraw(p, grants["retain_normalized"]))
    p.equal(
        tag,
        "metadata stripped physically and authorized keys retained",
        p.sql(f"""
        SELECT count(*),count(*) FILTER (WHERE collection_metadata IS NOT NULL)
        FROM observations_receipt WHERE batch_id={batch_id}
    """),
        "14|0",
    )
    coverage(p, tag, account, window, (14, 14, 14))
    narrow = grant(p, "L", "retain_normalized", required, geography="f-002")
    p.record(tag, "partial normalized withdrawal completion", withdraw(p, both))
    p.equal(
        tag,
        "PG keeps the authorized city's seven keys and removes lost items",
        p.sql(f"""
        SELECT count(*) FILTER (WHERE object_code='002'),count(*) FILTER (WHERE object_code='001')
        FROM collection_runitem WHERE window_id={window}
    """),
        "7|0",
    )
    p.equal(
        tag,
        "window aggregate and manifest scrubbed",
        p.sql(f"""
        SELECT w.disposed,w.expected IS NULL,r.manifest_count IS NULL,r.fetched_at IS NULL
        FROM collection_workwindow w JOIN collection_run r ON r.window_id=w.id WHERE r.id='{run}'
    """),
        "t|t|t|t",
    )
    http_case(
        p,
        tag,
        "current review scope blocks aggregate details",
        account.call(f"{WINDOW_URL}{window}/"),
        403,
        "use_denied",
    )
    survivor = http_case(
        p,
        tag,
        "limited reviewer reads the authorized city's evidence",
        account.call(f"{WINDOW_URL}{window}/?object_code=002"),
        200,
    )
    assert_disposed_coverage(p, tag, survivor)
    http_case(
        p,
        tag,
        "withdrawn city cannot be read directly",
        account.call(f"{WINDOW_URL}{window}/?object_code=001"),
        403,
        "use_denied",
    )
    renewed = grant(p, "L", "retain_normalized", required)
    assert_disposed_coverage(p, tag, p.window(tag, account, window))
    p.record(tag, "renewed full scope withdrawn", withdraw(p, renewed))
    p.record(tag, "final normalized withdrawal completion", withdraw(p, narrow))
    p.equal(
        tag,
        "empty Batch removed, SET_NULL linkage and no stale run items",
        p.sql(f"""
        SELECT r.batch_id IS NULL,NOT EXISTS(SELECT 1 FROM observations_batch WHERE id={batch_id}),
            NOT EXISTS(SELECT 1 FROM collection_runitem i WHERE i.window_id={window})
        FROM collection_run r WHERE r.id='{run}'
    """),
        "t|t|t",
    )
    grant(p, "L", "retain_normalized", required)
    p.equal(
        tag,
        "re-authorized window remains explicitly disposed",
        p.window(tag, account, window)["state"],
        "disposed",
    )
    count = p.count("L")
    # Verify actual redelivery using a fresh delivery envelope with the same Run UUID.
    task_id = str(uuid.uuid4())
    p.shell(f"""
from config.celery import app
app.send_task('collection.execute_run', args=[{run!r}], task_id={task_id!r}, queue='s03.collection', retry=False)
""")
    p.record(
        tag,
        "Worker completed disposed Run redelivery",
        wait_for(
            "disposed Run consumed",
            lambda: next(
                (
                    line
                    for line in p.compose("logs", "--no-color", "worker", "worker-b").splitlines()
                    if f"collection.execute_run[{task_id}] succeeded" in line
                ),
                None,
            ),
        ),
    )
    p.equal(
        tag,
        "replay after disposition did not reacquire or write",
        [p.count("L") - count, p.accepted_count(window)],
        [0, 0],
    )
    marker_absent(p, tag, DISPOSITION_MARKER)


PRODUCT_CASES = {
    "V02": nationwide,
    "V04": stations,
    "V05": trend,
    "V06": transport_limits,
    "V13": two_station_coverage,
    "V17": disposition,
}


def verify(cases, project=None):
    p = project if project is not None else S03Project()
    outcomes = dict.fromkeys(cases, "NOT RUN")
    failed = False
    try:
        p.claim()
        initial = source_hash()
        p.record(
            "runner", "source digest and evidence classes", {"hash": initial, "types": ["F", "I"]}
        )
        print("RUN", p.name, flush=True)
        p.compose("config", "-q")
        p.up(build=True)
        p.stop_beat()
        users = prepare(p)
        for tag in cases:
            try:
                PRODUCT_CASES[tag](p, *users)
            except Exception as exc:
                p.record(tag, "case failed", p.clean_text(str(exc)))
                print("FAIL", tag, p.clean_text(str(exc))[:400], flush=True)
                outcomes[tag] = "FAIL"
            else:
                outcomes[tag] = "PASS"
                print("PASS", tag, flush=True)
            p.reset_web_limits()
            for client in users:
                client.base = p.base
        p.record("runner", "synthetic HTTP events", p.origin("/_events"))
        p.equal("runner", "tested source unchanged during verification", source_hash(), initial)
    except Exception as exc:
        failed = True
        p.record("runner", "execution failure", p.clean_text(str(exc)))
        print("FAIL runner", p.clean_text(str(exc))[:400], flush=True)
    finally:
        try:
            p.cleanup()
            if p.owned:
                p.owned = False
                p.claim()
                p.owned = False
                p.record("runner", "owned Compose resources removed", True)
        except Exception as exc:
            failed = True
            p.record("runner", "cleanup failure", p.clean_text(str(exc)))
    failure = failed or any(state != "PASS" for state in outcomes.values())
    p.receipt("FAIL" if failure else "PASS", outcomes)
    return int(failure)


if __name__ == "__main__":
    if not __debug__:
        sys.exit("Assertions must be enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=PRODUCT_CASES)
    sys.exit(verify(parser.parse_args().case or PRODUCT_CASES))
