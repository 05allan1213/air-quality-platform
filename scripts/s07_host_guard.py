"""Fail-closed prerequisites for the S07 disposable host acceptance harness."""

import os
import re
import stat
from typing import Any

from s01_runtime import ROOT
from s06_guard import require
from s06_sandbox import check_network

ENVIRONMENT = re.compile(r"s07-([br])-([a-f0-9]{12})")
POSTGRES_MARKER = "airquality.s07_instance"
BROKER_MARKER = "airquality:s07:instance"


def identity(run):
    match = ENVIRONMENT.fullmatch(run or "")
    require(match is not None, "invalid_s07_host_identity")
    suffix = match[2]
    return f"s07_db_{suffix}", f"s07_app_{suffix}"


def work_root(run):
    identity(run)
    return ROOT / "tmp" / "s07-evidence" / run


def directory(path, mode):
    require(path.is_absolute() and path.resolve(strict=True) == path, "redirected_directory")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid(), "directory_owner")
    require(stat.S_IMODE(info.st_mode) == mode, "directory_mode")
    return path


def check_environment(env):
    run = env.get("AQ_INSTANCE_ID")
    root = directory(work_root(run), 0o700)
    database, role = identity(run)
    expected = {
        "AQ_HOST_ROOT": str(root),
        "POSTGRES_DB": database,
        "POSTGRES_USER": role,
        "POSTGRES_HOST": str(root / "pg"),
        "POSTGRES_PORT": "5432",
        "CELERY_BROKER_URL": f"redis+socket://{root}/broker/redis.sock",
        "S02_STAGE_DIR": str(root / "stage"),
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
    }
    for key, value in expected.items():
        require(env.get(key) == value, f"invalid_{key.lower()}")
    require(
        not any(
            key.startswith("PG")
            or key.upper().endswith("_PROXY")
            or key
            in {
                "AQ_HOST_CONFIG",
                "DATABASE_URL",
                "DJANGO_SETTINGS_MODULE",
                "DOCKER_HOST",
                "S06_BACKUP_DIR",
                "S06_AUTHORITY_DIR",
                "CELERY_RESULT_BACKEND",
                "CNEMC_GATEWAY_URL",
            }
            for key in env
        ),
        "environment_override",
    )
    pg = root / "pg"
    pg_info = pg.lstat()
    require(pg.resolve(strict=True) == pg and stat.S_ISDIR(pg_info.st_mode), "redirected_directory")
    # The pinned image initializes its shared socket directory as uid 70 (postgres).
    require(
        (pg_info.st_uid, stat.S_IMODE(pg_info.st_mode))
        in {
            (os.geteuid(), 0o777),
            (70, 0o1775),
            (65534, 0o1775),
        },
        "postgres_directory_owner",
    )
    for name, mode in (("broker", 0o700), ("stage", 0o700)):
        directory(root / name, mode)
    return root


def check_database(env, *, empty, marker=None):
    import psycopg

    check_environment(env)
    run = env["AQ_INSTANCE_ID"]
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
        cur.execute(
            """SELECT current_database(), current_user, inet_server_addr() IS NULL,
            current_setting('listen_addresses'), current_setting(%s, true),
            current_setting('transaction_read_only'), rolsuper, rolcreaterole,
            rolreplication, rolbypassrls, current_setting('server_version'), rolcreatedb
            FROM pg_roles WHERE rolname=current_user""",
            (POSTGRES_MARKER,),
        )
        row = cur.fetchone()
        require(
            row is not None
            and row[:-2]
            == (
                env["POSTGRES_DB"],
                env["POSTGRES_USER"],
                True,
                "",
                marker or run,
                "on",
                False,
                False,
                False,
                False,
            )
            and row[-1] == run.startswith("s07-r-"),
            "database_identity",
        )
        cur.execute("""SELECT count(*) FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            AND schemaname !~ '^pg_(toast|temp)'""")
        count = cur.fetchone()[0]
        require(not empty or count == 0, "database_not_empty")
    return {
        "database": row[0],
        "role": row[1],
        "socket_only": True,
        "postgres_version": row[-2],
        "createdb": row[-1],
        "business_tables": count,
        "read_only": True,
    }


def broker_client(env):
    from redis import Redis

    root = check_environment(env)
    path = root / "broker" / "redis.sock"
    info = path.lstat()
    require(
        stat.S_ISSOCK(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "broker_socket_owner",
    )
    return Redis(unix_socket_path=str(path), socket_timeout=1, socket_connect_timeout=1)


def check_broker(env, *, empty, marker=None):
    with broker_client(env) as broker:
        server = broker.info("server")
        require(
            broker.ping()
            and server["tcp_port"] == 0
            and broker.get(BROKER_MARKER) == (marker or env["AQ_INSTANCE_ID"]).encode(),
            "broker_identity",
        )
        keys = broker.dbsize()
        require(not empty or keys == 1, "broker_not_empty")
        return {"socket_only": True, "redis_version": server["redis_version"], "keys": keys}


def check_process_network(host_namespace):
    require(re.fullmatch(r"net:\[\d+\]", host_namespace or ""), "missing_host_namespace")
    require(os.readlink("/proc/self/ns/net") != host_namespace, "host_network_namespace")
    # Reuse the S06 no-packet route probe, and bind its expected host identity here.
    require(os.environ.get("S06_HOST_NET_NS") == host_namespace, "invalid_host_namespace")
    return check_network()


def check_postgres_container(info: dict[str, Any], run, container, image, volume=None):
    identity(run)
    require(re.fullmatch(r"[0-9a-f]{64}", container), "invalid_container_id")
    require(info["Id"] == container and info["Name"] == f"/{run}-pg", "container_identity")
    require(
        info["Config"]["Labels"].get("airquality.s07.run") == run and info["Image"] == image,
        "container_owner",
    )
    cfg = info["HostConfig"]
    require(
        cfg["NetworkMode"] == "none"
        and not cfg["PortBindings"]
        and not cfg["PublishAllPorts"]
        and set(info["NetworkSettings"].get("Networks") or {}) <= {"none"},
        "external_network",
    )
    require(
        not cfg["Privileged"] and not cfg["Binds"] and not cfg.get("VolumesFrom"),
        "unsafe_container",
    )
    source = str(work_root(run) / "pg")
    socket = "/run/postgresql"
    data = "/var/lib/postgresql/data"
    expected = {("bind", source, socket)}
    if volume is not None:
        require(volume == f"{run}-pgdata", "foreign_volume")
        expected.add(("volume", volume, data))
    mounts = cfg.get("Mounts") or []
    require(
        {(m["Type"], m["Source"], m["Target"]) for m in mounts} == expected
        and len(mounts) == len(expected)
        and all(not m.get("ReadOnly", False) for m in mounts),
        "foreign_bind",
    )
    require(set(cfg.get("Tmpfs") or {}) == (set() if volume else {data}), "persistent_data")
    observed = {
        (m["Type"], m.get("Name", m.get("Source", "")), m["Destination"]) for m in info["Mounts"]
    }
    if volume is None:
        observed.discard(("tmpfs", "", data))
    require(observed == expected and all(m["RW"] for m in info["Mounts"]), "foreign_mount")
