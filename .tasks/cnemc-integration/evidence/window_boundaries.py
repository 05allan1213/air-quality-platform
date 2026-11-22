"""Exercise the plan's concurrent jobs, exact endpoint refusal and overlapping trend hours."""

import hashlib
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_cnemc as v  # noqa: E402
from s01_runtime import Client, wait_for  # noqa: E402
from s03_runtime import RUN_URL, S03Project, prepare  # noqa: E402

HOUR = timedelta(hours=1)


def compete(p, operator, at):
    first, product = v.setup_job(p, "A07", {"F001": ["AQI"]}, unknown=True)
    # Explicit registration permits a second manual job with an identical window contract.
    second = int(
        p.shell(f"""
from collection.models import Job
from collection.services import register_job
j = Job.objects.get(pk={first})
print(register_job(j.executor_id, j.executor_id, j.product_id, None, j.profile,
                   j.cadence_seconds, None, None, '', selection=j.selection).pk)
""")
    )
    jobs = [first, second]
    assert first != second
    for job in jobs:
        v.enable(operator, job)
    p.script("A07", {"gate": "same-selector", "body": [v.packet(at, "F001")]})
    accounts = [operator, Client(p.base).login("collector", p.password)]
    barrier = Barrier(2)

    def submit(job, client):
        payload = dict(job=job, reason="manual", target=at.isoformat(), request_id=str(uuid4()))
        barrier.wait(timeout=8)
        status, body = client.call(RUN_URL, payload)
        assert status == 202, (status, body)
        return body

    with ThreadPoolExecutor(max_workers=2) as pool:
        posted = list(pool.map(submit, jobs, accounts))
    runs = [r["run_id"] for r in posted]
    window = int(posted[0]["window_id"])
    assert runs[0] != runs[1] and posted[1]["window_id"] == window
    try:
        p.hit("same-selector")
        wait_for(
            "second Worker coalesced under the same window owner",
            lambda: (
                p.sql(
                    f"SELECT count(*) FROM collection_run WHERE window_id={window} AND status='coalesced'"
                )
                == "1"
            ),
            seconds=8,
        )
    finally:
        p.release("same-selector")
    rows = [v.wait_run(p, rid) for rid in runs]
    assert sorted(r["status"] for r in rows) == ["coalesced", "finished"]
    assert {r["job_id"] for r in rows} == set(jobs)
    reservations = sum(r["reserved_at"] is not None for r in rows)
    hits = p.count("A07")
    accepted = p.accepted_count(window)
    assert reservations == hits == accepted == 1
    assert p.sql(f"SELECT count(*) FROM collection_workwindow WHERE product_id={product}") == "1"
    v.verify_counts(p, operator, posted[0], (None, 1, 1))
    return dict(
        jobs=jobs,
        runs=runs,
        window=window,
        product=product,
        coverage=[None, 1, 1],
        accepted=accepted,
        http=hits,
        reservations=reservations,
    )


def reject_exact(p, operator, first, at):
    # The operating calendar begins before both targets. Only the two-hour lookback excludes the older one.
    policy = dict(
        equivalence_basis="F exact station projection",
        lookback_hours=2,
        max_windows_per_scan=2,
        max_requests_per_scan=1,
        operating_from=(at - 72 * HOUR).isoformat(),
    )
    ids = json.loads(
        p.shell(f"""
import json
from datetime import date
from collection.models import Job
from collection.services import register_job
from collection.cnemc_jobs import anchor, exact_available
from observations.policy import register_grant, clock
j = Job.objects.get(pk={first})
allow = j.product.usegrant_set.get(operation='acquire_online', revoked_at=None)
assert allow.endpoints == ['A07']
g = register_grant(j.executor_id, j.product_id, operation='acquire_online', audience='platform',
        geography='*', fields=['*'], data_from=date.min, data_until=date.max, terms='F exact',
        terms_version='F-exact', endpoints=['A10'], collection_limits=allow.collection_limits)
k = register_job(j.executor_id, j.executor_id, j.product_id, None, j.profile,
        j.cadence_seconds, None, j.selection['objects'], 'F station roster',
        selection=j.selection, backfill_policy={policy!r})
from datetime import datetime, timedelta
at = datetime.fromisoformat({at.isoformat()!r})
assert anchor(k, clock()) == at and exact_available(k, at-timedelta(hours=1), clock())
assert not exact_available(k, at-timedelta(hours=3), clock())
print(json.dumps([k.pk, g.pk, j.executor_id, j.product_id]))
""")
    )
    job, grant, actor, product = ids
    v.enable(operator, job)
    count_reserved = (
        "SELECT count(*) FROM collection_run WHERE endpoint='A10' AND reserved_at IS NOT NULL"
    )
    counters = (p.count("A10"), int(p.sql(count_reserved)))
    assert counters == (0, 0)
    old = p.post("lookback", operator, job, at - 3 * HOUR, reason="backfill")
    row = v.wait_run(p, old["run_id"])
    assert (row["endpoint"], row["status"], row["error_code"], row["reserved_at"]) == (
        "A10",
        "failed",
        "backfill_unavailable",
        None,
    )
    assert (p.count("A10"), int(p.sql(count_reserved))) == counters
    p.shell(
        f"from observations.policy import revoke_grant\nrevoke_grant({actor}, {grant}, 'F withdraw exact access')"
    )
    grants = json.loads(
        p.sql(
            f"SELECT jsonb_agg(endpoints) FROM observations_usegrant WHERE product_id={product} AND operation='acquire_online' AND revoked_at IS NULL"
        )
    )
    assert grants == [["A07"]]
    before = int(p.sql("SELECT count(*) FROM collection_run WHERE endpoint='A10'"))
    denied = p.post("endpoint", operator, job, at - HOUR, reason="backfill", allowed=(403,))
    assert denied == {"code": "use_denied"}
    assert int(p.sql("SELECT count(*) FROM collection_run WHERE endpoint='A10'")) == before
    assert (p.count("A10"), int(p.sql(count_reserved))) == counters
    return dict(
        job=job,
        anchor=at.isoformat(),
        lookback_hours=policy["lookback_hours"],
        operating_from=policy["operating_from"],
        outside_target=(at - 3 * HOUR).isoformat(),
        outside_run=old["run_id"],
        outside_reason=row["error_code"],
        in_range_target=(at - HOUR).isoformat(),
        endpoints=grants[0],
        denied_status=403,
        denied_reason=denied["code"],
        http_deltas=[0, 0],
        reservation_deltas=[0, 0],
    )


