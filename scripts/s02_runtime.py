"""Containers and evidence for the S02 acceptance runner."""

import json
import re
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from s01_runtime import FIXTURE_POST, OBS, ROOT, RUNS, Client, Project
from verify_s01 import http_case

BASELINE = "07711ee39d7a5673671214ca51c4bd0d64b17fd9"
APP_FILES = (
    "Dockerfile",
    ".dockerignore",
    ".python-version",
    "compose.yaml",
    "pyproject.toml",
    "uv.lock",
    "docker",
    "backend",
    "tests",
    "scripts",
)


class VerificationError(RuntimeError):
    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        super().__init__(f"exit {exit_code}: {output}")


class S02Project(Project):
    def __init__(self):
        # Keep the isolation machinery in Project, and use a separate evidence directory.
        super().__init__()
        self.evidence.rmdir()
        self.evidence = ROOT / "tmp" / "s02-evidence" / self.name
        self.evidence.mkdir(parents=True, mode=0o700)

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
            raise VerificationError(
                result.returncode,
                self.clean_text((result.stderr + result.stdout)[-3500:]),
            )
        return result.stdout.strip()

    def manage(self, *arguments, service="web"):
        return self.compose("exec", "-T", service, "python", "backend/manage.py", *arguments)

    def stage(self, source):
        # Pass a bounded/controlled file object straight to the container command. Persist
        # neither stdout from a failing adapter nor source bytes in acceptance evidence.
        result = subprocess.run(
            [
                *self.prefix,
                "exec",
                "-T",
                "worker",
                "python",
                "backend/manage.py",
                "s02_stage_uci",
                "--actor",
                "operator",
            ],
            stdin=source,
            env=self.env,
            capture_output=True,
            cwd=ROOT,
            timeout=60,
        )
        if result.returncode:
            # Domain command errors are fixed codes. Do not relay arbitrary stdout/stderr.
            error = re.fullmatch(rb"CommandError: ([a-z][a-z_0-9]{0,63})\s*", result.stderr)
            raise VerificationError(
                result.returncode, error[1].decode() if error else "staging failed"
            )
        try:
            return str(uuid.UUID(result.stdout.decode().strip()))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("staging did not return a UUID") from exc

    def receipt(self, status, outcomes):
        result = {
            "status": status,
            "project": self.name,
            "time_utc": datetime.now(UTC).isoformat(),
            "matrix": outcomes,
        }
        (self.evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"{status} {self.name} {self.evidence.relative_to(ROOT)}", flush=True)


def fresh(p):
    p.compose("config", "-q")
    p.up(build=True)
    p.record("V01", "fresh migrate", p.compose("logs", "--no-color", "migrate"))
    p.equal(
        "V01",
        "application role is database owner, without administrator privileges",
        p.sql("""
            SELECT r.rolsuper,r.rolcreatedb,r.rolname=pg_get_userbyid(d.datdba)
            FROM pg_roles r CROSS JOIN pg_database d
            WHERE r.rolname='s01_app' AND d.datname=current_database()
        """),
        "f|f|t",
    )
    p.equal(
        "V01",
        "ordinary-role migration installs btree_gist",
        p.sql("SELECT count(*) FROM pg_extension WHERE extname='btree_gist'"),
        "1",
    )
    p.equal(
        "V01",
        "no formal observations or grants on fresh migration",
        p.sql("""
            SELECT (SELECT count(*) FROM observations_cityobservation),
                   (SELECT count(*) FROM observations_stationobservation),
                   (SELECT count(*) FROM observations_usegrant)
        """),
        "0|0|0",
    )
    p.equal(
        "V01",
        "no default periodic business tasks",
        p.sql("""
            SELECT count(*) FROM django_celery_beat_periodictask
            WHERE enabled AND task <> 'celery.backend_cleanup'
        """),
        "0",
    )
    p.record("V01", "PG server", p.sql("SHOW server_version"))
    p.record("V01", "Redis server", p.compose("exec", "-T", "redis", "redis-server", "--version"))
    p.record("V01", "Compose image ids", p.compose("images"))
    assertions = p.manage("showmigrations", "--plan")
    assert "[ ]" not in assertions
    p.record("V01", "fresh migration graph", assertions)
    reverse_db = "s02_reverse"
    p.sql(f"CREATE DATABASE {reverse_db} OWNER s01_app")
    try:

        def migrate(*args):
            return p.compose(
                "exec",
                "-T",
                "-e",
                f"POSTGRES_DB={reverse_db}",
                "web",
                "python",
                "backend/manage.py",
                "migrate",
                *args,
                "--noinput",
            )

        p.record("V01", "isolated reversal database migrate", migrate())
        p.record("V01", "isolated reversal observations zero", migrate("observations", "zero"))
        inspect = [
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
            reverse_db,
        ]
        p.equal(
            "V01",
            "reverse keeps S01 table and shared extension",
            p.compose(
                *inspect,
                stdin="""
                SELECT to_regclass('samples_fixtureobservation') IS NOT NULL,
                       to_regclass('observations_stationobservation') IS NULL,
                       EXISTS (SELECT 1 FROM pg_extension WHERE extname='btree_gist')
            """,
            ),
            "t|t|t",
        )
        p.record("V01", "isolated reversal reapply", migrate())
        p.equal(
            "V01",
            "S01 grants and new app table after reapply",
            p.compose(
                *inspect,
                stdin="""
                SELECT (SELECT count(*) FROM samples_fixturegrant),
                       (SELECT count(*) FROM observations_usegrant)
            """,
            ),
            "3|0",
        )
    finally:
        p.sql(f"DROP DATABASE {reverse_db}")


