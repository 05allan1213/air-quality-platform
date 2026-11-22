"""Run hourly CNEMC contract acceptance exclusively against a task-owned F HTTP origin."""

import argparse
import json
import sys
import traceback
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import monotonic, sleep
from urllib.parse import urlencode
from uuid import UUID, uuid4
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from s01_runtime import source_hash, wait_for
from s02_runtime import VerificationError
from s03_runtime import JOB_URL, S03Project, prepare

ZONE = ZoneInfo("Asia/Shanghai")
CITY = "999999"
INDICATORS = ["AQI", "CO", "NO2", "O3", "PM10", "PM2.5", "SO2"]
MEASURED = {"AQI": 43, "SO2": 12, "NO2": 17, "CO": 0.31, "O3": 28, "PM10": 21, "PM2_5": 8}
PRODUCTS = {
    "A01": "city-list-hour",
    "A03": "city-live-hour",
    "A04": "station-city-hour",
    "A05": "city-trend-hour",
    "A07": "station-national-hour",
}
LIMITS = dict(min_interval_seconds=0, period_seconds=60, max_requests=60, max_concurrent=2)


def current_hour():
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def packet(at, station=None):
    row = dict(
        MEASURED,
        CityCode=CITY,
        Area="F City",
        TimePoint=at.astimezone(ZONE).replace(tzinfo=None).isoformat(),
    )
    if station:
        row.update(StationCode=station, PositionName=station)
    return row


def setup_job(
    p,
    endpoint,
    roster,
    *,
    unknown=False,
    quota=None,
    due=None,
    repair=False,
    limited=False,
    standard=None,
):
    code = PRODUCTS[endpoint]
    limits = dict(LIMITS, period_seconds=5, max_requests=1) if quota else LIMITS
    source_catalog = dict(product=code, evidence="F directory", **{"from": "2023-01-01T00:00:00Z"})
    grants = []
    for op, audience in (
        ("acquire_online", "platform"),
        ("process_local", "platform"),
        ("retain_normalized", "platform"),
        ("display", "local_authenticated"),
        ("review", "operator"),
    ):
        grant = dict(
            operation=op,
            audience=audience,
            geography="*",
            object_code="*",
            indicator="*",
            data_from=date.min.isoformat(),
            data_until=date.max.isoformat(),
            fields=["*"],
            terms="F offline",
            terms_version="F1",
            valid_from="2023-01-01T00:00:00Z",
        )
        if op == "acquire_online":
            grant.update(
                endpoints=[endpoint, "A10"] if repair else [endpoint], collection_limits=limits
            )
            if quota:
                grant.update(
                    max_total_requests=quota,
                    valid_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                )
        if limited and op == "process_local":
            grant.update(object_code=next(iter(roster)), indicator="AQI", fields=["normalized"])
        grants.append(grant)
    selection = {"mode": "include", "objects": roster}
    spec = dict(
        product=code,
        executor=int(p.sql("SELECT id FROM auth_user WHERE username='collector'")),
        selection=selection,
    )
    if not unknown:
        spec.update(expected=roster, roster_basis="Independent F monitoring schedule")
    if standard:
        spec["standard"] = standard
    if quota:
        spec["validation_terms_version"] = "F1"
    if due:
        spec["first_due"] = due.isoformat()
    if repair:
        spec["backfill_policy"] = dict(
            equivalence_basis="F identical station products",
            lookback_hours=23,
            max_windows_per_scan=2,
            max_requests_per_scan=1,
            operating_from=(current_hour() - timedelta(hours=2)).isoformat(),
        )
    if endpoint not in {"A01", "A07"}:
        spec["city_code"] = CITY
    manifest = dict(
        namespace="cnemc.fixture",
        collection_limits=limits,
        products=[dict(code=code, evidence="F contract", attribution="F", grants=grants)],
        cities=[dict(source_catalog, code=CITY, name="F City", geography="F")],
        jobs=[spec],
    )
    if endpoint in {"A04", "A07"}:
        manifest["stations"] = [
            dict(source_catalog, code=c, name=c, city_code=CITY) for c in roster
        ]
    # manifest contains generated test records only. Production inputs never enter this verifier.
    ids = json.loads(
        p.shell(
            f"import json\nfrom collection.registration import install\nprint(json.dumps(install({spec['executor']}, {manifest!r})))"
        )
    )
    return ids["jobs"][0], ids["products"][code]


