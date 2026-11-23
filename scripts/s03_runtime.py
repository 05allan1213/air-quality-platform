"""Isolated S03 Compose project and independent database/HTTP evidence."""

import json
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from s01_runtime import FIXTURE_POST, OBS, ROOT, RUNS, Client, wait_for
from s02_runtime import APP_FILES, S02Project
from verify_s01 import http_case

S02 = "d7c5d8b79bdd60d4fc5d3d1840fc1e4043dfae0a"
RUN_URL = "/api/v1/admin/collection-runs/"
WINDOW_URL = "/api/v1/admin/collection-windows/"
JOB_URL = "/api/v1/admin/collection-jobs/"
LIMITS = {
    "min_interval_seconds": 1,
    "max_concurrent": 1,
    "period_seconds": 60,
    "max_requests": 12,
}


def control_payload(action, version):
    reason = {"pause": "operator_pause", "redeliver": "operator_recovery"}.get(
        action, "operator_resume"
    )
    return dict(
        action=action, request_id=str(uuid.uuid4()), reason_code=reason, expected_version=version
    )


def job_control(account, job, action):
    route = f"{JOB_URL}{job}/"
    status, result = account.call(route)
    assert status == 200, (status, result)
    kind = {"resume_source": "source", "resume_product": "product"}.get(action, "job")
    return account.call(route, control_payload(action, result["targets"][kind]["control_version"]))


TEST_HOUR = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def hour(offset=0):
    if not offset:
        return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    return TEST_HOUR + timedelta(hours=offset)


def city_packet(at, code="001", aqi=43):
    return {
        "Area": "Fixture " + code,
        "CityCode": code,
        "TimePoint": at.isoformat(),
        "AQI": aqi,
        "PM2_5": 12,
        "PM10": 23,
        "SO2": 5,
        "NO2": 7,
        "CO": "0.31",
        "O3": 34,
    }


def list_packet(at, count, aqi=43):
    return [
        {
            "Area": f"Fixture {pos:03d}",
            "CityCode": f"{pos:03d}",
            "TimePoint": at.isoformat(),
            "AQI": aqi,
            **{key + "Level": 1 for key in ["PM2_5", "PM10", "SO2", "NO2", "CO", "O3"]},
        }
        for pos in range(1, count + 1)
    ]