def verify_upgrade(p):
    """Seed and run the exact S01 Git tree before attaching the same owned database to S02."""
    begin = p.prefix
    with tempfile.TemporaryDirectory(dir=p.evidence) as directory:
        with tempfile.TemporaryFile() as archive:
            subprocess.run(
                ["git", "archive", "--format=tar", BASELINE, *APP_FILES],
                check=True,
                cwd=ROOT,
                stdout=archive,
                timeout=30,
            )
            archive.seek(0)
            subprocess.run(["tar", "-x", "-C", directory], check=True, stdin=archive, timeout=30)
        p.prefix = [*begin[:5], str(Path(directory) / "compose.yaml"), *begin[6:]]
        try:
            p.up(build=True)
            p.equal(
                "V01",
                "S01 tree has no observations migration",
                p.sql("SELECT to_regclass('observations_cityobservation') IS NULL"),
                "t",
            )
            p.shell(f"""
from django.contrib.auth.models import Permission, User
u = User.objects.create_user('upgrade', password={p.password!r})
u.user_permissions.add(Permission.objects.get(codename='run_fixture', content_type__app_label='samples'))
""")
            client = Client(p.base).login("upgrade", p.password)
            run_id = http_case(
                p, "V01", "S01 trigger before upgrading", client.call(RUNS, FIXTURE_POST), 202
            )["run_id"]
            p.done(run_id, "succeeded")
            p.record("V01", "S01 Worker before upgrading", p.receive(run_id, "succeeded"))
            original = s01_snapshot(p)
            # Do not persist session cookie identifiers or encoded session contents.
            p.record("V01", "S01 SQL before upgrading", original)
            old = http_case(p, "V01", "S01 Session before upgrading", client.call(OBS), 200)
            p.equal("V01", "S01 pre-upgrade value", old["results"][0]["value"], "1.250")
            p.compose("down", "--timeout", "5")
        finally:
            p.prefix = begin
    p.up(build=True)
    client.base = p.base
    p.equal("V01", "S01 SQL unchanged after upgrading", s01_snapshot(p), original)
    result = http_case(p, "V01", "original Session after upgrading", client.call(OBS), 200)
    p.equal(
        "V01",
        "S01 observation unchanged after upgrading",
        result["results"][0]["id"],
        old["results"][0]["id"],
    )
    p.equal("V01", "S01 value after upgrading", result["results"][0]["value"], "1.250")
    p.equal(
        "V01",
        "no automatic grant or observation backfill",
        p.sql("""
            SELECT (SELECT count(*) FROM observations_usegrant),
                   (SELECT count(*) FROM observations_cityobservation)
                     + (SELECT count(*) FROM observations_stationobservation)
        """),
        "0|0",
    )
    repeat = p.manage("migrate", "--noinput")
    assert "No migrations to apply" in repeat
    p.record("V01", "repeat upgrade", repeat)