def enable(account, job):
    code, result = account.call(f"{JOB_URL}{job}/", {"action": "resume"})
    assert code == 200 and result["enabled"], (code, result)


def wait_run(p, identifier, timeout=600):
    end = monotonic() + timeout + 3
    while monotonic() < end:
        row = p.row(identifier)
        if row["status"] not in {"running", "pending"}:
            return row
        sleep(0.5)
    raise AssertionError("Worker deadline exceeded")


def exact_url(product, entity, pk, indicator, at, series=None):
    q = dict(
        scope="synthetic",
        product=product,
        indicator=indicator,
        report_kind="hourly",
        window_definition="hourly_label_unspecified",
        observed_at=at.isoformat(),
        standard_series=series
        or ("unknown" if indicator == "AQI" or indicator.endswith("Level") else "not_applicable"),
        **{entity: pk},
    )
    return "/api/v1/observations/current/?" + urlencode(q)


def exact(account, product, entity, pk, indicator, at, series=None):
    code, result = account.call(exact_url(product, entity, pk, indicator, at, series))
    assert code == 200, (code, result)
    return result


def verify_counts(p, account, run, counts):
    code, result = account.call(
        "/api/v1/admin/collection-windows/" + str(int(run["window_id"])) + "/"
    )
    assert code == 200, (code, result)
    coverage = result["coverage"]
    assert [coverage[k] for k in ("E", "O", "V")] == list(counts), coverage
    return coverage


