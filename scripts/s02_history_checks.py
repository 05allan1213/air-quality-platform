"""Real H file, database, Worker and Session reconciliation for S02."""

import csv
import hashlib
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit

from s01_runtime import Client, wait_for
from s02_runtime import VerificationError
from verify_s01 import http_case

PROFILE = "uci501-edges-v1"
POST = "/api/v1/admin/history-imports/"
CURRENT = "/api/v1/observations/current/"
EVIDENCE = "/api/v1/admin/observation-evidence/"
ARCHIVE_SHA = "d1b9261c54132f04c374f762f1e5e512af19f95c95fd6bfa1e8ac7e927e3b0b8"
WINDOW = "hourly_label_unspecified"
HEADERS = (
    "No,year,month,day,hour,PM2.5,PM10,SO2,NO2,CO,O3,TEMP,PRES,DEWP,RAIN,wd,WSPM,station".split(",")
)
EXAMPLES = [
    ("Aotizhongxin", 1, "2013-02-28T16:00:00Z", "300", "0.300"),
    ("Aotizhongxin", 35017, "2017-02-26T16:00:00Z", "100", "0.100"),
    ("Changping", 1, "2013-02-28T16:00:00Z", "300", "0.300"),
    ("Changping", 35017, "2017-02-26T16:00:00Z", "1100", "1.100"),
]


def seed_accounts(p):
    p.shell(f"""
from django.contrib.auth.models import Permission, User
for name in ('reader', 'operator', 'supervisor'):
    u = User.objects.create_user(name, password={p.password!r}, is_superuser=name=='supervisor', is_staff=name!='reader')
    if name == 'operator':
        u.user_permissions.set(Permission.objects.filter(content_type__app_label='observations', codename__in=['import_history','review_observation','manage_grants']))
""")
    return tuple(
        Client(p.base).login(name, p.password) for name in ("reader", "operator", "supervisor")
    )


def source_file(p, archive):
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    p.equal(
        "V08",
        "approved UCI archive bytes and fingerprint",
        [archive.stat().st_size, actual],
        [7959991, ARCHIVE_SHA],
    )
    counts = {}
    samples = {}
    points = {(name, ordinal) for name, ordinal, *_ in EXAMPLES} | {("Changping", 28)}
    with zipfile.ZipFile(archive) as source:
        p.equal(
            "V08",
            "source directory/member count",
            sum(info.is_dir() for info in source.infolist()),
            1,
        )
        for member in source.infolist():
            if member.is_dir():
                continue
            site = (
                member.filename.rsplit("/", 1)[-1]
                .removeprefix("PRSA_Data_")
                .removesuffix("_20130301-20170228.csv")
            )
            assert site not in counts
            with source.open(member) as content:
                data = csv.DictReader(io.TextIOWrapper(content, encoding="utf-8", newline=""))
                assert data.fieldnames == HEADERS
                count = 0
                first = last = None
                seen = set()
                for count, row in enumerate(data, 1):
                    assert row["station"] == site and int(row["No"]) == count
                    label = tuple(int(row[name]) for name in HEADERS[1:5])
                    assert label not in seen
                    seen.add(label)
                    first = first or label
                    last = label
                    if (site, count) in points:
                        samples[f"{site}/{count}"] = row["CO"]
                assert [count, first, last] == [35064, (2013, 3, 1, 0), (2017, 2, 28, 23)]
                counts[site] = count
    p.equal(
        "V08",
        "12 station CSVs, each with 35,064 unique hourly labels and verified first/last labels",
        len(counts),
        12,
    )
    p.record("V08", "enumerated station rows", counts)
    p.equal(
        "V08",
        "licensed source CO evidence",
        samples,
        {
            **{f"{site}/{ordinal}": raw for site, ordinal, _, raw, _ in EXAMPLES},
            "Changping/28": "NA",
        },
    )


def register_uci(p):
    p.manage("s02_register_uci", "--actor", "operator", service="worker")
    product = int(
        p.sql(
            "SELECT id FROM observations_product WHERE code='uci501' AND family='H' AND NOT synthetic"
        )
    )
    epochs = dict(
        (name, int(epoch))
        for name, epoch in (
            row.split("|")
            for row in p.sql("""
                SELECT s.name,e.id FROM observations_siteepoch e JOIN observations_station s ON e.station_id=s.id
                JOIN observations_source src ON s.source_id=src.id WHERE src.namespace='uci.501'
            """).splitlines()
        )
    )
    assert set(epochs) == {"Aotizhongxin", "Changping"}
    return product, epochs


