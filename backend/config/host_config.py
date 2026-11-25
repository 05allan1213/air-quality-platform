"""Validation for the explicit offline host instance. No dotenv or shell evaluation."""

import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

POSTGRES_MARKER = "airquality.s07_instance"
BROKER_MARKER = "airquality:s07:instance"
QUEUES = frozenset({"s01.fixture", "s02.history", "s03.collection"})
REQUIRED = {
    "AQ_INSTANCE_ID",
    "AQ_HOST_ROOT",
    "DJANGO_SECRET_KEY",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "CELERY_BROKER_URL",
}
OVERRIDES = {"WEB_PORT", "DJANGO_ALLOWED_HOSTS", "COOKIE_SECURE"}
CONFIG_KEYS = (
    REQUIRED
    | OVERRIDES
    | {
        "AQ_SPA_DIST",
        "AQ_STATIC_ROOT",
        "AQ_WORKER_NODE",
        "CNEMC_MODE",
        "CNEMC_FIXTURE_HTTP",
        "S03_FIXTURE_HTTP",
        "AQ_S08_FIXTURE_SCHEDULE",
        "S02_STAGE_DIR",
        "S06_BACKUP_DIR",
        "S06_AUTHORITY_DIR",
        "AQ_BEAT_LOCK_FILE",
    }
)


class StartupError(RuntimeError):
    """A safe, operator-facing startup diagnostic."""


def require(condition, reason):
    if not condition:
        raise StartupError(reason)


def config_file(selected):
    path = Path(selected).expanduser().absolute()
    require(path.resolve() == path, "redirected_config")
    require(path != Path(__file__).resolve().parents[2] / ".env", "shared_config_refused")
    # Open first and inspect the same descriptor that supplies the configuration.
    try:
        with open(
            path,
            encoding="utf-8",
            opener=lambda path, flags: os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK),
        ) as source:
            info = os.fstat(source.fileno())
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1
                and os.path.samestat(info, path.lstat())
                and info.st_size <= 16_384,
                "unsafe_config_file",
            )
            payload = source.read(16_385)
    except (OSError, UnicodeError):
        raise StartupError("config_unavailable") from None
    require(len(payload) <= 16_384, "config_too_large")
    values = {}
    for number, line in enumerate(payload.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(
            sep
            and key in CONFIG_KEYS
            and key not in values
            and not re.search(r"[\x00-\x20\x7f'\"`$;|&<>\\]", value),
            f"invalid_config_line_{number}",
        )
        values[key] = value
    return values


def config_values(environment, selected=None):
    for key in environment:
        if (
            key.startswith(("PG", "GUNICORN_"))
            or key.upper().endswith("_PROXY")
            or key in {"DJANGO_SETTINGS_MODULE", "DATABASE_URL", "CELERY_RESULT_BACKEND"}
            or (key.startswith(("CELERY_", "CNEMC_")) and key not in CONFIG_KEYS)
        ):
            raise StartupError(f"unsupported_environment_{key}")
    explicit = selected or environment.get("AQ_HOST_CONFIG")
    require(
        not (selected and environment.get("AQ_HOST_CONFIG"))
        or Path(selected) == Path(environment["AQ_HOST_CONFIG"]),
        "conflicting_host_config",
    )
    default = Path(environment.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    default /= "air-quality-platform/backend.env"
    values = config_file(explicit or default) if explicit or default.exists() else {}
    for key in CONFIG_KEYS & environment.keys():
        require(
            key not in values or key in OVERRIDES or environment[key] == values[key],
            f"conflicting_{key}",
        )
        values[key] = environment[key]
    for key in sorted(REQUIRED):
        require(values.get(key), f"missing_{key}")
    return values


def private_root(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "redirected_private_directory")
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "unsafe_private_directory",
    )
    return path


def number(value, low, high, error):
    require(value.isascii() and value.isdecimal() and len(value) <= 5, error)
    parsed = int(value)
    require(low <= parsed <= high, error)
    return parsed


def endpoints(values, *, allow_missing_broker=False):
    root = private_root(values["AQ_HOST_ROOT"])
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9-]{7,62}", values["AQ_INSTANCE_ID"])
        and all(
            re.fullmatch(r"[a-z][a-z0-9_]{0,62}", values[k])
            for k in ("POSTGRES_DB", "POSTGRES_USER")
        ),
        "invalid_instance_identity",
    )
    for key, expected in (
        ("CNEMC_MODE", "off"),
        ("CNEMC_FIXTURE_HTTP", "0"),
        ("S03_FIXTURE_HTTP", "0"),
    ):
        require(values.setdefault(key, expected) == expected, f"offline_requires_{key}")
    fixture = values.get("AQ_S08_FIXTURE_SCHEDULE", "0")
    require(fixture in {"0", "1"}, "invalid_schedule_fixture_mode")
    if fixture == "1":
        # Normal offline instances cannot opt in. database()/broker_ready() verify
        # the actual task-owned instance markers before any service is started.
        suffix = values["AQ_INSTANCE_ID"][-12:]
        require(
            re.fullmatch(r"s08-b-[0-9a-f]{12}", values["AQ_INSTANCE_ID"])
            and values["POSTGRES_DB"] == f"s08_db_{suffix}"
            and values["POSTGRES_USER"] == f"s08_app_{suffix}"
            and root == Path("/tmp/s08-host")
            and values["POSTGRES_HOST"] == str(root / "pg")
            and values["CELERY_BROKER_URL"] == f"redis+socket://{root}/broker/redis.sock",
            "schedule_fixture_instance_required",
        )
    db_port = number(values["POSTGRES_PORT"], 1, 65535, "invalid_POSTGRES_PORT")
    host = values["POSTGRES_HOST"]
    socket_only = host != "127.0.0.1"
    if socket_only:
        path = Path(host)
        require(
            path != root and path.is_relative_to(root) and path.resolve() == path,
            "invalid_postgres_socket",
        )
        require(
            stat.S_ISSOCK((path / f".s.PGSQL.{db_port}").lstat().st_mode), "invalid_postgres_socket"
        )
    else:
        require(db_port >= 1024 and db_port != 5432, "postgres_requires_dedicated_port")
    broker = urlsplit(values["CELERY_BROKER_URL"])
    require(not broker.query and not broker.fragment, "invalid_broker_url")
    if broker.scheme == "redis+socket":
        path = Path(broker.path)
        require(
            not broker.netloc
            and path.parent != root
            and path.is_relative_to(root)
            and path.resolve() == path,
            "invalid_broker_socket",
        )
        try:
            info = path.lstat()
        except FileNotFoundError:
            require(allow_missing_broker, "broker_socket_missing")
        else:
            require(
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.geteuid()
                and not (info.st_mode & 0o077),
                "invalid_broker_socket",
            )
    else:
        require(
            broker.scheme == "redis"
            and broker.hostname == "127.0.0.1"
            and broker.password
            and broker.port is not None
            and broker.port >= 1024
            and broker.port != 6379
            and broker.path in {"", "/0"},
            "broker_requires_dedicated_protected_endpoint",
        )
    port = number(values.get("WEB_PORT", "8000"), 1024, 65535, "invalid_WEB_PORT")
    node = values.setdefault("AQ_WORKER_NODE", f"worker@{values['AQ_INSTANCE_ID']}")
    require(
        re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+", node) and len(node) <= 100,
        "invalid_worker_node",
    )
    require(values.get("COOKIE_SECURE", "0") in {"0", "1"}, "invalid_COOKIE_SECURE")
    require(
        set(values.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(","))
        <= {"127.0.0.1", "localhost", "[::1]"},
        "host_requires_loopback",
    )
    require(len(values["DJANGO_SECRET_KEY"]) >= 32, "weak_DJANGO_SECRET_KEY")
    values.setdefault("AQ_STATIC_ROOT", str(root / "staticfiles"))
    return root, port, socket_only