def chain(p, account, reader):
    t = current_hour()
    data = packet(t)
    expected_l = ["AQI", *(i + "Level" for i in INDICATORS if i != "AQI")]
    facts = {}
    for ep, roster, body in (
        ("A03", {CITY: INDICATORS}, data),
        (
            "A01",
            {CITY: expected_l},
            [
                dict(
                    data,
                    **{i.replace(".", "_"): 2 for i in expected_l[1:]},
                    AQI=44,
                    AqiLevel=5,
                    Quality="F category",
                )
            ],
        ),
        ("A04", {"F001": INDICATORS}, [dict(packet(t, "F001"), CityCode=0)]),
        ("A05", {CITY: ["AQI"]}, [dict(packet(t - timedelta(hours=i)), AQI=45) for i in range(24)]),
    ):
        job, prod = setup_job(p, ep, roster, due=datetime.now(UTC) if ep == "A03" else None)
        p.script(ep, {"body": body})
        enable(account, job)
        if ep == "A03":
            # DatabaseScheduler and the ordinary scanner create and dispatch the first run.
            rid = wait_for(
                "Beat scheduled A03",
                lambda job=job: p.sql(
                    f"SELECT id FROM collection_run WHERE job_id={job} AND reason='scheduled'"
                ),
                seconds=40,
            )
            p.stop_beat()
            row = wait_run(p, rid)
            run = dict(run_id=rid, window_id=row["window_id"])
        else:
            run = p.post("chain", account, job, t)
            row = wait_run(p, run["run_id"])
        assert row["status"] == "finished" and row["endpoint"] == ep, row
        n = 24 if ep == "A05" else 7
        verify_counts(p, account, run, (n, n, n))
        assert p.count(ep) == 1
        p.worker_log(run["run_id"])
        facts[ep] = (run, prod)
    city = int(p.sql("SELECT id FROM observations_city WHERE name='F City'"))
    for ep, wanted in (("A03", 43), ("A01", 44), ("A05", 45)):
        result = exact(reader, facts[ep][1], "city", city, "AQI", t)["results"]
        assert len(result) == 1 and Decimal(result[0]["value"]) == wanted
        if ep == "A01":
            assert (result[0]["grade"], result[0]["color"]) == (None, "")
            # Optional source text is authorized separately on its receipt.
            code, projected = account.call(
                f"/api/v1/admin/collection-windows/{facts[ep][0]['window_id']}/?indicator=AQI"
            )
            assert code == 200
            optional = projected["receipts"][0]["metadata"]
            assert (optional["source_description"], optional["source_grade"]) == ("F category", 5)
    for ind in INDICATORS:
        value = exact(reader, facts["A03"][1], "city", city, ind, t)["results"][0]
        assert Decimal(value["value"]) == Decimal(str(data[ind.replace(".", "_")]))
        assert value["unit"] == ("1" if ind == "AQI" else "mg/m³" if ind == "CO" else "µg/m³")
    # The national and city products may share a verified directory, never an observation key.
    job, prod = setup_job(p, "A07", {"F001": INDICATORS, "F002": INDICATORS}, repair=True)
    p.script("A07", {"body": [packet(t, "F001")]})
    enable(account, job)
    first = p.post("chain", account, job, t)
    assert wait_run(p, first["run_id"])["result"] == "partial"
    verify_counts(p, account, first, (14, 7, 7))
    body = [packet(t, c) for c in ("F001", "F002")]
    for r in body:
        del r["CityCode"]
        r["TimePoint"] = f"/Date({int(t.timestamp() * 1000)})/"
    p.script("A10", {"body": body})
    uid = str(uuid4())
    fill = p.post("chain", account, job, t, request_id=uid, reason="backfill")
    assert wait_run(p, fill["run_id"])["status"] == "finished"
    repeated = p.post("chain", account, job, t, request_id=uid, reason="backfill")
    assert fill == repeated and fill["window_id"] == first["window_id"]
    verify_counts(p, account, fill, (14, 14, 14))
    assert p.accepted_count(fill["window_id"]) == 14
    assert p.count("A07") == p.count("A10") == 1
    facts["A07"] = (first, prod)
    facts["A10"] = (fill, prod)
    p.assert_unique("chain")
    events = p.origin("/_events")
    assert all(e["window"] is None for e in events)
    assert events[-1]["form"] == {"date": [t.astimezone(ZONE).strftime("%Y-%m-%d %H:%M:%S")]}
    p.compose("stop", "-t", "3", "worker", "worker-b", "s03-origin")
    p.reset_web_limits()
    reader.base = p.base
    assert exact(reader, facts["A03"][1], "city", city, "AQI", t)["count"] == 1
    assert (
        exact(reader, facts["A03"][1], "city", city, "AQI", t - timedelta(hours=3))["reason"]
        == "no_observation_at_time"
    )
    return dict(products=facts, observed_at=t.isoformat(), http=len(events), cold_read=True)


def sql_probe(p):
    p.sql("""
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE TABLE f_window_timing(id bigint, at timestamptz, tx_seconds double precision);
GRANT INSERT ON f_window_timing TO s01_app;
CREATE FUNCTION f_window_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 INSERT INTO f_window_timing VALUES(NEW.id,clock_timestamp(),extract(epoch from clock_timestamp()-transaction_timestamp()));
 RETURN NEW; END $$;
CREATE TRIGGER f_checkpoint AFTER UPDATE OF lease_until ON collection_workwindow FOR EACH ROW EXECUTE FUNCTION f_window_checkpoint();
""")


def verify_capacity_reads(p, reader, product, codes, at, partial):
    checks = (
        [(codes[0], "AQI")]
        if partial
        else [(code, indicator) for code in (codes[0], codes[-1]) for indicator in INDICATORS]
    )
    for code, indicator in checks:
        epoch = int(
            p.sql(f"""SELECT e.id FROM observations_siteepoch e
            JOIN observations_stationidentifier si ON si.station_id=e.station_id
            JOIN observations_product p ON p.source_id=si.source_id
            WHERE p.id={product} AND si.kind='code' AND si.code='{code}'
              AND e.active_period @> '{at.isoformat()}'::timestamptz""")
        )
        result = exact(reader, product, "site_epoch", epoch, indicator, at)
        assert result["count"] == 1 and result["reason"] is None, result
        (value,) = result["results"]
        assert (value["product"], value["site_epoch"], value["indicator"]) == (
            product,
            epoch,
            indicator,
        )
        assert Decimal(value["value"]) == Decimal(str(MEASURED[indicator.replace(".", "_")]))
    return len(checks), exact_url(product, "site_epoch", epoch, indicator, at)


