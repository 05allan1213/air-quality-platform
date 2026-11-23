"""Disposable local Compose project, a real HTTP client, and redacted evidence."""

import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import stat
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_POST = {"fixture_key": "co-hour-v1", "reason": "local_verification"}
RUNS = "/api/v1/admin/fixture-runs/"
AUTH = "/api/v1/auth/"
OBS = "/api/v1/fixtures/observations/?product=F&observed_at=2001-02-03T00:00:00Z"


CONTRACT_SPEC = Path("docs/api/openapi.v1.json")
BROKER_MARKER = "airquality:s07:instance"
WORKER_BINDINGS = {"_kombu.binding_deadlines"} | {
    f"_kombu.{kind}.{exchange}"
    for kind in ("binding", "binding_permanent")
    for exchange in ("s01.fixture", "s02.history", "s03.collection", "celery.pidbox", "celeryev")
}


def source_hash():
    digest = hashlib.sha256()
    paths = [
        ROOT / name
        for name in (
            "Dockerfile",
            ".dockerignore",
            ".env.example",
            ".python-version",
            "compose.yaml",
            "pyproject.toml",
            "uv.lock",
        )
    ]
    for folder in ["backend", "tests", "docker", "scripts"]:
        paths.extend(
            p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    paths.extend(ROOT.glob("compose.*.yaml"))
    if (ROOT / "main.py").is_file():
        paths.append(ROOT / "main.py")
    # S01/S05 archives predate the optional S07 contract candidate.
    if (ROOT / CONTRACT_SPEC).is_file():
        paths.append(ROOT / CONTRACT_SPEC)
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def wait_for(description, probe, seconds=35):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if value := probe():
            return value
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {description}")


class Client:
    def __init__(self, base):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.http = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))
        self.token = None

    def call(self, path, data=None, token=True, headers=None):
        extra = {"Accept": "application/json", **(headers or {})}
        if data is not None:
            extra["Content-Type"] = "application/json"
            if csrf := (self.token if token is True else token):
                extra["X-CSRFToken"] = csrf
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
        request = Request(
            self.base + path,
            data=data,
            headers=extra,
        )
        try:
            response = self.http.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            try:
                payload = json.loads(body) if body else None
            except ValueError:
                payload = {"non_json": True}
            return response.status, payload

    def csrf(self):
        status, body = self.call(AUTH + "csrf/")
        assert status == 200
        self.token = body["csrf_token"]
        return self.token

    def login(self, username, password):
        self.csrf()
        assert self.call(AUTH + "login/", {"username": username, "password": password})[0] == 200
        self.csrf()
        return self