def submit(p, operator, stag, section, label):
    payload = {"profile": PROFILE, "stage_id": stag, "reason": "historical_verification"}
    body = http_case(p, section, label, operator.call(POST, payload), 202)
    return str(uuid.UUID(body["attempt_id"]))


def stage(p, archive):
    with archive.open("rb") as stream:
        return p.stage(stream)


def import_done(p, section, attempt, expected):
    def probe():
        result = json.loads(
            p.sql(f"""
            SELECT jsonb_build_object('id', a.id, 'state', a.state, 'dispatch',a.dispatch,
                              'error_code',a.error_code,'batch_id',a.batch_id,'stage',s.state,
                              'attempt_times_ordered',a.created_at<=a.started_at AND a.started_at<=a.finished_at,
                              'deliveries',a.deliveries)
            FROM observations_importattempt a JOIN observations_stagedinput s ON a.stage_id=s.id
            WHERE a.id='{str(uuid.UUID(attempt))}'
        """)
        )
        return result if result["state"] != "pending" and result["stage"] == "cleared" else None

    result = wait_for("terminal history attempt and cleared staging descriptor", probe, seconds=120)
    p.equal(section, "history terminal state", result["state"], expected)
    p.record(section, "history attempt (fixed metadata only)", result)
    return result


def counts(p, product):
    product = int(product)
    return list(
        map(
            int,
            p.sql(f"""
        SELECT (SELECT count(*) FROM observations_batch WHERE product_id={product}),
               (SELECT count(*) FROM observations_stationobservation WHERE product_id={product}),
               (SELECT count(*) FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id WHERE o.product_id={product}),
               (SELECT count(*) FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id WHERE o.product_id={product} AND r.is_current),
               (SELECT count(*) FROM observations_receipt r JOIN observations_batch b ON r.batch_id=b.id WHERE b.product_id={product}),
               (SELECT count(*) FROM observations_qualityissue WHERE product_id={product}),
               (SELECT count(*) FROM observations_change WHERE product_id={product} AND station_key_id IS NOT NULL)
    """).split("|"),
        )
    )


def query(product, site, at, **overrides):
    selector = {
        "product": product,
        "site_epoch": site,
        "indicator": "CO",
        "report_kind": "hourly",
        "window_definition": WINDOW,
        "standard_series": "not_applicable",
        "observed_at": at,
    }
    return CURRENT + "?" + urlencode(selector | overrides)


def assert_no_stage_files(p, section):
    p.equal(
        section,
        "no live staged object for these attempts",
        p.sql("""
        SELECT count(*) FROM observations_stagedinput s JOIN observations_product p ON p.id=s.product_id
        WHERE p.code='uci501' AND s.state <> 'cleared'
    """),
        "0",
    )


@contextmanager
def fail_first_observation(p):
    p.sql("""
        CREATE FUNCTION s02_verifier_rollback() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 's02_verifier_injected_failure'; END $$;
        CREATE TRIGGER s02_verifier_rollback AFTER INSERT ON observations_stationrevision
        FOR EACH ROW EXECUTE FUNCTION s02_verifier_rollback();
    """)
    try:
        yield
    finally:
        p.sql("""
            DROP TRIGGER s02_verifier_rollback ON observations_stationrevision;
            DROP FUNCTION s02_verifier_rollback();
        """)


def check_upload_denials(p, archive, reader, operator, product):
    before = p.sql("SELECT count(*) FROM observations_importattempt")
    stag = stage(p, archive)
    pending = {"profile": PROFILE, "stage_id": stag, "reason": "historical_verification"}
    for section, label, client, payload, token, status, code in [
        ("V02", "ordinary user", reader, pending, True, 403, "permission_denied"),
        ("V12", "submit missing CSRF", operator, pending, False, 403, "csrf_failed"),
        (
            "V10",
            "forged external profile",
            operator,
            pending | {"profile": "CNEMC"},
            True,
            400,
            None,
        ),
        (
            "V10",
            "attempted extra file path",
            operator,
            pending | {"path": "file.zip"},
            True,
            400,
            None,
        ),
    ]:
        reply = client.call(POST, payload, token=token)
        if code:
            http_case(p, section, label, reply, status, code)
        else:
            http_case(p, section, label, reply, status, "invalid_request")
        p.equal(
            section,
            label + " refuses to create attempt",
            p.sql("SELECT count(*) FROM observations_importattempt"),
            before,
        )
    p.equal("V10", "no H data after rejection", counts(p, product)[:5], [0, 0, 0, 0, 0])
    p.equal(
        "V10",
        "no queue message after rejection",
        p.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s02.history"),
        "0",
    )
    return stag