def verify_capacity_disposal(p, account, reader, job, product, run, url):
    """Leave a durable withdrawal pending while the owners are down, then cold-start one."""
    before = p.row(run["run_id"])
    assert before["reserved_at"] and before["batch_id"]
    p.compose("stop", "-t", "5", "worker", "worker-b")
    p.shell(f"""
from django.contrib.auth.models import User
from observations.models import UseGrant
from observations.policy import revoke_grant
u = User.objects.get(username='collector')
g = UseGrant.objects.get(product_id={product}, operation='retain_normalized', revoked_at=None)
revoke_grant(u.pk, g.pk, 'F capacity withdrawal')
""")
    assert reader.call(url)[0] == 403
    pending = f"SELECT count(*) FROM observations_dispositionjob WHERE product_id={product} AND completed_at IS NULL AND due_at<=clock_timestamp()"
    assert int(p.sql(pending)) > 0
    start = monotonic()
    p.compose("start", "worker")
    wait_for(
        "cold Worker finished national retention disposition",
        lambda: p.sql(pending) == "0",
        seconds=600,
    )
    seconds = monotonic() - start
    p.reset_web_limits()
    account.base = reader.base = p.base
    assert reader.call(url)[0] == 403
    assert account.call(f"/api/v1/admin/collection-windows/{run['window_id']}/")[0] == 403
    assert account.call(f"{JOB_URL}{job}/", {"action": "resume"}) == (403, {"code": "job_disposed"})
    assert (
        p.sql(f"""SELECT disposed AND NOT enabled AND expected_codes IS NULL AND
        selection='{{}}'::jsonb AND roster_basis='' AND next_due_at IS NULL
        FROM collection_job WHERE id={job}""")
        == "t"
    )
    assert (
        p.sql(f"""SELECT disposed AND result='disposed' AND expected IS NULL AND
        roster_basis='' AND selection='{{"mode":"include","objects":{{}}}}'::jsonb
        FROM collection_workwindow WHERE id={int(run["window_id"])}""")
        == "t"
    )
    after = p.row(run["run_id"])
    assert (
        after["reserved_at"] is None
        and after["request_state"] == "disposed"
        and after["endpoint"] == "A07"
    )
    assert after["batch_id"] is None and after["manifest_count"] is None
    # prepare() seeds an unrelated F directory. Scope the purge to the national product/source.
    source = int(p.sql(f"SELECT source_id FROM observations_product WHERE id={product}"))
    checks = {
        "observations_stationobservation": f"product_id={product}",
        "observations_stationrevision": "TRUE",  # this case ingests exactly one station product
        "observations_receipt": f"batch_id={int(before['batch_id'])}",
        "observations_batch": f"product_id={product}",
        "observations_qualityissue": f"product_id={product}",
        "collection_runitem": f"window_id={int(run['window_id'])}",
        **{
            f"observations_{table}": f"source_id={source}"
            for table in ("city", "cityidentifier", "station", "stationidentifier")
        },
    }
    for table, condition in checks.items():
        assert p.sql(f"SELECT count(*) FROM {table} WHERE {condition}") == "0", table
    assert (
        p.sql(f"SELECT count(*) FROM collection_run WHERE job_id={job} AND reserved_at IS NOT NULL")
        == "0"
    )
    assert p.count("A07") == 1
    return dict(cold_disposal_seconds=round(seconds, 2), retained_reservations=0)


