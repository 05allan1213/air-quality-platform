#!/usr/bin/env python3
"""Execute current checkout code against a fresh disposable PostgreSQL cluster."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager

from s01_runtime import ROOT, source_hash, wait_for

POSTGRES = "postgres:16-alpine"
LABEL = "airquality.s05.verification"


def docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args],
        cwd=ROOT,
        timeout=45,
        check=True,
        capture_output=True,
        text=True,
        **kwargs,
    ).stdout.strip()


def ensure_owner(container, name):
    (info,) = json.loads(docker("inspect", "--type", "container", container))
    if (
        info["Id"] != container
        or info["Config"]["Labels"].get(LABEL) != name
        or info["HostConfig"]["NetworkMode"] != "none"
        or info["HostConfig"]["PortBindings"]
        or any(mount["Type"] not in ("tmpfs", "bind") for mount in info["Mounts"])
    ):
        raise RuntimeError(f"Isolation or ownership check failed for {name}")
    return info


@contextmanager
def postgres(evidence, name, password):
    socket_dir = evidence / "pg"
    if len(os.fsencode(socket_dir / ".s.PGSQL.5432")) >= 108:
        raise RuntimeError("Path to the S05 PostgreSQL socket exceeds the OS limit")
    socket_dir.mkdir(mode=0o777)
    # The container's postgres uid writes under this directory. Its 0700 parent
    # confines access to this test run's host user.
    socket_dir.chmod(0o777)
    with tempfile.NamedTemporaryFile(dir=evidence, mode="w") as env_file:
        env_file.write(
            f"POSTGRES_PASSWORD={password}\nPOSTGRES_INITDB_ARGS=--auth-local=scram-sha-256\n"
        )
        env_file.flush()
        container = docker(
            "run",
            "--detach",
            "--pull=never",
            "--network=none",
            "--name",
            name,
            "--label",
            f"{LABEL}={name}",
            "--env-file",
            env_file.name,
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=768m",
            "--mount",
            f"type=bind,source={socket_dir},target=/run/postgresql",
            POSTGRES,
            "-c",
            "listen_addresses=",
        )
    try:
        info = ensure_owner(container, name)
        if not info["HostConfig"]["Tmpfs"].get("/var/lib/postgresql/data"):
            raise RuntimeError("Missing PostgreSQL tmpfs")
        wait_for("owned PostgreSQL socket", lambda: ready(container))
        # Preinstall the extension in the template so both the migration target
        # and pytest's disposable database can use an ordinary, non-superuser role.
        docker(
            "exec",
            "-i",
            container,
            "sh",
            "-ec",
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 '
            "-h /run/postgresql -U postgres -d template1",
            input=(
                "CREATE EXTENSION btree_gist;\n"
                f"CREATE ROLE s05_app LOGIN CREATEDB PASSWORD '{password}';\n"
                "CREATE DATABASE s05verify OWNER s05_app;\n"
                f"ALTER DATABASE s05verify SET airquality.s05_verify_run = '{name}';\n"
            ),
        )
        yield info["Image"]
    finally:
        ensure_owner(container, name)
        docker("rm", "--force", container)


def ready(container):
    try:
        # Initdb uses another transient server before PID 1 becomes the final
        # postgres instance. Check the live PID, socket directory and database.
        docker(
            "exec",
            container,
            "sh",
            "-ec",
            'test "$(cat /proc/1/comm)" = postgres && '
            "pg_isready -q -h /run/postgresql -U postgres -d postgres",
        )
    except subprocess.CalledProcessError:
        return False
    return True


def environment(evidence, name, password):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TERM") if key in os.environ}
    return env | {
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": secrets.token_hex(48),
        "DJANGO_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "POSTGRES_HOST": str(evidence / "pg"),
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "s05verify",
        "POSTGRES_USER": "s05_app",
        "POSTGRES_PASSWORD": password,
        "PYTHONPATH": os.pathsep.join(str(ROOT / name) for name in ("backend", "tests", "scripts")),
        "S05_VERIFY_RUN": name,
        "S05_EVIDENCE_DIR": str(evidence),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def probe(env):
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
import django
from django.db import connection

django.setup()
with connection.cursor() as cursor:
    cursor.execute('''SELECT current_user, current_database(), inet_server_addr() IS NULL,
        current_setting('airquality.s05_verify_run', true), current_setting('server_version'),
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)''')
    role, database, local, run, version, superuser = cursor.fetchone()
    assert (role, database, local, run, superuser) == (
        's05_app', 's05verify', True, os.environ['S05_VERIFY_RUN'], False,
    )
    print(f'Owned PostgreSQL {version} via Unix socket ({role})', flush=True)
""",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=15,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "python_args",
        nargs=argparse.REMAINDER,
        help="Python arguments following --, e.g. -- -m pytest -q",
    )
    args = parser.parse_args().python_args
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        parser.error("Specify a Python command after --")
    name = f"s05-{secrets.token_hex(5)}"
    evidence = ROOT / "tmp" / "s05-evidence" / name
    evidence.mkdir(mode=0o700, parents=True)
    print(f"S05 evidence: {evidence.relative_to(ROOT)}", flush=True)
    password = secrets.token_hex(32)
    receipt = {"run": name, "source_sha256_before": source_hash(), "cnemc_mode": "off"}
    try:
        with postgres(evidence, name, password) as image:
            receipt["postgres_image"] = image
            env = environment(evidence, name, password)
            probe(env)
            receipt["returncode"] = subprocess.run(
                [sys.executable, *args],
                cwd=ROOT,
                env=env,
                timeout=900,
            ).returncode
    except subprocess.CalledProcessError as exc:
        # Docker/psql can include connection input in error details.
        print(f"S05 runner command failed (exit {exc.returncode})", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        receipt["source_sha256_after"] = source_hash()
        (evidence / "run.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt["returncode"]


if __name__ == "__main__":
    sys.exit(main())