def check_wrong_archive(p, product):
    # Empty /dev/null is sufficient to fail the transport archive check. Parser shape
    # cases feed complete synthetic material directly to its test-only fixture.
    with open("/dev/null", "rb") as source:
        try:
            p.stage(source)
        except VerificationError as exc:
            p.equal(
                "V10",
                "invalid archive staging rejected",
                [exc.exit_code, str(exc)],
                [1, "exit 1: archive_fingerprint_mismatch"],
            )
        else:
            raise AssertionError("invalid archive unexpectedly staged")
    p.equal(
        "V10", "no successful storage from invalid archive", counts(p, product)[:5], [0, 0, 0, 0, 0]
    )
    assert_no_stage_files(p, "V10")


def first_import(p, archive, product, reader, operator):
    check_wrong_archive(p, product)
    stag = check_upload_denials(p, archive, reader, operator, product)
    with fail_first_observation(p):
        attempt = submit(p, operator, stag, "V11", "submit before injected write failure")
        failed = import_done(p, "V11", attempt, "failed")
        p.equal("V11", "failed attempt has no successful batch", failed["batch_id"], None)
        p.equal(
            "V11",
            "separate SQL session sees no partial batch, key, revision, receipt or scoped change",
            counts(p, product)[:5],
            [0, 0, 0, 0, 0],
        )
    p.equal("V11", "attempt has no surviving formal observation change", counts(p, product)[-1], 0)
    assert_no_stage_files(p, "V11")
    stages = [stage(p, archive), stage(p, archive)]
    # Each request has its own session transport and stage. Both authenticate before
    # the race, so a login or cookie change cannot serialize the submissions.
    another = Client(p.base).login("operator", p.password)
    payloads = [
        {"profile": PROFILE, "stage_id": key, "reason": "historical_verification"} for key in stages
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [
            pool.submit(c.call, POST, payload)
            for c, payload in zip((operator, another), payloads, strict=True)
        ]
        statuses = [call.result() for call in calls]
    attempts = [
        str(uuid.UUID(http_case(p, "V09", "concurrent H submission", result, 202)["attempt_id"]))
        for result in statuses
    ]
    assert attempts[0] != attempts[1]

    def states():
        value = p.sql(
            f"SELECT string_agg(state,',' ORDER BY state) FROM observations_importattempt WHERE id IN ('{attempts[0]}','{attempts[1]}')"
        )
        return value if "pending" not in value else None

    terminal = wait_for("both H imports terminal", states, seconds=120)
    p.equal("V09", "one H success, one duplicate", terminal, "duplicate,succeeded")
    outcome = []
    for attempt in attempts:
        expected = p.sql(f"SELECT state FROM observations_importattempt WHERE id='{attempt}'")
        outcome.append(import_done(p, "V09", attempt, expected))
    batch = outcome[0]["batch_id"]
    assert isinstance(batch, int)
    p.equal(
        "V09",
        "same confirmed batch association",
        [entry["batch_id"] for entry in outcome],
        [batch, batch],
    )
    p.equal(
        "V11",
        "retry after dropping trigger commits exactly once",
        counts(p, product)[:5],
        [1, 1152, 1152, 1152, 1152],
    )
    assert_no_stage_files(p, "V09")
    return batch, next(entry for entry in outcome if entry["state"] == "succeeded")


def revision_value(response):
    assert response["count"] == 1
    (value,) = response["results"]
    return value


def verify_samples(p, reader, product, epochs, batch, attempt):
    p.equal(
        "V08",
        "H keys, confirmed revisions, current values, and lineage",
        counts(p, product)[:5],
        [1, 1152, 1152, 1152, 1152],
    )
    p.equal(
        "V08",
        "H city and AQI count",
        p.sql(f"""
        SELECT (SELECT count(*) FROM observations_cityobservation WHERE product_id={product}),
               (SELECT count(*) FROM observations_stationobservation WHERE product_id={product} AND indicator='AQI')
    """),
        "0|0",
    )
    p.equal(
        "V08",
        "H timestamps: process start <= source read <= process end, no fabricated source ordering",
        p.sql(f"""
        SELECT (a.started_at <= b.fetched_at AND b.fetched_at <= a.finished_at),
               (SELECT count(*) FROM observations_stationobservation WHERE product_id={product} AND watermark IS NOT NULL)
        FROM observations_batch b JOIN observations_importattempt a ON a.batch_id=b.id
        WHERE b.id={batch} AND a.id='{attempt["id"]}'
    """),
        "t|0",
    )
    p.record(
        "V08",
        "quality distribution",
        p.sql(f"""
        SELECT r.quality,count(*) FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id
        WHERE o.product_id={product} AND r.is_current GROUP BY r.quality ORDER BY r.quality
    """).splitlines(),
    )
    p.equal(
        "V08",
        "per station/window hourly labels, missing, CO missing and non-null values",
        p.sql(f"""
        SELECT s.name,extract(year FROM o.source_local_date)::int,count(DISTINCT o.observed_at_utc),
               count(*) FILTER (WHERE r.quality='missing'),
               count(*) FILTER (WHERE r.quality='missing' AND o.indicator='CO'),count(r.value)
        FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id
        JOIN observations_siteepoch e ON o.site_epoch_id=e.id JOIN observations_station s ON e.station_id=s.id
        WHERE o.product_id={product} AND r.is_current GROUP BY s.name,2 ORDER BY s.name,2
    """).splitlines(),
        [
            "Aotizhongxin|2013|48|0|0|288",
            "Aotizhongxin|2017|48|15|3|273",
            "Changping|2013|48|4|1|284",
            "Changping|2017|48|3|1|285",
        ],
    )
    for name, ordinal, at, raw, value in EXAMPLES:
        data = json.loads(
            p.sql(f"""
            SELECT jsonb_build_object('raw',r.raw_value,'value',r.value::text,'unit',r.unit,'raw_unit',r.raw_unit,'quality',r.quality,
                                     'station',o.object_code,'provenance',r.provenance,'key',o.id)
            FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id
            WHERE o.product_id={product} AND o.site_epoch_id={epochs[name]} AND o.indicator='CO'
                  AND o.observed_at_utc='{at}' AND r.is_current
        """)
        )
        label = f"{name}/No={ordinal}"
        p.equal(
            "V08",
            label + " timezone and effective window evidence",
            {
                key: data["provenance"][key]
                for key in ("source_timezone", "timezone_basis", "window_start", "window_end")
            },
            {
                "source_timezone": "Asia/Shanghai",
                "timezone_basis": "timezone_assumed",
                "window_start": None,
                "window_end": None,
            },
        )
        p.equal(
            "V08",
            label + " PG normalized and raw",
            [
                data["station"],
                data["raw"],
                str(Decimal(data["value"]).quantize(Decimal(".001"))),
                data["unit"],
                data["raw_unit"],
                data["quality"],
            ],
            [name, raw, value, "mg/m³", "µg/m³", "valid"],
        )
        result = revision_value(
            http_case(
                p,
                "V08",
                label + " authorized exact read",
                reader.call(query(product, epochs[name], at)),
                200,
            )
        )
        p.equal(
            "V08",
            label + " HTTP normalized",
            [
                str(Decimal(result["value"]).quantize(Decimal(".001"))),
                result["unit"],
                result["quality"],
                result["synthetic"],
            ],
            [value, "mg/m³", "valid", False],
        )
        assert all(term in result["attribution"] for term in ("10.24432/C5RK5G", "CC BY 4.0"))
        p.record(
            "V08",
            label + " authorized provenance and attribution",
            {"pg": data["provenance"], "attribution": result["attribution"]},
        )
    p.shell(f"""
from datetime import date
from django.contrib.auth.models import User
from observations.policy import OPERATIONS, register_grant
from observations.uci_catalog import ATTRIBUTION, LICENSE
u=User.objects.get(username='operator')
for op in ('retain_normalized','display'):
    register_grant(u.pk,{product},operation=op,audience=OPERATIONS[op],geography='beijing',object_code='Changping',indicator='CO',data_from=date(2015,3,1),data_until=date(2015,3,2),fields=['normalized','provenance'],terms=LICENSE,terms_version='CC BY 4.0 / exact empty-window verification',attribution=ATTRIBUTION)
""")
    missing_at = "2013-03-01T19:00:00Z"
    p.equal(
        "V08",
        "Changping No=28 exists as missing, no value",
        p.sql(f"""
        SELECT r.quality,r.value IS NULL,r.raw_value FROM observations_stationrevision r
        JOIN observations_stationobservation o ON r.key_id=o.id WHERE o.product_id={product}
        AND o.site_epoch_id={epochs["Changping"]} AND o.indicator='CO' AND o.observed_at_utc='{missing_at}' AND r.is_current
    """),
        "missing|t|NA",
    )
    for label, at in [
        ("source missing", missing_at),
        ("middle empty hour", "2015-02-28T16:00:00Z"),
    ]:
        result = http_case(
            p, "V08", label, reader.call(query(product, epochs["Changping"], at)), 200
        )
        p.equal(
            "V08",
            label + " does not substitute data",
            [result["count"], result["results"]],
            [0, []],
        )
        p.equal(
            "V08",
            label + " reason",
            result["reason"],
            "missing" if label == "source missing" else "no_observation_at_time",
        )


def read_boundaries(p, reader, operator, product, epochs, batch):
    site = epochs["Aotizhongxin"]
    at = EXAMPLES[0][2]
    authorized = query(product, site, at)
    evidence = EVIDENCE + "?" + urlencode({"product": product, "batch": batch})
    http_case(
        p, "V12", "anonymous current", Client(p.base).call(authorized), 403, "not_authenticated"
    )
    http_case(
        p,
        "V12",
        "ordinary user has no governance permission",
        reader.call(evidence),
        403,
        "permission_denied",
    )
    body = http_case(p, "V12", "operator governance batch view", operator.call(evidence), 200)
    p.equal("V12", "authorized evidence page bounded at 100", len(body["receipts"]), 100)
    assert body["next_after"] is not None and not body["synthetic"]
    for label, url in [
        ("duplicate selector", authorized + "&indicator=CO"),
        ("wildcard selector", query(product, site, at, indicator="*")),
        ("missing timezone offset", query(product, site, at.removesuffix("Z"))),
    ]:
        p.equal("V12", label + " HTTP status", reader.call(url)[0], 400)
    for label, url in [
        ("unavailable product", query(99999999, site, at)),
        ("formal H in synthetic space", query(product, site, at, scope="synthetic")),
        ("unavailable site", query(product, 99999999, at)),
    ]:
        http_case(p, "V12", label, reader.call(url), 403, "use_denied")


def revoke_grant(p, grant):
    return p.manage(
        "s02_revoke_grant",
        "--actor",
        "operator",
        "--grant",
        str(grant),
        "--reason",
        "verification",
        service="worker",
    )


def display_withdrawal(p, clients, product, epochs):
    address = query(product, epochs["Aotizhongxin"], EXAMPLES[0][2])
    for label, client in zip(("reader", "operator", "superuser"), clients, strict=True):
        http_case(p, "V13", label + " before display withdrawal", client.call(address), 200)
    before = counts(p, product)
    version = int(p.sql(f"SELECT rights_version FROM observations_product WHERE id={product}"))
    p.shell(f"""
from datetime import date
from django.contrib.auth.models import User
from observations.policy import register_grant
from observations.uci_catalog import ATTRIBUTION, LICENSE
u=User.objects.get(username='operator')
register_grant(u.pk,{product},operation='analyze',audience='operator',geography='beijing',object_code='Aotizhongxin',indicator='CO',data_from=date(2013,3,1),data_until=date(2013,3,2),fields=['normalized','provenance'],terms=LICENSE,terms_version='CC BY 4.0 / S02 internal verification',attribution=ATTRIBUTION)
""")
    targeted = int(
        p.sql(
            f"SELECT id FROM observations_usegrant WHERE product_id={product} AND operation='display' AND object_code='Aotizhongxin' AND data_from='2013-03-01'"
        )
    )
    revoke_grant(p, targeted)
    for label, client in zip(("reader", "operator", "superuser"), clients, strict=True):
        http_case(
            p,
            "V13",
            label + " with original Session after withdrawal",
            client.call(address),
            403,
            "use_denied",
        )
    p.equal("V13", "display withdrawal preserves retained H ledger", counts(p, product), before)
    revision = int(p.sql(f"SELECT rights_version FROM observations_product WHERE id={product}"))
    assert revision > version
    p.record("V13", "product authorization version before/after", [version, revision])

    selector = {key: values[0] for key, values in parse_qs(urlsplit(address).query).items()}
    p.equal(
        "V13",
        "independent internal analysis stays authorized",
        p.shell(f"""
from django.contrib.auth.models import User
from observations.queries import read_current
u=User.objects.get(username='operator')
print(read_current(u.pk,{selector!r},'analyze')['count'])
"""),
        "1",
    )


def worker_event(p, section, attempt, state):
    key = str(uuid.UUID(attempt))

    def probe():
        text = p.compose("logs", "--no-color", "worker")
        received = []
        result = None
        for line in text.splitlines():
            if f"observations.import_history[{key}] received" in line:
                received.append(line)
            if received and line.endswith(f"history_end attempt={key} state={state}"):
                result = [received[-1], line]
        return result

    p.record(section, "Worker receipt", wait_for("received and terminal Worker receipt", probe))


def redelivery(p, product, prior, section):
    attempt = str(uuid.UUID(prior["id"]))
    deliveries = int(
        p.sql(f"SELECT deliveries FROM observations_importattempt WHERE id='{attempt}'")
    )
    before = counts(p, product)
    if section == "V09":
        p.equal(
            section,
            "operator redelivery is a terminal no-op",
            p.manage("s02_redeliver", "--actor", "operator", "--attempt", attempt),
            f"{attempt} {prior['state']}",
        )
        p.equal(
            section,
            "terminal command leaves delivery count unchanged",
            int(p.sql(f"SELECT deliveries FROM observations_importattempt WHERE id='{attempt}'")),
            deliveries,
        )
    p.shell(f"""
from observations.tasks import process_history
process_history.apply_async(args=[{attempt!r}],task_id={attempt!r},retry=False)
""")
    wait_for(
        "redelivery is acknowledged in PostgreSQL",
        lambda: (
            int(p.sql(f"SELECT deliveries FROM observations_importattempt WHERE id='{attempt}'"))
            > deliveries
        ),
    )
    repeated = import_done(p, section, attempt, prior["state"])
    p.equal(
        section, "redelivery increments only delivery count", repeated["deliveries"], deliveries + 1
    )
    p.equal(
        section, "redelivery does not restore data or duplicate batch", counts(p, product), before
    )
    worker_event(p, section, attempt, "redelivery")


def broker_recovery(p, archive, operator, product, batch):
    before = counts(p, product)
    stage_id = stage(p, archive)
    p.compose("stop", "redis")
    try:
        response = operator.call(
            POST, {"profile": PROFILE, "stage_id": stage_id, "reason": "historical_verification"}
        )
        body = http_case(
            p, "V16", "Broker offline at submission", response, 503, "dispatch_unconfirmed"
        )
        attempt = str(uuid.UUID(body["attempt_id"]))
        p.equal(
            "V16",
            "no delivery for pending attempt",
            p.sql(
                f"SELECT state,dispatch,deliveries FROM observations_importattempt WHERE id='{attempt}'"
            ),
            "pending|unconfirmed|0",
        )
        p.equal("V16", "no writes without Worker delivery", counts(p, product), before)
    finally:
        p.start_redis()
    p.equal(
        "V16",
        "recovered broker accepts original attempt UUID",
        p.manage("s02_redeliver", "--actor", "operator", "--attempt", attempt),
        f"{attempt} sent",
    )
    finished = import_done(p, "V16", attempt, "duplicate")
    p.equal(
        "V16",
        "recovered attempt references the original authorized batch",
        [finished["batch_id"], finished["deliveries"]],
        [batch, 1],
    )
    p.equal("V16", "recovery preserves observation and lineage counts", counts(p, product), before)
    worker_event(p, "V16", attempt, "duplicate")


def revoke_while_queued(p, archive, operator, product, succeeded):
    permission = (
        "Permission.objects.get(content_type__app_label='observations',codename='import_history')"
    )
    base = "from django.contrib.auth.models import Permission,User\nu=User.objects.get(username='operator')\n"

    # Pause the existing Worker so the application-owned tmpfs survives. Each
    # revoked operation is checked independently before the domain consume.
    def queued(label, denial, code, restore=None, *, redeliver=False):
        stage_id = stage(p, archive)
        before = counts(p, product)
        p.compose("pause", "worker")
        try:
            attempt = submit(p, operator, stage_id, "V16", "queued before " + label)
            p.equal(
                "V16",
                label + " pending PG attempt",
                p.sql(
                    f"SELECT state || '|' || dispatch FROM observations_importattempt WHERE id='{attempt}'"
                ),
                "pending|sent",
            )
            if redeliver:
                p.equal(
                    "V16",
                    "operator republishes the same pending attempt",
                    p.manage("s02_redeliver", "--actor", "operator", "--attempt", attempt),
                    f"{attempt} sent",
                )
            p.shell(base + denial)
        finally:
            p.compose("unpause", "worker")
        rejected = import_done(p, "V16", attempt, "rejected")
        p.equal("V16", label + " fixed Worker rejection code", rejected["error_code"], code)
        p.equal("V16", label + " created no observations or receipts", counts(p, product), before)
        worker_event(p, "V16", attempt, "rejected")
        if redeliver:
            worker_event(p, "V16", attempt, "redelivery")
            p.equal(
                "V16",
                "original and republished deliveries share the rejected attempt",
                p.sql(f"SELECT deliveries FROM observations_importattempt WHERE id='{attempt}'"),
                "2",
            )
        if restore:
            p.shell(base + restore)

    queued(
        "role withdrawal",
        "u.user_permissions.remove(" + permission + ")",
        "permission_denied",
        "u.user_permissions.add(" + permission + ")",
        redeliver=True,
    )
    source = f"""
from observations.models import UseGrant
from observations.policy import register_grant,revoke_grant
old=UseGrant.objects.get(product_id={product},operation='acquire_file',revoked_at__isnull=True)
"""
    queued(
        "source acquisition withdrawal",
        source + "revoke_grant(u.pk,old.pk,'verification withdrawal of platform acquisition use')",
        "use_denied",
        f"""
from observations.models import UseGrant
from observations.policy import register_grant
old=UseGrant.objects.filter(product_id={product},operation='acquire_file').latest('id')
register_grant(u.pk,{product},operation=old.operation,audience=old.audience,geography=old.geography,object_code=old.object_code,indicator=old.indicator,data_from=old.data_from,data_until=old.data_until,fields=old.fields,terms=old.terms,terms_version=old.terms_version,attribution=old.attribution)
""",
    )
    stage_id = stage(p, archive)
    p.compose("pause", "worker")
    try:
        attempt = submit(
            p, operator, stage_id, "V16", "queued before applicable retention grant withdrawal"
        )
        p.equal(
            "V16",
            "retention race starts with a pending attempt",
            p.sql(f"SELECT state FROM observations_importattempt WHERE id='{attempt}'"),
            "pending",
        )
        grant = int(
            p.sql(
                f"SELECT id FROM observations_usegrant WHERE product_id={product} AND operation='retain_normalized' AND object_code='Aotizhongxin' AND data_from='2013-03-01'"
            )
        )
        p.shell(
            base
            + f"from observations.policy import revoke_grant\nrevoke_grant(u.pk,{grant},'verification withdrawal of platform window')\n"
        )
    finally:
        p.compose("unpause", "worker")
    rejected = import_done(p, "V16", attempt, "rejected")
    p.equal(
        "V16",
        "retention race rejects queued attempt before loss of authorization",
        [rejected["error_code"], rejected["batch_id"]],
        ["use_denied", None],
    )
    worker_event(p, "V16", attempt, "rejected")
    p.manage("s02_dispose_due", "--product", str(product), service="worker")
    p.equal(
        "V16",
        "after exact H window withdrawal, three other windows preserve data",
        counts(p, product)[:5],
        [1, 864, 864, 864, 864],
    )
    redelivery(p, product, succeeded, "V16")
    assert_no_stage_files(p, "V16")