def capacity(p, account, reader, *, partial):
    p.stop_beat()
    sql_probe(p)
    codes = [f"F{i:04d}" for i in range(1, 1602)]
    roster = {codes[0]: ["AQI"]} if partial else dict.fromkeys(codes, INDICATORS)
    job, product = setup_job(p, "A07", roster, unknown=partial, limited=partial)
    enable(account, job)
    t = current_hour()
    rows = [packet(t, code) for code in codes]
    for row in rows:
        row.update(FixtureTextA="x" * 200, FixtureTextB="y" * 200, FixtureTextC="z" * 139)
    body = json.dumps(rows)
    size = len(body.encode())
    assert 1_180_000 <= size <= 1_360_000, size
    p.script("A07", {"raw": body})
    p.sql("SELECT pg_stat_statements_reset();")
    start = monotonic()
    run = p.post("partial" if partial else "capacity", account, job, t)
    result = wait_run(p, run["run_id"])
    seconds = monotonic() - start
    assert result["status"] == "finished", result
    counts = (None, 1, 1) if partial else (11207, 11207, 11207)
    verify_counts(p, account, run, counts)
    assert (
        p.accepted_count(run["window_id"]) == counts[1]
        and p.receipt_count(run["run_id"]) == counts[1]
    )
    assert (
        int(
            p.sql(
                f"SELECT count(*) FROM observations_stationobservation WHERE product_id={product}"
            )
        )
        == counts[1]
    )
    assert p.count("A07") == 1
    stats = p.sql(
        """SELECT jsonb_build_object('calls',sum(calls),'directory_calls',coalesce(sum(calls) FILTER (WHERE query ~* 'from "?observations_(cityidentifier|siteepoch|stationidentifier)"?'),0)) FROM pg_stat_statements WHERE userid=(SELECT usesysid FROM pg_user WHERE usename='s01_app') AND dbid=(SELECT oid FROM pg_database WHERE datname=current_database())"""
    )
    timing = json.loads(
        p.sql(
            f"""SELECT jsonb_build_object('fence_updates',count(*),'max_tx_seconds',max(tx_seconds),'max_renewal_seconds',max(extract(epoch from delta))) FROM (SELECT tx_seconds,at-lag(at) over(order by at) AS delta FROM f_window_timing WHERE id={int(run["window_id"])}) a"""
        )
    )
    assert timing["max_tx_seconds"] < 60 and timing["max_renewal_seconds"] < 60 and seconds < 600, (
        timing
    )
    expected_blocks = (counts[1] + 127) // 128
    assert timing["fence_updates"] == expected_blocks + 4, timing
    peak = {
        service: int(p.compose("exec", "-T", service, "cat", "/sys/fs/cgroup/memory.peak"))
        for service in ("worker", "worker-b")
    }
    assert all(peak.values())
    p.assert_unique("capacity")
    reads, url = verify_capacity_reads(p, reader, product, codes, t, partial)
    disposal = (
        {} if partial else verify_capacity_disposal(p, account, reader, job, product, run, url)
    )
    return dict(
        exact_api_reads=reads,
        **disposal,
        run_id=str(UUID(run["run_id"])),
        window=run["window_id"],
        payload_bytes=size,
        http=1,
        coverage=counts,
        blocks=expected_blocks,
        wall_seconds=round(seconds, 2),
        worker_cgroup_peak_bytes=peak,
        postgres=json.loads(stats),
        **timing,
    )