class Project:
    def __init__(self):
        self.name = "s01v" + uuid.uuid4().hex[:12]
        self.evidence = ROOT / "tmp" / "s01-evidence" / self.name
        self.evidence.mkdir(parents=True, mode=0o700)
        self.password = secrets.token_urlsafe(32)
        self.test_password = secrets.token_hex(24)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
        self.env.update(
            POSTGRES_DB="s01",
            POSTGRES_USER="s01_app",
            POSTGRES_PASSWORD=secrets.token_hex(32),
            POSTGRES_ADMIN_PASSWORD=secrets.token_hex(32),
            DJANGO_SECRET_KEY=secrets.token_hex(48),
            WEB_PORT="0",
            COOKIE_SECURE="0",
            DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
            COMPOSE_ANSI="never",
        )
        self.prefix = [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(ROOT / "compose.yaml"),
            "-p",
            self.name,
        ]
        self.owned = False
        self.beat_lock = self.beat_binding = None
        self.pg_generation = self.redis_generation = None
        self.events = []
        self.base = None

    def clean_text(self, text):
        for secret in (
            self.password,
            self.test_password,
            *(
                self.env[k]
                for k in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "POSTGRES_ADMIN_PASSWORD")
            ),
        ):
            text = text.replace(secret, "[redacted]")
        return text

    def cmd(self, args, *, stdin=None, timeout=60, env=None):
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=self.env | (env or {}),
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(
                self.clean_text(
                    f"exit {result.returncode}: " + (result.stderr + result.stdout)[-3500:]
                )
            )
        return result.stdout.strip()

    def compose(self, *args, **options):
        return self.cmd([*self.prefix, *args], **options)

    def sql(self, sql):
        return self.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-XAtq",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "s01",
            stdin=sql,
        )

    def shell(self, code):
        return self.compose(
            "exec",
            "-T",
            "web",
            "python",
            "backend/manage.py",
            "shell",
            "--verbosity",
            "0",
            stdin=code,
        )

    def record(self, section, check, actual):
        self.events.append({"section": section, "check": check, "actual": actual})
        (self.evidence / "checks.json").write_text(
            json.dumps(self.events, indent=2, ensure_ascii=False) + "\n"
        )

    def equal(self, section, check, actual, expected):
        self.record(section, check, actual)
        if actual != expected:
            raise AssertionError(f"{section} {check}: expected {expected!r}, got {actual!r}")

    def claim(self):
        if not re.fullmatch(r"s0[13]v[0-9a-f]{12}", self.name) or self.prefix[-2:] != [
            "-p",
            self.name,
        ]:
            raise ValueError("Expected a generated S01/S02/S03 test project")
        host = self.env.get("DOCKER_HOST")
        if self.env.get("DOCKER_CONTEXT") or not host:
            host = self.cmd(
                ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
            )
        if not host.startswith("unix://"):
            raise ValueError("This verifier operates on a local Docker socket")
        for resource in (
            ["docker", "ps", "-a"],
            ["docker", "network", "ls"],
            ["docker", "volume", "ls"],
        ):
            if self.cmd(
                [*resource, "-q", "--filter", f"label=com.docker.compose.project={self.name}"]
            ):
                raise RuntimeError("Test project already exists")
        for kind, name in [("volume", "pgdata"), ("network", "backend"), ("network", "front")]:
            if (
                self.name + "_" + name
                in self.cmd(["docker", kind, "ls", "--format", "{{.Name}}"]).splitlines()
            ):
                raise RuntimeError("Test project resource already exists")
        if self.cmd(["docker", "image", "ls", "-q", self.name + "-app:local"]):
            raise RuntimeError("Test project image already exists")
        if self.beat_lock is None:
            lock = self.evidence.resolve(strict=True) / "beat.lock"
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o444)
            try:
                os.fchmod(fd, 0o444)
            finally:
                os.close(fd)
            self.beat_lock = lock
            self.beat_binding = self.lock_identity()
        self.env.update(AQ_INSTANCE_ID=self.name, AQ_BEAT_LOCK_SOURCE=str(self.beat_lock))
        self.pg_generation = self.redis_generation = None
        self.owned = True

    def lock_identity(self):
        info = self.beat_lock.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise RuntimeError("Invalid test Beat lock file")
        boot = uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        return f"{boot}:{info.st_dev}:{info.st_ino}:{info.st_uid}"

    def infrastructure_id(self, service):
        if not self.owned:
            raise RuntimeError("Claim the disposable test project before registration")
        cid = self.compose("ps", "-q", service)
        if not re.fullmatch(r"[a-f0-9]{64}", cid):
            raise RuntimeError("Expected one healthy test infrastructure container")
        (info,) = json.loads(self.cmd(["docker", "container", "inspect", cid]))
        labels = info["Config"]["Labels"]
        label = "com.docker.compose."
        backend = f"{self.name}_backend"
        volume_name = f"{self.name}_pgdata"
        mounts = {
            (m["Type"], m.get("Name", m.get("Source", "")), m["Destination"], m["RW"])
            for m in info["Mounts"]
        }
        compose_root = Path(self.prefix[self.prefix.index("-f") + 1]).parent
        init_sql = str(compose_root / "docker" / "10-app-role.sql")
        expected = (
            {
                ("volume", volume_name, "/var/lib/postgresql/data", True),
                ("bind", init_sql, "/docker-entrypoint-initdb.d/10-app-role.sql", False),
            }
            if service == "db"
            else {("tmpfs", "", "/data", True)}
        )
        if (
            info["Id"] != cid
            or labels.get(label + "project") != self.name
            or labels.get(label + "service") != service
            or info["State"]["Health"]["Status"] != "healthy"
            or set(info["NetworkSettings"]["Networks"]) != {backend}
            or info["HostConfig"]["Privileged"]
            or info["HostConfig"]["PortBindings"]
            or mounts != expected
        ):
            raise RuntimeError("Unexpected test infrastructure ownership or storage")
        (network,) = json.loads(self.cmd(["docker", "network", "inspect", backend]))
        if not network["Internal"] or network["Labels"].get(label + "project") != self.name:
            raise RuntimeError("Expected an internal test project network")
        if service == "db":
            (volume,) = json.loads(self.cmd(["docker", "volume", "inspect", volume_name]))
            if (
                volume["Labels"].get(label + "project") != self.name
                or volume["Driver"] != "local"
                or volume["Options"]
            ):
                raise RuntimeError("Expected a local test project volume")
        return cid

    def register_database(self):
        cid = self.infrastructure_id("db")

        def local_sql(sql):
            return self.cmd(
                [
                    "docker",
                    "exec",
                    "-i",
                    cid,
                    "psql",
                    "-XAtq",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "/var/run/postgresql",
                    "-U",
                    "postgres",
                    "-d",
                    "s01",
                ],
                stdin=sql,
            )

        identity = """
            SELECT current_database(), r.rolname, r.rolsuper, system_identifier,
                   coalesce(current_setting('airquality.s07_instance', true), ''),
                   coalesce(current_setting('airquality.beat_lock', true), ''),
                   coalesce(current_setting('airquality.beat_mode', true), ''),
                   (SELECT count(*) FROM pg_tables
                    WHERE schemaname NOT IN ('pg_catalog', 'information_schema'))
            FROM pg_control_system(), pg_database d JOIN pg_roles r ON r.oid=d.datdba
            WHERE d.datname=current_database();
        """
        database, owner, superuser, generation, *registration = local_sql(identity).split("|")
        if [database, owner, superuser] != ["s01", "s01_app", "f"]:
            raise RuntimeError("Unexpected test database or role")
        expected = [self.name, self.beat_binding, "compose"]
        if self.pg_generation is not None:
            if generation != self.pg_generation or registration[:3] != expected:
                raise RuntimeError("Test database registration changed")
            return
        if registration != ["", "", "", "0"]:
            raise RuntimeError("Test Beat registration requires a new empty database")
        local_sql(f"""
            BEGIN;
            ALTER DATABASE s01 SET airquality.s07_instance='{self.name}';
            ALTER DATABASE s01 SET airquality.beat_lock='{self.beat_binding}';
            ALTER DATABASE s01 SET airquality.beat_mode='compose';
            COMMIT;
        """)
        self.pg_generation = generation

    def register_broker(self, *, reconnecting=False):
        cid = self.infrastructure_id("redis")

        def redis(*args):
            return self.cmd(["docker", "exec", cid, "redis-cli", "--raw", "-n", "0", *args])

        generation = re.search(r"^run_id:([0-9a-f]{40})$", redis("INFO", "server"), re.MULTILINE)
        if not generation:
            raise RuntimeError("Could not identify test Redis generation")
        run_id = generation[1]
        marker = redis("GET", BROKER_MARKER)
        if run_id == self.redis_generation:
            if marker != self.name:
                raise RuntimeError("Test Redis registration changed")
            return
        allowed = WORKER_BINDINGS if reconnecting and self.redis_generation else set()
        if (
            marker
            or int(redis("DBSIZE")) > len(allowed)
            or set(redis("KEYS", "*").splitlines()) - allowed
            or redis("SET", BROKER_MARKER, self.name, "NX") != "OK"
        ):
            raise RuntimeError("Test Beat registration requires a fresh Redis generation")
        self.redis_generation = run_id

    def start_redis(self):
        if not self.owned or self.pg_generation is None:
            raise RuntimeError("Claim and start a disposable test project before restarting Redis")
        self.compose("up", "-d", "--wait", "redis")
        self.register_broker(reconnecting=bool(self.compose("ps", "-q", "worker")))

    def cleanup(self):
        if self.owned:
            self.compose("down", "--volumes", "--timeout", "5", timeout=100)
            # The unique image tag, like the volume, was absent before this run.
            tag = self.name + "-app:local"
            if self.cmd(["docker", "image", "ls", "-q", tag]):
                self.cmd(["docker", "image", "rm", tag])

    def up(self, build=False):
        if not self.owned or self.lock_identity() != self.beat_binding:
            raise RuntimeError("Claim the disposable project and retain its registered Beat lock")
        # Register before migrations, including when the first app image is a sealed S01/S02 tree.
        self.compose("up", "-d", "--wait", "db", "redis", timeout=150)
        self.register_database()
        self.register_broker()
        self.compose("up", "-d", *(["--build"] if build else []), timeout=1800)
        self.base = "http://" + self.compose("port", "web", "8000")
        self.wait_ready()

    def wait_ready(self):
        def probe():
            try:
                return Client(self.base).call("/health/ready/") == (200, {"web": "ok", "pg": "ok"})
            except (URLError, OSError):
                return False

        wait_for("Web and PG", probe)

    def snapshot(self):
        counts = self.sql(
            "SELECT (SELECT count(*) FROM samples_fixturerun), (SELECT count(*) FROM samples_fixtureobservation)"
        )
        return [
            *map(int, counts.split("|")),
            int(self.compose("exec", "-T", "redis", "redis-cli", "LLEN", "s01.fixture")),
        ]

    def run_row(self, run_id):
        key = str(uuid.UUID(run_id))
        return json.loads(
            self.sql(f"SELECT row_to_json(r) FROM samples_fixturerun r WHERE id='{key}'")
        )

    def done(self, run_id, outcome):
        def probe():
            row = self.run_row(run_id)
            return row if row["state"] != "pending" else None

        row = wait_for("Worker terminal " + run_id, probe)
        assert row["state"] == outcome, row
        return row

    def receive(self, run_id, reason, offset=0):
        def probe():
            lines = self.compose("logs", "--no-color", "worker").splitlines()[offset:]
            received = [
                line for line in lines if f"samples.process_fixture[{run_id}] received" in line
            ]
            ended = [line for line in lines if f"fixture_end run={run_id} reason={reason}" in line]
            if received and ended:
                return [received[-1], ended[-1]]
            return None

        return wait_for("matching Worker receipt and completion", probe)

    def grant(self, operation, enabled):
        assert operation in ("process_local", "retain", "display")
        self.sql(
            f"UPDATE samples_fixturegrant SET enabled={'true' if enabled else 'false'} WHERE operation='{operation}'"
        )

    def reset_web_limits(self):
        self.compose("restart", "web")
        # Docker may assign a new ephemeral host port even to the same container.
        self.base = "http://" + self.compose("port", "web", "8000")
        self.wait_ready()

    def receipt(self, status, detail=""):
        receipt = {
            "status": status,
            "project": self.name,
            "time_utc": datetime.now(UTC).isoformat(),
            "detail": self.clean_text(detail),
        }
        (self.evidence / "result.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            f"{status} {self.name} {self.evidence.relative_to(ROOT)} {receipt['detail']}",
            flush=True,
        )