class S03Project(S02Project):
    def __init__(self):
        super().__init__()
        self.evidence.rmdir()
        self.name = "s03v" + self.name[4:]
        self.evidence = ROOT / "tmp" / "s03-evidence" / self.name
        self.evidence.mkdir(parents=True, mode=0o700)
        self.prefix[-1] = self.name
        self.default_prefix = self.prefix.copy()
        self.prefix[6:6] = ["-f", str(ROOT / "scripts" / "s03_compose.yaml")]

    def origin(self, path, body=None):
        source = """import sys
from urllib.request import Request, urlopen
url = 'http://s03-origin:8080' + sys.argv[1]
with urlopen(Request(url, data=sys.stdin.buffer.read() if sys.argv[2] == '1' else None,
                     headers={'Content-Type': 'application/json'}), timeout=5) as response:
    print(response.read().decode())
"""
        return json.loads(
            self.compose(
                "exec",
                "-T",
                "web",
                "python",
                "-c",
                source,
                path,
                str(int(body is not None)),
                stdin=json.dumps(body) if body is not None else None,
            )
        )

    def script(self, shape, *packets):
        self.origin("/_script", {shape: packets})

    def release(self, gate):
        self.origin("/_release", {"gate": gate})

    def hit(self, gate):
        return wait_for(
            "source barrier " + gate,
            lambda: next(
                (event for event in self.origin("/_events") if event["gate"] == gate), None
            ),
        )

    def count(self, shape):
        return sum(event["shape"] == shape for event in self.origin("/_events"))

    def row(self, run_id):
        return json.loads(
            self.sql(f"SELECT row_to_json(r) FROM collection_run r WHERE id='{uuid.UUID(run_id)}'")
        )

    def settled(self, run_id):
        def probe():
            row = self.row(run_id)
            if row["status"] not in ("running", "pending"):
                return row
            logs = self.compose("logs", "--no-color", "worker", "worker-b").splitlines()
            error = next(
                (
                    line
                    for line in reversed(logs)
                    if f"collection.execute_run[{run_id}] raised unexpected" in line
                ),
                None,
            )
            if error:
                raise AssertionError(self.clean_text(error))
            return None

        return wait_for("collection terminal " + run_id, probe, seconds=90)

    def successor(self, run_id):
        def probe():
            row = self.sql(
                f"SELECT id FROM collection_run WHERE predecessor_id='{uuid.UUID(run_id)}' AND reason='retry'"
            )
            return row or None

        return wait_for("linked acquisition " + run_id, probe, seconds=110)

    def post(self, tag, account, job, at, *, reason="manual", request_id=None, allowed=(202,)):
        code, body = account.call(
            RUN_URL,
            {
                "job": job,
                "target": at.isoformat(),
                "request_id": request_id or str(uuid.uuid4()),
                "reason": reason,
            },
        )
        self.record(tag, f"POST {reason} job {job}", [code, body])
        assert code in allowed, (code, body)
        return body

    def delivery(self, run_id):
        identifier = str(uuid.UUID(run_id))
        self.shell(
            f"from config.celery import app\napp.send_task('collection.execute_run', args=[{identifier!r}], task_id={identifier!r}, queue='s03.collection', retry=False)"
        )

    def reconcile(self):
        return self.manage("s03_reconcile_due")

    def stop_beat(self):
        self.compose("stop", "-t", "2", "beat")

    def worker_log(self, run_id):
        return wait_for(
            "Celery received " + run_id,
            lambda: [
                line
                for line in self.compose("logs", "--no-color", "worker", "worker-b").splitlines()
                if f"collection.execute_run[{run_id}] received" in line
            ],
        )

    def receipt_count(self, run_id):
        return int(
            self.sql(
                f"SELECT count(*) FROM observations_receipt WHERE batch_id=(SELECT batch_id FROM collection_run WHERE id='{uuid.UUID(run_id)}')"
            )
        )

    def accepted_count(self, window):
        return int(
            self.sql(
                f"SELECT count(*) FROM collection_runitem WHERE window_id={int(window)} AND accepted"
            )
        )

    def assert_unique(self, tag):
        self.equal(
            tag,
            "independent SQL duplicate acceptance count",
            self.sql("""
            SELECT count(*) FROM (SELECT window_id,item_key FROM collection_runitem
              WHERE accepted GROUP BY window_id,item_key HAVING count(*)>1) duplicates
        """),
            "0",
        )

    def window(self, tag, account, window):
        result = http_case(
            self, tag, "review window", account.call(f"{WINDOW_URL}{int(window)}/"), 200
        )
        self.record(tag, "authorized window projection", result)
        return result

    def release_budget(self):
        # An explicit clock accelerator between independent scenarios. In-flight
        # occupancy and within-scenario intervals stay untouched.
        self.sql("""UPDATE collection_run SET reserved_at=reserved_at-interval '70 seconds'
                    WHERE status NOT IN ('running','pending') AND reserved_at IS NOT NULL""")


def snapshot(p):
    return json.loads(
        p.sql("""
        SELECT jsonb_build_object(
          'keys', (SELECT jsonb_agg(to_jsonb(k) ORDER BY id) FROM observations_stationobservation k),
          'revisions', (SELECT jsonb_agg(to_jsonb(v) ORDER BY id) FROM observations_stationrevision v),
          'grants', (SELECT jsonb_agg(to_jsonb(g) ORDER BY id) FROM observations_usegrant g),
          'permissions', (SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM auth_user_user_permissions p),
          'sessions', (SELECT count(*) FROM django_session)
        )
    """)
    )