def quota(p, account, reader):
    p.stop_beat()
    job, _ = setup_job(p, "A03", {CITY: INDICATORS}, quota=1)
    enable(account, job)
    # Give the latest-only retry enough time before its hourly target closes.
    t = wait_for(
        "open quota test hour",
        lambda: current_hour() if datetime.now(UTC).minute < 59 else None,
        seconds=65,
    )
    p.script("A03", {"status": 503})
    first = p.post("quota", account, job, t)
    assert wait_run(p, first["run_id"])["error_code"] == "upstream_transient"
    rid = str(UUID(first["run_id"]))
    reserved = p.row(rid)
    assert reserved["request_state"] == "responded" and reserved["reserved_at"]
    grant = int(reserved["validation_grant_id"])
    assert [(e["shape"], e["status"]) for e in p.origin("/_events")] == [("A03", 503)]
    # Wait for the *actual registered* rolling limit, without changing reservation timestamps.
    period = int(
        p.sql(
            f"SELECT (collection_limits->>'period_seconds')::int FROM observations_usegrant WHERE id={grant}"
        )
    )
    assert p.sql(f"SELECT max_total_requests FROM observations_usegrant WHERE id={grant}") == "1"
    wait_for(
        "rolling period elapsed in PostgreSQL",
        lambda: (
            p.sql(
                f"SELECT clock_timestamp() >= reserved_at + make_interval(secs => {period}) FROM collection_run WHERE id='{rid}'"
            )
            == "t"
        ),
        seconds=period + 10,
    )
    p.sql(f"UPDATE collection_run SET next_retry_at=clock_timestamp() WHERE id='{rid}'")
    p.reconcile()
    linked = p.successor(rid)
    successor = wait_run(p, linked)
    assert (
        successor["error_code"] == "validation_budget_exhausted"
        and successor["reserved_at"] is None
    )
    p.compose("restart", "worker", "worker-b")

    def completions():
        return p.compose("logs", "--no-color", "worker", "worker-b").count(
            f"collection.execute_run[{rid}] succeeded"
        )

    previous = completions()
    assert previous >= 1
    p.delivery(rid)
    wait_for("both Celery deliveries of the same run finished", lambda: completions() > previous)
    second = p.post("quota", account, job, current_hour())
    assert second["run_id"] not in {rid, linked}
    manual = wait_run(p, second["run_id"])
    assert manual["error_code"] == "validation_budget_exhausted" and manual["reserved_at"] is None
    assert p.count("A03") == 1 and p.row(rid)["reserved_at"] == reserved["reserved_at"]
    assert (
        p.sql(
            f"SELECT count(*) FROM collection_run WHERE validation_grant_id={grant} AND reserved_at IS NOT NULL"
        )
        == "1"
    )
    return dict(
        run=rid,
        successor=linked,
        new_uuid=second["run_id"],
        http=1,
        reservations=1,
        rolling_period_seconds=period,
        old_uuid_completions=completions(),
        validation_grant=grant,
    )


PG_TESTS = (
    "tests/collection/test_cnemc_jobs.py",
    "tests/collection/test_cnemc_recovery_catalog.py",
    "tests/collection/test_cnemc_disposition.py",
    "tests/collection/test_cnemc_wire.py",
    "tests/collection/test_cnemc_migrations.py",
    "tests/test_observation_catalog.py",
)


def targeted_pg(p, account, reader):
    p.stop_beat()
    guest = {"POSTGRES_USER": "cnemc_pytest", "POSTGRES_PASSWORD": p.test_password}
    p.sql(f"CREATE ROLE cnemc_pytest LOGIN CREATEDB PASSWORD '{p.test_password}'")
    report = "/tmp/cnemc-targeted.xml"
    try:
        code = 0
        try:
            p.compose(
                "exec",
                "-T",
                *(arg for key in guest for arg in ("-e", key)),
                "web",
                "python",
                "-m",
                "pytest",
                "-q",
                *PG_TESTS,
                f"--junitxml={report}",
                env=guest,
                timeout=600,
            )
        except VerificationError as exc:
            code = exc.exit_code
        root = ElementTree.fromstring(p.compose("exec", "-T", "web", "cat", report))
        cases = {
            f"{n.get('classname')}::{n.get('name')}": (
                "FAIL"
                if n.find("failure") is not None or n.find("error") is not None
                else "NOT RUN"
                if n.find("skipped") is not None
                else "PASS"
            )
            for n in root.iter("testcase")
        }
        p.record("pg", "targeted pytest", dict(selectors=PG_TESTS, exit=code, cases=cases))
        assert cases and code == 0 and set(cases.values()) == {"PASS"}, (
            code,
            [k for k, v in cases.items() if v != "PASS"],
        )
    finally:
        p.sql("DROP ROLE cnemc_pytest")

    # Wire tests check both standard modes. Also exercise the confirmed grade through the Worker and API.
    t = current_hour()
    standard = {
        "series": "HJ633-2026",
        "basis": "source_stated",
        "evidence": "F standard",
        "from": date.min.isoformat(),
        "until": date.max.isoformat(),
    }
    job, product = setup_job(p, "A01", {CITY: ["AQI"]}, standard=standard)
    enable(account, job)
    p.script("A01", {"body": [dict(packet(t), AqiLevel=5, Quality="F category")]})
    run = p.post("pg", account, job, t)
    assert wait_run(p, run["run_id"])["status"] == "finished"
    verify_counts(p, account, run, (1, 1, 0))
    city = int(
        p.sql(f"SELECT city_id FROM observations_cityobservation WHERE product_id={product}")
    )
    assert (
        p.sql(
            f"SELECT count(*) FROM observations_qualityissue WHERE product_id={product} AND code='source_grade_conflict'"
        )
        == "1"
    )
    result = exact(reader, product, "city", city, "AQI", t, series=standard["series"])
    assert result["count"] == 0 and result["reason"] == "suspect", result
    assert p.count("A01") == 1
    return dict(pytest_passed=len(cases), grade_conflict_run=run, http=1)


