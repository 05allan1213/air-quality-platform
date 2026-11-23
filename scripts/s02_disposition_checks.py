"""Synthetic observation, withdrawal, expiry and recovery scenarios."""

import json
from contextlib import contextmanager

from s01_runtime import wait_for
from s02_history_checks import counts, query, revision_value
from s02_runtime import VerificationError
from verify_s01 import http_case

HOUR = "2024-01-01T00:00:00Z"
F_SETUP = """
import json,sys
sys.path.insert(0, '/app/tests')
from django.contrib.auth.models import User
from s02_support import ingest_fixture as ingest_batch
from s02_support import allow, candidate, create_product, create_site
u=User.objects.get(username='operator')
"""


def worker_shell(p, code):
    return p.compose(
        "exec",
        "-T",
        "worker",
        "python",
        "backend/manage.py",
        "shell",
        "--verbosity",
        "0",
        stdin=code,
    )


def t06_watermark(p, reader):
    product, site = map(
        int,
        p.shell(
            F_SETUP
            + """
product=create_product('t06-watermark')
site=create_site(product)
for op in ('display','process_local','retain_normalized'):
    allow(u,product,op)
print(product.pk,site.pk)
"""
        ).split(),
    )
    path = query(product, site, HOUR, scope="synthetic")
    first_version = None
    for token, incoming, watermark, outcome in [
        (1, "10", 1, "first"),
        (3, "10", 3, "watermark_advanced"),
        (2, "20", 3, "late"),
    ]:
        label = f"v{token}={incoming}"
        batch = int(
            p.shell(
                F_SETUP
                + f"""
from observations.models import Product
from observations.revisions import fixture_ticket, ingest_batch
product=Product.objects.get(pk={product})
batch, created=ingest_batch(u.pk, product.pk, fixture_ticket(product.pk), product.contract_version,
    [candidate(product,{token},{incoming!r})])
assert created
print(batch.pk)
"""
            )
        )
        actual = p.sql(f"""
            SELECT k.watermark, v.value, v.revision_no, r.outcome, r.source_token,
                   (SELECT count(*) FROM observations_stationobservation WHERE product_id={product}),
                   (SELECT count(*) FROM observations_stationrevision WHERE key_id=k.id)
            FROM observations_receipt r JOIN observations_stationrevision v ON v.id=r.station_revision_id
            JOIN observations_stationobservation k ON k.id=v.key_id
            WHERE r.batch_id={batch} AND v.is_current
        """)
        p.equal(
            "V06",
            label + " independent PostgreSQL reconciliation",
            actual,
            f"{watermark}|10.000000|1|{outcome}|{token}|1|1",
        )
        row = revision_value(
            http_case(p, "V06", label + " authorized exact F read", reader.call(path), 200)
        )
        p.equal(
            "V06",
            label + " value and synthetic flag",
            [row["value"], row["synthetic"]],
            ["10.000000", True],
        )
        if first_version is None:
            first_version = row["input_version"]
        p.equal(
            "V06",
            label + " exact-read content and qualification version",
            row["input_version"],
            first_version,
        )


def assert_clean_path(p, stage, expected):
    actual = worker_shell(
        p,
        f"""
from observations.disposition import private_dir
print((private_dir()/{stage!r}).exists())
""",
    )
    p.equal("V14", "stage path existence", actual, str(expected))


def disposition_jobs(p, product):
    return p.sql(f"""
        SELECT count(*) FILTER (WHERE completed_at IS NULL AND due_at <= clock_timestamp()),
               count(*) FILTER (WHERE completed_at IS NOT NULL),
               count(*) FILTER (WHERE error_code='cleanup_failed')
        FROM observations_dispositionjob WHERE product_id={product}
    """)


def candidate_counts(p, product):
    return p.sql(f"""
        SELECT o.object_code,o.indicator,count(*),count(*) FILTER (WHERE r.is_current),
               count(*) FILTER (WHERE r.candidate),count(r.raw_value),count(r.raw_unit)
        FROM observations_stationrevision r JOIN observations_stationobservation o ON r.key_id=o.id
        WHERE o.product_id={product} GROUP BY o.object_code,o.indicator ORDER BY 1,2
    """).splitlines()


