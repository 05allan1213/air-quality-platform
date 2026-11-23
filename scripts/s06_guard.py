"""Fail-closed checks used only by the disposable S06 verification runner."""

import os
import re
import stat

from s01_runtime import ROOT


class IsolationError(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise IsolationError(reason)


def run_root(run):
    require(re.fullmatch(r"s06-[0-9a-f]{12}", run or ""), "invalid_run")
    return ROOT / "tmp" / "s06-evidence" / run


def owned_root(run):
    root = run_root(run)
    require(root.resolve(strict=True) == root, "redirected_root")
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "root_mode")
    require(info.st_uid == os.geteuid(), "root_owner")
    return root


def identity(run):
    run_root(run)
    suffix = run.removeprefix("s06-")
    return f"s06_db_{suffix}", f"s06_app_{suffix}"


def check_environment(env):
    run = env.get("S06_RUN")
    root = owned_root(run)
    database, role = identity(run)
    expected = {
        "DJANGO_SETTINGS_MODULE": "s06_settings",
        "POSTGRES_DB": database,
        "POSTGRES_USER": role,
        "POSTGRES_HOST": str(root / "pg"),
        "POSTGRES_PORT": "5432",
        "S06_WORK_ROOT": str(root),
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
    }
    for key, value in expected.items():
        require(env.get(key) == value, f"invalid_{key.lower()}")
    for path in (root / "pg", root / "stage", root / "broker"):
        require(path.resolve(strict=True) == path and path.is_dir(), "redirected_directory")
    require(
        not any(
            k in env
            for k in (
                "DATABASE_URL",
                "PGSERVICE",
                "PGSERVICEFILE",
                "CELERY_BROKER_URL",
                "CELERY_RESULT_BACKEND",
            )
        ),
        "override",
    )
    return root


def check_database(env, *, expected_marker, empty):
    """No writes, including when the task marker or initial schema disagrees."""
    import psycopg

    check_environment(env)
    with (
        psycopg.connect(
            host=env["POSTGRES_HOST"],
            port=5432,
            dbname=env["POSTGRES_DB"],
            user=env["POSTGRES_USER"],
            password=env["POSTGRES_PASSWORD"],
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=2500",
        ) as db,
        db.cursor() as cur,
    ):
        cur.execute("""SELECT current_database(), current_user, inet_server_addr() IS NULL,
            current_setting('listen_addresses'), current_setting('airquality.s06_run', true),
            current_setting('transaction_read_only'), rolsuper, current_setting('server_version')
            FROM pg_roles WHERE rolname=current_user""")
        row = cur.fetchone()
        require(
            row is not None
            and row[:-1]
            == (
                env["POSTGRES_DB"],
                env["POSTGRES_USER"],
                True,
                "",
                expected_marker,
                "on",
                False,
            ),
            "database_identity",
        )
        cur.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
            "('pg_catalog','information_schema') AND schemaname !~ '^pg_(toast|temp)'"
        )
        count = cur.fetchone()[0]
        require(not empty or count == 0, "database_not_empty")
        return {
            "database": row[0],
            "role": row[1],
            "socket_only": True,
            "run": expected_marker,
            "postgres_version": row[-1],
            "business_tables": count,
        }