def upgrade(p):
    """Use the sealed S02 Git image to seed the same PG and Redis used after migration."""
    upgraded = p.prefix
    with (
        tempfile.TemporaryDirectory(dir=p.evidence) as directory,
        tempfile.TemporaryFile() as archive,
    ):
        subprocess.run(
            ["git", "archive", "--format=tar", S02, *APP_FILES],
            cwd=ROOT,
            stdout=archive,
            check=True,
            timeout=30,
        )
        archive.seek(0)
        subprocess.run(["tar", "-x", "-C", directory], stdin=archive, check=True, timeout=30)
        p.prefix = [
            *p.default_prefix[:5],
            str(Path(directory) / "compose.yaml"),
            *p.default_prefix[6:],
        ]
        try:
            p.up(build=True)
            p.equal(
                "V01",
                "sealed S02 schema lacks S03 ledger",
                p.sql("SELECT to_regclass('collection_run') IS NULL"),
                "t",
            )
            p.shell(f"""
import sys
sys.path.insert(0, '/app/tests')
from django.contrib.auth.models import User, Permission
from observations.revisions import fixture_ticket, ingest_batch
from s02_support import allow, candidate, create_product, create_site
u=User.objects.create_user('upgrade', password={p.password!r})
u.user_permissions.add(*Permission.objects.filter(codename__in=['manage_grants','import_history','run_fixture']))
product = create_product('upgrade-s02')
create_site(product)
for op in ('display','process_local','retain_normalized'):
    allow(u,product,op)
for version in (1,3,2):
    ingest_batch(u.pk, product.pk, fixture_ticket(product.pk), product.contract_version,
                 [candidate(product,version,'20' if version==2 else '10')])
""")
            client = Client(p.base).login("upgrade", p.password)
            done = http_case(
                p, "V01", "pre-upgrade S01 request", client.call(RUNS, FIXTURE_POST), 202
            )["run_id"]
            p.done(done, "succeeded")
            p.compose("stop", "-t", "5", "worker", "beat")
            queued = http_case(
                p, "V01", "enqueue old F task", client.call(RUNS, FIXTURE_POST), 202
            )["run_id"]
            p.equal(
                "V01",
                "old queue retains message",
                p.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s01.fixture"),
                "1",
            )
            p.compose("rm", "-f", "-s", "worker", "beat", "web", "migrate")
            old = snapshot(p)
            # Keep every pre-upgrade column and verify each additive permission default.
            for grant in old["grants"]:
                for field, value in dict(
                    collection_limits=None,
                    endpoints=[],
                    max_total_requests=None,
                    validation_allocation={},
                    validation_disabled=False,
                ).items():
                    grant.setdefault(field, value)
        finally:
            p.prefix = upgraded
    p.up(build=True)
    p.equal(
        "V01", "S02 keys, revisions, rights, permissions, and sessions preserved", snapshot(p), old
    )
    p.record(
        "V01",
        "S02 watermark/qualification and original key",
        p.sql("SELECT id,watermark,qualification_version FROM observations_stationobservation"),
    )
    client.base = p.base
    p.done(queued, "succeeded")
    p.record(
        "V19",
        "original worker consumes queued S01 message after upgrade",
        p.receive(queued, "succeeded"),
    )
    response = http_case(
        p, "V01", "original Session and exact S01 read after upgrade", client.call(OBS), 200
    )
    assert response["results"][0]["value"] == "1.250"
    selector = urllib_query(
        p, "upgrade-s02", "2024-01-01T00:00:00Z", entity="site_epoch", indicator="CO"
    )
    result = http_case(
        p, "V01", "original Session exact S02 read after upgrade", client.call(selector), 200
    )
    p.equal(
        "V01",
        "old normalized result retains watermark v3 value",
        result["results"][0]["value"],
        "10.000000",
    )
    repeated = p.manage("migrate", "--noinput")
    assert "No migrations to apply" in repeated
    p.record("V01", "repeat migration", repeated)


def rollback_empty(p):
    """Exercise S03 downgrade and re-apply without touching the upgrade database."""
    p.compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-Xq",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-c",
        "CREATE DATABASE s03_rollback OWNER s01_app",
    )

    def manage(*args):
        return p.compose(
            "exec",
            "-T",
            "-e",
            "POSTGRES_DB=s03_rollback",
            "web",
            "python",
            "backend/manage.py",
            *args,
        )

    def sql(probe):
        return p.compose(
            "exec", "-T", "db", "psql", "-XAtq", "-U", "postgres", "-d", "s03_rollback", "-c", probe
        )

    p.record(
        "V01", "clean PG forward migration as application role", manage("migrate", "--noinput")
    )
    p.equal(
        "V01",
        "clean PG application owner and disabled collection",
        sql("""
        SELECT pg_get_userbyid(c.relowner), (SELECT count(*) FROM collection_job),
               (SELECT count(*) FROM observations_usegrant)
        FROM pg_class c WHERE relname='collection_job'
    """),
        "s01_app|0|0",
    )
    p.record(
        "V01",
        "reverse S03 observation migrations",
        manage("migrate", "observations", "0006", "--noinput"),
    )
    p.record(
        "V01", "reverse collection ledger", manage("migrate", "collection", "zero", "--noinput")
    )
    p.equal(
        "V01",
        "rollback removes S03 ledger and preserves S02 relations",
        sql("""
        SELECT to_regclass('collection_run') IS NULL, to_regclass('observations_receipt') IS NOT NULL,
               EXISTS (SELECT 1 FROM django_migrations WHERE app='observations' AND name='0006_source_clock')
    """),
        "t|t|t",
    )
    p.record(
        "V01",
        "second forward migration on same isolated PG database",
        manage("migrate", "--noinput"),
    )
    p.equal(
        "V01",
        "re-applied constraints and lineage trigger",
        sql("""
        SELECT (SELECT count(*) FROM pg_constraint WHERE conname IN
              ('s03_business_window','s03_item_same_window','s03_window_fence')),
              EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='s03_receipt_lineage'),
              EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='s03_window_accept_once')
    """),
        "3|t|t",
    )