def s01_snapshot(p):
    return json.loads(
        p.sql("""
        SELECT jsonb_build_object(
            'run', (SELECT to_jsonb(r) FROM samples_fixturerun r),
            'observation', (SELECT to_jsonb(o) FROM samples_fixtureobservation o),
            'grants', (SELECT jsonb_agg(to_jsonb(g) ORDER BY id) FROM samples_fixturegrant g),
            'user_id', (SELECT id FROM auth_user WHERE username='upgrade'),
            'grant_link', (SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM auth_user_user_permissions p),
            'session_count', (SELECT count(*) FROM django_session)
        )
    """)
    )


def quality(p):
    """Record exit codes and individual F test results without arbitrary test stdout."""
    result = True
    for command in [
        ["uv", "lock", "--check"],
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
        ["mypy"],
        ["python", "backend/manage.py", "check"],
        ["python", "backend/manage.py", "makemigrations", "--check", "--dry-run"],
    ]:
        try:
            output = p.compose("exec", "-T", "web", *command)
        except VerificationError as exc:
            result = False
            p.record("quality", " ".join(command), {"exit": exc.exit_code, "error": str(exc)})
        else:
            p.record("quality", " ".join(command), {"exit": 0, "stdout": output})
    p.sql(f"CREATE ROLE s02_pytest LOGIN CREATEDB PASSWORD '{p.test_password}'")
    try:
        guest = {"POSTGRES_USER": "s02_pytest", "POSTGRES_PASSWORD": p.test_password}
        for label, selectors in [("rules", ["tests/test_observation_rules.py"]), ("full", [])]:
            command = ["pytest", "-q", *selectors, f"--junitxml=/tmp/s02-{label}.xml"]
            code = 0
            try:
                p.compose(
                    "exec",
                    "-T",
                    *(arg for key in guest for arg in ("-e", key)),
                    "web",
                    *command,
                    env=guest,
                    timeout=240,
                )
            except VerificationError as exc:
                code = exc.exit_code
                result = False
            xml = p.compose("exec", "-T", "web", "cat", f"/tmp/s02-{label}.xml")
            root = ElementTree.fromstring(xml)
            cases = {
                f"{node.get('classname')}::{node.get('name')}": (
                    "FAIL"
                    if node.find("failure") is not None or node.find("error") is not None
                    else "NOT RUN"
                    if node.find("skipped") is not None
                    else "PASS"
                )
                for node in root.iter("testcase")
            }
            p.record(
                "quality",
                f"pytest {label}",
                {
                    "exit": code,
                    "summary": [dict(suite.attrib) for suite in root.iter("testsuite")],
                    "cases": cases,
                },
            )
    finally:
        p.sql("DROP ROLE s02_pytest")
    return result


def regression(p):
    """Execute the original script unchanged and collect only its new project receipt."""
    if not p.owned:
        raise RuntimeError("claim the local Docker socket before regression")
    result = subprocess.run(
        ["python3.13", "scripts/verify_s01.py", "--rounds", "1"],
        cwd=ROOT,
        env=p.env,
        text=True,
        capture_output=True,
        timeout=2400,
    )
    projects = re.findall(r"^RUN (s01v[0-9a-f]{12})$", result.stdout, re.MULTILINE)
    assert len(projects) == 1, p.clean_text(result.stdout[-1000:])
    evidence = ROOT / "tmp" / "s01-evidence" / projects[0]
    receipt = json.loads((evidence / "result.json").read_text())
    checks = json.loads((evidence / "checks.json").read_text())
    p.record(
        "V15",
        "original S01 regression",
        {
            "command": "python3.13 scripts/verify_s01.py --rounds 1",
            "exit": result.returncode,
            "status": receipt["status"],
            "receipts": str(evidence.relative_to(ROOT)),
            "checks": len(checks),
            "source": checks[0]["actual"],
        },
    )
    assert result.returncode == 0 and receipt["status"] == "PASS", receipt["detail"]