def check(p, operator, reader):
    p.stop_beat()
    job, product = v.setup_job(p, "A05", {v.CITY: ["AQI"]})
    v.enable(operator, job)
    city = int(p.sql(f"SELECT city_id FROM collection_job WHERE id={job}"))
    t = wait_for(
        "sufficient time in the first publication hour",
        lambda: v.current_hour() if datetime.now(UTC).minute < 59 else None,
        seconds=70,
    )
    hole = t - 2 * HOUR

    def trend(target):
        labels = [target - i * HOUR for i in range(24)]
        return [
            dict(v.packet(at), AQI=51 if at == hole else 37)
            for at in labels
            if target != t or at != hole
        ]

    p.script("A05", {"body": trend(t)})
    first = p.post("trend", operator, job, t)
    assert v.wait_run(p, first["run_id"])["result"] == "partial"
    v.verify_counts(p, operator, first, (24, 23, 23))
    absent = v.exact(reader, product, "city", city, "AQI", hole)
    assert absent["count"] == 0 and absent["reason"] == "no_observation_at_time"
    equal = compete(p, operator, t)
    refused = reject_exact(p, operator, equal["jobs"][0], t)
    p.record("boundary", "equivalent jobs", equal)
    p.record("boundary", "exact endpoint refusals", refused)
    # The registered latest-only job must see the second label in its real publication hour.
    next_hour = t + HOUR
    print("Station boundaries PASS. Next F trend publication:", next_hour.isoformat(), flush=True)
    deadline = monotonic() + 3670
    while (remaining := (next_hour - datetime.now(UTC)).total_seconds()) > 0:
        assert monotonic() < deadline, "next publication hour did not arrive"
        sleep(min(45, remaining))
    p.script("A05", {"body": trend(next_hour)})
    second = p.post("trend", operator, job, next_hour)
    assert second["window_id"] != first["window_id"]
    assert v.wait_run(p, second["run_id"])["status"] == "finished"
    v.verify_counts(p, operator, first, (24, 23, 23))
    v.verify_counts(p, operator, second, (24, 24, 24))
    api = v.exact(reader, product, "city", city, "AQI", hole)
    assert (
        api["count"] == 1
        and Decimal(api["results"][0]["value"]) == 51
        and api["results"][0]["unit"] == "1"
    )
    revision = int(api["results"][0]["revision_id"])
    receipts = [
        int(
            p.sql(
                f"SELECT count(*) FROM observations_receipt WHERE batch_id=(SELECT batch_id FROM collection_run WHERE id='{r['run_id']}') AND city_revision_id={revision}"
            )
        )
        for r in (first, second)
    ]
    keys = int(
        p.sql(f"SELECT count(*) FROM observations_cityobservation WHERE product_id={product}")
    )
    assert receipts == [0, 1] and keys == 25 and p.count("A05") == 2
    return dict(
        equivalent_jobs=equal,
        exact_refusals=refused,
        trend=dict(
            product=product,
            city=city,
            hours=[t.isoformat(), next_hour.isoformat()],
            hole=hole.isoformat(),
            runs=[first["run_id"], second["run_id"]],
            windows=[first["window_id"], second["window_id"]],
            coverage=[[24, 23, 23], [24, 24, 24]],
            before_reason=absent["reason"],
            after_value=51,
            revision_id=revision,
            receipts_by_run=receipts,
            keys=keys,
            http=2,
        ),
    )


def main():
    p = S03Project()
    digest = v.source_hash()
    probe = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    overlay = p.evidence / "f-internal.yaml"
    overlay.write_text(
        "services:\n"
        + "".join(
            f'  {name}:\n    environment:\n      CNEMC_FIXTURE_HTTP: "1"\n      CNEMC_MODE: "off"\n'
            for name in ("worker", "worker-b", "web", "migrate", "beat")
        )
    )
    p.prefix[6:6] = ["-f", str(overlay)]
    data = dict(
        project=p.name,
        git_base=p.cmd(["git", "rev-parse", "HEAD"]),
        source_hash=digest,
        probe_hash=probe,
        evidence="F/I",
        status="FAIL",
    )
    try:
        p.claim()
        p.up(build=True)
        data["worker_image_ids"] = {
            n: p.cmd(["docker", "inspect", "--format", "{{.Image}}", p.compose("ps", "-q", n)])
            for n in ("worker", "worker-b")
        }
        operator, reader, _ = prepare(p)
        data.update(check(p, operator, reader))
        data["source_hash_after"] = v.source_hash()
        assert (
            data["source_hash_after"] == digest
            and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == probe
        )
        data["status"] = "PASS"
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        data["failure"] = f"{frame.name}:{frame.lineno} {p.clean_text(str(exc))[:350]}"
    finally:
        p.cleanup()
    path = p.evidence / "window-boundaries.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(data["status"], path, data.get("failure", ""), flush=True)
    return 0 if data["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