def urllib_query(p, code, at, *, entity="city", indicator="AQI", city=None):
    product = p.sql("SELECT id FROM observations_product WHERE code='" + code + "'")
    assert product.isdigit()
    entity_id = city or p.sql("SELECT id FROM observations_siteepoch ORDER BY id LIMIT 1")
    return "/api/v1/observations/current/?" + urlencode(
        {
            "product": product,
            entity: entity_id,
            "indicator": indicator,
            "report_kind": "hourly",
            "window_definition": "hourly_label_unspecified",
            "standard_series": "unknown" if indicator == "AQI" else "not_applicable",
            "observed_at": at,
            "scope": "synthetic",
        }
    )


def prepare(p):
    p.shell(f"""
from datetime import UTC, date, datetime
from django.contrib.auth.models import User, Permission
from observations.models import City, CityIdentifier, Product, SiteEpoch, Source, Station, StationIdentifier
from observations.policy import OPERATIONS, register_grant
from psycopg.types.range import Range
u = User.objects.create_user('collector', password={p.password!r})
u.user_permissions.set(Permission.objects.filter(content_type__app_label='observations', codename__in=[
    'run_collection','manage_collection','review_observation','manage_grants']))
reader = User.objects.create_user('collection_reader', password={p.password!r})
archivist = User.objects.create_user('collection_archivist', password={p.password!r})
archivist.user_permissions.set(Permission.objects.filter(codename__in=['import_history','run_fixture']))
limits = {LIMITS!r} | dict(max_requests=200, min_interval_seconds=0)
s = Source.objects.create(namespace='s03.fixture', publisher='Synthetic S03', collection_limits=limits)
for shape in 'LCST':
    product = Product.objects.create(source=s, code=shape, family='F', shape=shape, level='station' if shape=='S' else 'city',
        synthetic=True, contract_version='s03.f.v1', source_timezone='UTC')
    for op in ('acquire_online','process_local','retain_normalized','display','review'):
        register_grant(u.pk, product.pk, operation=op, audience=OPERATIONS[op], geography='*',
            fields=['*'], data_from=date.min, data_until=date.max, terms='Synthetic F', terms_version='1',
            **(dict(collection_limits=limits) if op=='acquire_online' else {{}}))
period = Range(datetime(2000,1,1,tzinfo=UTC), None)
for pos in range(1, 201):
    code = f'{{pos:03d}}'
    c = City.objects.create(source=s, name='Fixture '+code, geography='f-'+code)
    CityIdentifier.objects.create(source=s, city=c, code=code, evidence='F', active_period=period)
c = City.objects.get(source=s, geography='f-001')
for code in ('101','102'):
    station = Station.objects.create(source=s,name='Fixture Station')
    StationIdentifier.objects.create(source=s,station=station,code=code,evidence='F',active_period=period)
    SiteEpoch.objects.create(station=station,city=c,name='Fixture Station',location_evidence='F',certainty='confirmed',active_period=period)
""")
    return tuple(
        Client(p.base).login(username, p.password)
        for username in ("collector", "collection_reader", "collection_archivist")
    )


def register_job(p, shape, *, profile="f-exact-v1", expected=None, city="001", due=None):
    if expected is None and shape in "CS":
        expected = [city] if shape == "C" else ["101", "102"]
    return int(
        p.shell(f"""
from datetime import datetime
from django.contrib.auth.models import User
from observations.models import City, Product
from collection.models import Job
product = Product.objects.get(source__namespace='s03.fixture',code={shape!r})
city = None if product.shape=='L' else City.objects.get(source=product.source,geography={"f-" + city!r})
job = Job.objects.create(product=product,city=city,executor=User.objects.get(username='collector'),enabled=True,
    profile={profile!r},expected_codes={expected!r},roster_basis='synthetic-v1',
    next_due_at={None if due is None else "datetime.fromisoformat(" + repr(due.isoformat()) + ")"})
print(job.pk)
""")
    )


def topology(p):
    def inspect(service):
        container = p.compose("ps", "-q", service)
        assert container
        return json.loads(
            p.cmd(["docker", "inspect", "--format", "{{json .NetworkSettings}}", container])
        )

    for service in ("worker", "worker-b", "beat", "db", "redis", "s03-origin"):
        networks = sorted(inspect(service)["Networks"])
        p.equal("V19", f"{service} only on backend network", networks, [p.name + "_backend"])
    backend = p.cmd(
        ["docker", "network", "inspect", "--format", "{{.Internal}}", p.name + "_backend"]
    )
    p.equal("V19", "backend network is internal", backend, "true")
    p.equal(
        "V19", "single Beat service instance", len(p.compose("ps", "-q", "beat").splitlines()), 1
    )
    p.equal(
        "V19",
        "synthetic HTTP service port has no host exposure",
        inspect("s03-origin")["Ports"],
        {},
    )
    for service in ("worker", "worker-b"):
        p.record("V19", service + " real worker container", p.compose("ps", "-q", service))
