"""Disposable local Compose project, a real HTTP client, and redacted evidence."""

import hashlib
import http.cookiejar
import json
import os
import secrets
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
            assert not self.cmd(
                [*resource, "-q", "--filter", f"label=com.docker.compose.project={self.name}"]
            )
        for kind, name in [("volume", "pgdata"), ("network", "backend"), ("network", "front")]:
            assert (
                self.name + "_" + name
                not in self.cmd(["docker", kind, "ls", "--format", "{{.Name}}"]).splitlines()
            )
        assert not self.cmd(["docker", "image", "ls", "-q", self.name + "-app:local"])
        self.owned = True

    def cleanup(self):
        if self.owned:
            self.compose("down", "--volumes", "--timeout", "5", timeout=100)
            # The unique image tag, like the volume, was absent before this run.
            tag = self.name + "-app:local"
            if self.cmd(["docker", "image", "ls", "-q", tag]):
                self.cmd(["docker", "image", "rm", tag])

    def up(self, build=False):
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