def raw_and_mixed_batch_withdrawal(p, reader, operator):
    # F-only stage emulates cleanup failure: a directory at the expiring UUID cannot
    # be removed by the file-only cleanup, and it contains only an injected marker.
    data = json.loads(
        worker_shell(
            p,
            F_SETUP
            + """
from datetime import timedelta
from observations.controlled_storage import register_volume
from observations.disposition import PACKAGE_SCOPE, private_dir
from observations.models import StagedInput
from observations.policy import clock, register_grant
product=create_product('retain-fixture')
a=create_site(product,code='00A',geography='A')
b=create_site(product,code='00B',geography='B')
for op in ('display','process_local','review','analyze'):
    allow(u,product,op)
broad=allow(u,product,geography='A')
allow(u,product,geography='A',object_code='00A',indicator='CO',fields=['normalized','provenance'])
allow(u,product,geography='B')
raw=register_grant(u.pk,product.pk,operation='retain_raw',audience='platform',
    geography=PACKAGE_SCOPE.geography,data_from=PACKAGE_SCOPE.start,data_until=PACKAGE_SCOPE.end,
    terms='F test fixture',terms_version='1',fields=['archive'])
stage=StagedInput.objects.create(product=product,owner=u,expires_at=clock()+timedelta(minutes=5),
    state='ready',volume_generation=register_volume(private_dir()))
path=private_dir()/str(stage.pk)
path.mkdir(mode=0o700)
(path/'injected').write_bytes(b'F')
for version, label, pm in ((1,'first','120'),(2,'newer','150'),(2,'conflict','151')):
    rows=[candidate(product,version,pm,code='00A',indicator='PM2.5',unit='µg/m³')]
    if label=='first':
        rows.extend([candidate(product,1,'10',code='00A'),candidate(product,1,'20',code='00B')])
    ingest_batch(u.pk,product.pk,'F-'+label,'f-acceptance-v1',rows)
print(json.dumps({'product':product.pk,'a':a.pk,'b':b.pk,'withdraw':broad.pk,'raw':raw.pk,'stage':str(stage.pk)}))
""",
        )
    )
    product = data["product"]
    a_url = query(product, data["a"], HOUR, scope="synthetic")
    b_url = query(product, data["b"], HOUR, scope="synthetic")
    pm_url = query(product, data["a"], HOUR, scope="synthetic", indicator="PM2.5")
    p.equal(
        "V14",
        "F before destruction: current, history, and dispute preserved",
        candidate_counts(p, product),
        [
            "00A|CO|1|1|0|1|1",
            "00A|PM2.5|3|1|1|3|3",
            "00B|CO|1|1|0|1|1",
        ],
    )
    assert_clean_path(p, data["stage"], True)
    row = revision_value(
        http_case(p, "V12", "authorized synthetic selection", reader.call(a_url), 200)
    )
    p.equal("V12", "synthetic flag", row["synthetic"], True)
    http_case(
        p,
        "V12",
        "F unavailable in real space",
        reader.call(a_url.replace("&scope=synthetic", "")),
        403,
        "use_denied",
    )
    evidence = p.sql(
        f"SELECT id FROM observations_stationobservation WHERE product_id={product} AND object_code='00A' AND indicator='PM2.5'"
    )
    governed = f"/api/v1/admin/observation-evidence/?product={product}&station_key={evidence}&scope=synthetic"
    review = http_case(
        p, "V07", "authorized synthetic dispute evidence", operator.call(governed), 200
    )
    assert review, "missing dispute evidence"
    conflicted = http_case(
        p,
        "V07",
        "ordinary read excludes current under unresolved dispute",
        reader.call(pm_url),
        200,
    )
    p.equal("V07", "dispute returns no value", conflicted["count"], 0)

    with stop_beat(p):
        # The grant change is committed separately from the disposition attempt. The
        # deliberately failed filesystem operation must leave restartable PG work.
        worker_shell(
            p,
            f"""
from django.contrib.auth.models import User
from observations.policy import revoke_grant
u=User.objects.get(username='operator')
revoke_grant(u.pk,{data["withdraw"]},'scope withdrawn for F verification')
revoke_grant(u.pk,{data["raw"]},'temporary F evidence withdrawn')
""",
        )
        try:
            p.manage("s02_dispose_due", "--product", str(product), service="worker")
        except VerificationError as exc:
            p.equal("V14", "injected filesystem failure exit", exc.exit_code, 1)
        else:
            raise AssertionError(
                "cleanup unexpectedly succeeded with a directory in place of a file"
            )
        p.equal(
            "V14",
            "pending due/complete/failed after filesystem failure",
            disposition_jobs(p, product),
            "2|0|2",
        )
        assert_clean_path(p, data["stage"], True)
        http_case(
            p, "V14", "display denied while cleanup pending", reader.call(pm_url), 403, "use_denied"
        )
        http_case(
            p,
            "V14",
            "independent fallback keeps normalized F while cleanup pending",
            reader.call(a_url),
            200,
        )
        http_case(
            p,
            "V14",
            "unaffected F scope remains readable while cleanup pending",
            reader.call(b_url),
            200,
        )
        worker_shell(
            p,
            f"""
from observations.disposition import private_dir
path=private_dir()/{data["stage"]!r}
(path/'injected').unlink()
path.rmdir()
path.write_bytes(b'F')
""",
        )
        for label in ("retry cleanup", "idempotent second cleanup"):
            p.manage("s02_dispose_due", "--product", str(product), service="worker")
            p.equal("V14", label + " due/complete/failed", disposition_jobs(p, product), "0|2|0")
            assert_clean_path(p, data["stage"], False)
        p.equal(
            "V14",
            "stage metadata settled only after physical cleanup",
            p.sql(f"SELECT state FROM observations_stagedinput WHERE id='{data['stage']}'"),
            "cleared",
        )

    p.equal(
        "V14",
        "all revisions of revoked F scope gone, independent rights intact",
        candidate_counts(p, product),
        ["00A|CO|1|1|0|0|0", "00B|CO|1|1|0|1|1"],
    )
    p.equal(
        "V14",
        "mixed batch keeps authorized receipts, strips archive identity and sensitive locator",
        p.sql(f"""
        SELECT count(DISTINCT b.id),bool_and(b.redacted AND b.identity IS NULL),count(r.id),
               count(r.locator) FILTER (WHERE o.object_code='00A'),count(r.locator) FILTER (WHERE o.object_code='00B')
        FROM observations_batch b JOIN observations_receipt r ON r.batch_id=b.id
        JOIN observations_stationrevision v ON v.id=r.station_revision_id
        JOIN observations_stationobservation o ON o.id=v.key_id WHERE b.product_id={product}
    """),
        "1|t|2|0|1",
    )
    p.equal(
        "V14",
        "purged key has no retained quality/impact detail",
        p.sql(f"""
        SELECT (SELECT count(*) FROM observations_qualityissue WHERE station_key_id={int(evidence)}),
               (SELECT count(*) FROM observations_change WHERE station_key_id={int(evidence)})
    """),
        "0|0",
    )
    http_case(
        p,
        "V14",
        "withdrawn scope still denied after cleanup",
        reader.call(pm_url),
        403,
        "use_denied",
    )
    for label, url, expected in [("independent A", a_url, 10), ("unaffected B", b_url, 20)]:
        result = revision_value(
            http_case(p, "V14", label + " after cleanup", reader.call(url), 200)
        )
        p.equal(
            "V14", label + " retains original normalized value", float(result["value"]), expected
        )