def database(values, socket_only):
    import psycopg

    db = psycopg.connect(
        host=values["POSTGRES_HOST"],
        port=values["POSTGRES_PORT"],
        dbname=values["POSTGRES_DB"],
        user=values["POSTGRES_USER"],
        password=values["POSTGRES_PASSWORD"],
        connect_timeout=2,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=1000",
    )
    try:
        actual = db.execute(
            """SELECT current_database(), current_user, inet_server_addr() IS NULL,
              current_setting(%s, true), current_setting('transaction_read_only'),
              rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls,
              datdba = pg_roles.oid
            FROM pg_roles CROSS JOIN pg_database
            WHERE rolname = current_user AND datname = current_database()""",
            (POSTGRES_MARKER,),
        ).fetchone()
        require(
            actual
            == (
                values["POSTGRES_DB"],
                values["POSTGRES_USER"],
                socket_only,
                values["AQ_INSTANCE_ID"],
                "on",
                False,
                True,
            ),
            "database_identity",
        )
        return db
    except BaseException:
        db.close()
        raise


def broker_ready(values, *, allow_unavailable=False):
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.exceptions import AuthenticationError, ConnectionError, TimeoutError
    from redis.retry import Retry

    url = values["CELERY_BROKER_URL"].replace("redis+socket://", "unix://", 1)
    try:
        with Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1, retry=Retry(NoBackoff(), 0)
        ) as client:
            require(
                client.ping() and client.get(BROKER_MARKER) == values["AQ_INSTANCE_ID"].encode(),
                "broker_identity",
            )
            if url.startswith("unix://"):
                require(client.info("server")["tcp_port"] == 0, "broker_not_socket_only")
    except AuthenticationError:
        raise StartupError("broker_authentication_failed") from None
    except (ConnectionError, TimeoutError):
        require(allow_unavailable, "broker_unavailable")