def run_case(case, invocation_hash):
    initial = source_hash()
    p = S03Project()
    p.record("runner", "source SHA-256 before build", initial)
    overlay = p.evidence / "cnemc-internal.yaml"
    overlay.write_text(
        "services:\n"
        + "".join(
            f'  {service}:\n    environment:\n      CNEMC_FIXTURE_HTTP: "1"\n      CNEMC_MODE: "off"\n'
            for service in ("web", "worker", "worker-b", "beat", "migrate")
        )
        + "  db:\n    command: [postgres, -c, shared_preload_libraries=pg_stat_statements]\n"
    )
    p.prefix[6:6] = ["-f", str(overlay)]
    measures = {}
    git_base = None
    worker_image_ids = {}
    status = "FAIL"
    try:
        assert initial == invocation_hash, "source changed between verification cases"
        git_base = p.cmd(["git", "rev-parse", "--verify", "HEAD^{commit}"])
        p.claim()
        p.up(build=True)
        for service in ("worker", "worker-b"):
            container_id = p.compose("ps", "-q", service)
            worker_image_ids[service] = p.cmd(
                ["docker", "container", "inspect", "--format", "{{.Image}}", container_id]
            )
        account, reader, _ = prepare(p)
        if case == "chain":
            measures = chain(p, account, reader)
        elif case == "pg":
            measures = targeted_pg(p, account, reader)
        elif case == "quota":
            measures = quota(p, account, reader)
        else:
            measures = capacity(p, account, reader, partial=case == "partial")
        status = "PASS"
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        measures["failure"] = (
            f"{type(exc).__name__} at {frame.name}:{frame.lineno} {p.clean_text(str(exc))[:1500]}"
        )
    finally:
        try:
            p.cleanup()
        except Exception as exc:
            status = "FAIL"
            measures["cleanup_failure"] = p.clean_text(str(exc))[:1500]
    final = source_hash()
    if final != initial:
        status = "FAIL"
        measures["source_failure"] = "source changed during verification"
    summary = dict(
        case=case,
        status=status,
        project=p.name,
        git_base=git_base,
        worker_image_ids=worker_image_ids,
        invocation_source_hash=invocation_hash,
        source_hash=initial,
        source_hash_after=final,
        source_unchanged=final == initial,
        evidence="F/I",
        **measures,
    )
    receipt = p.evidence / "cnemc-result.json"
    receipt.write_text(json.dumps(summary, indent=2) + "\n")
    errors = [str(value) for key, value in measures.items() if key.endswith("failure")]
    print(status, case, receipt, *errors, flush=True)
    return status == "PASS"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", action="append", choices=["chain", "capacity", "partial", "quota", "pg"]
    )
    cases = parser.parse_args().case or ["chain", "capacity", "partial", "quota", "pg"]
    if not __debug__:
        sys.exit("Assertions must be enabled")
    invocation_hash = source_hash()
    sys.exit(0 if all([run_case(case, invocation_hash) for case in cases]) else 1)