@contextmanager
def stop_beat(p):
    p.compose("stop", "beat")
    try:
        yield
    finally:
        p.compose("start", "beat")


def expiry_fixture(p, code, interval=8):
    return json.loads(
        p.shell(
            F_SETUP
            + f"""
from datetime import timedelta
from observations.models import DispositionJob
from observations.policy import Denied, clock
product=create_product({code!r})
site=create_site(product)
allow(u,product,'display')
allow(u,product,'process_local')
# The hard physical deadline cannot be guaranteed across a stopped machine.
allow(u,product,valid_until=clock()+timedelta(minutes=5))
try:
    ingest_batch(u.pk,product.pk,'hard','f-acceptance-v1',[candidate(product,1)])
except Denied:
    hard_denied=True
else:
    hard_denied=False
cutoff=clock()+timedelta(seconds={interval})
allow(u,product,valid_until=cutoff,expiry_mode='recoverable')
job=DispositionJob.objects.get(product=product,due_at=cutoff)
ingest_batch(u.pk,product.pk,'expiring','f-acceptance-v1',[candidate(product,1)])
print(json.dumps({{'product':product.pk,'site':site.pk,'job':job.pk,'hard_denied':hard_denied}}))
"""
        )
    )


def expiry_recovery(p, reader):
    # Isolate the product-specific one-off from the periodic recovery path.
    with stop_beat(p):
        normal = expiry_fixture(p, "expiry-one-off", interval=18)
        p.shell("""
from django_celery_beat.models import PeriodicTask
periodic = PeriodicTask.objects.get(name='s02.reconcile')
periodic.enabled = False
periodic.save(update_fields=['enabled'])
""")
        p.equal("V14", "hard deadline refused before save", normal["hard_denied"], True)
        p.equal(
            "V14",
            "normal timed fixture saved only under recoverable grant",
            counts(p, normal["product"])[:5],
            [1, 1, 1, 1, 1],
        )
        p.equal(
            "V14",
            "one-off task scheduled with matching PG cutoff",
            p.sql(f"""
            SELECT q.one_off AND q.enabled AND q.queue='s02.history' AND c.clocked_time=j.due_at
            FROM django_celery_beat_periodictask q JOIN django_celery_beat_clockedschedule c ON c.id=q.clocked_id
            JOIN observations_dispositionjob j ON q.name='s02.due.'||j.id
            WHERE j.id={normal["job"]}
            """),
            "t",
        )
        worker_before = p.compose("logs", "--no-color", "worker").splitlines()

    def completed_one_off():
        return (
            p.sql(f"""
            SELECT j.completed_at>=j.due_at
            FROM observations_dispositionjob j
            JOIN django_celery_beat_periodictask q ON q.name='s02.due.'||j.id
            WHERE j.id={normal["job"]}
        """)
            == "t"
        )

    wait_for("real Beat dispatch and Worker cleanup", completed_one_off, seconds=65)
    p.equal(
        "V14", "one-off expiry removed PostgreSQL rows", counts(p, normal["product"])[:5], [0] * 5
    )
    dispatch = f"Scheduler: Sending due task s02.due.{normal['job']} (observations.dispose)"
    beat = [
        line for line in p.compose("logs", "--no-color", "beat").splitlines() if dispatch in line
    ]
    p.equal("V14", "Beat dispatched the isolated one-off exactly once", len(beat), 1)
    p.record("V14", "Beat one-off receipt", beat[0])

    def consumer_receipts():
        lines = p.compose("logs", "--no-color", "worker").splitlines()[len(worker_before) :]
        received = {}
        matched = set()
        for line in lines:
            if (
                f"disposition_end product={normal['product']}" in line
                and "observations.dispose[" in line
            ):
                matched.add(line.split("observations.dispose[", 1)[1].split("]", 1)[0])
            if "Task observations.dispose[" not in line:
                continue
            task_id = line.split("Task observations.dispose[", 1)[1].split("]", 1)[0]
            if "] received" in line:
                received[task_id] = line
            if "] succeeded in " in line and task_id in received and task_id in matched:
                return [received[task_id], line]
        return None

    p.record(
        "V14", "Worker disposition receipt", wait_for("Worker disposition logs", consumer_receipts)
    )
    p.record("V14", "normal one-off expiry completed automatically", normal["job"])

    with stop_beat(p):
        p.compose("stop", "worker")
        fixture = expiry_fixture(p, "expiry-missed", interval=18)
        product = fixture["product"]
        job = fixture["job"]
        path = query(product, fixture["site"], HOUR, scope="synthetic")
        http_case(
            p, "V14", "recoverable grant still readable before cutoff", reader.call(path), 200
        )
        p.equal(
            "V14",
            "one-off queued zero times before simulated loss",
            p.sql(
                f"SELECT total_run_count FROM django_celery_beat_periodictask WHERE name='s02.due.{job}'"
            ),
            "0",
        )
        p.sql(f"DELETE FROM django_celery_beat_periodictask WHERE name='s02.due.{job}'")
        p.equal(
            "V14",
            "omitted one-off still has authoritative PG job",
            p.sql(
                f"SELECT count(*) FROM observations_dispositionjob WHERE id={job} AND completed_at IS NULL"
            ),
            "1",
        )
        wait_for(
            "PostgreSQL reaches retention cutoff",
            lambda: (
                p.sql(
                    f"SELECT clock_timestamp()>=due_at FROM observations_dispositionjob WHERE id={job}"
                )
                == "t"
            ),
            seconds=30,
        )
        http_case(
            p,
            "V14",
            "cutoff denies reader while Worker and Beat unavailable",
            reader.call(path),
            403,
            "use_denied",
        )
        p.equal(
            "V14",
            "cutoff denies a new write while Worker and Beat are unavailable",
            p.shell(
                F_SETUP
                + f"""
from observations.models import Product
from observations.policy import Denied
product=Product.objects.get(pk={product})
try:
    ingest_batch(u.pk,product.pk,'after-cutoff','f-acceptance-v1',[candidate(product,2)])
except Denied as exc:
    print(exc.code)
else:
    print('unexpected_acceptance')
"""
            ),
            "use_denied",
        )
        p.equal(
            "V14",
            "no claim of physical cleanup during outage",
            [counts(p, product)[:5], disposition_jobs(p, product)],
            [[1, 1, 1, 1, 1], "1|0|0"],
        )
        p.compose("start", "worker")
        wait_for(
            "Worker startup reconciles missed disposal with Beat still stopped",
            lambda: disposition_jobs(p, product) == "0|1|0",
            seconds=45,
        )
        p.equal(
            "V14", "startup reconciles physical DB rows", counts(p, product)[:5], [0, 0, 0, 0, 0]
        )
        p.manage("s02_dispose_due", "--product", str(product), service="worker")
        p.equal("V14", "no second completion on replay", disposition_jobs(p, product), "0|1|0")
