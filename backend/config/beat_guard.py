"""Exclusive, fail-closed admission for the actual scheduler and publisher."""

import fcntl
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from psycopg import Connection, Error
from redis.exceptions import RedisError

SCHEDULER = "config.scheduler:ObservedScheduler"
COMPOSE_FILE = Path("/run/aq-beat.lock")
BROKER_MARKER = "airquality:s07:instance"
INSTANCE_MARKER = "airquality.s07_instance"
LOCK_MARKER = "airquality.beat_lock"
MODE_MARKER = "airquality.beat_mode"
LOCK_NAMESPACE, LOCK_KEY = 1095840852, 1
LOCAL_FILESYSTEMS = {"ext4", "xfs", "btrfs"}
PG_OVERRIDES = {"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"}


class BeatGuardError(RuntimeError):
    """Startup refusal with a controlled error code."""


class BeatLeaseLost(BaseException):
    """Cross Celery's catch-and-continue publish boundary and exit the scheduler."""


class LockSession(Connection):
    def wait(self, gen, interval=0.1, timeout=None):
        # statement_timeout handles a reachable server; this also bounds a stalled socket.
        return super().wait(gen, interval=interval, timeout=2.5 if timeout is None else timeout)


def require(condition, code):
    if not condition:
        raise BeatGuardError(code)


def unescape_mount_field(field):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), field)


def lock_mount(path):
    """Return the visible mount, including its device, root and protection."""
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        mount, fs = line.split(" - ", 1)
        fields = mount.split()
        target = Path(unescape_mount_field(fields[4]))
        if path.is_relative_to(target):
            mounts.append((target, tuple(fields[:4]), fields[5].split(","), fs.split()[0]))
    require(mounts, "beat_mount_unknown")
    return max(mounts, key=lambda row: len(row[0].parts))


def lock_identity(info):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(re.fullmatch(r"[0-9a-f-]{36}", boot), "beat_kernel_unknown")
    return f"{boot}:{info.st_dev}:{info.st_ino}:{info.st_uid}"


@dataclass
class LockFile:
    path: Path
    fd: int
    identity: str
    mount: tuple

    @classmethod
    def open(cls, path):
        fd = None
        try:
            require(path.is_absolute() and path.resolve(strict=True) == path, "beat_lock_path")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            lock = cls(path, fd, lock_identity(os.fstat(fd)), lock_mount(path))
            lock.verify(lock.identity)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BaseException as error:
            if fd is not None:
                os.close(fd)
            if isinstance(error, BlockingIOError):
                raise BeatGuardError("beat_file_busy") from None
            if isinstance(error, OSError):
                raise BeatGuardError("beat_lock_path") from None
            raise

    def verify(self, registered):
        info = os.fstat(self.fd)
        visible = self.path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and not info.st_mode & 0o022
            and lock_identity(info) == registered
            and (info.st_dev, info.st_ino) == (visible.st_dev, visible.st_ino)
            and self.path.resolve(strict=True) == self.path,
            "beat_lock_identity",
        )
        mount = lock_mount(self.path)
        require(mount == self.mount and mount[3] in LOCAL_FILESYSTEMS, "beat_mount_identity")
        read_only = "ro" in mount[2]
        if self.path == COMPOSE_FILE:
            require(mount[0] == self.path and read_only, "beat_compose_bind")
        else:
            require(
                read_only
                or (
                    self.path.parent.stat().st_uid != os.geteuid()
                    and not os.access(self.path.parent, os.W_OK)
                    and info.st_uid != os.geteuid()
                    and not os.access(self.path, os.W_OK)
                ),
                "beat_lock_replaceable",
            )

    def close(self):
        os.close(self.fd)


def check_broker(channel, instance):
    # Use the selected publisher channel. A separately configured probe might prove the
    # wrong broker while `celery --broker ...` directs real publication elsewhere.
    from kombu.transport.redis import Channel

    require(isinstance(channel, Channel) and not channel.global_keyprefix, "beat_broker_type")
    try:
        client = channel.client
        require(
            client.connection_pool.connection_kwargs.get("db", 0) == 0
            and client.get(BROKER_MARKER) == instance.encode("ascii"),
            "beat_broker_identity",
        )
    except RedisError:
        raise BeatGuardError("beat_broker_unavailable") from None


class BeatLease:
    def __init__(self):
        from django.conf import settings

        self.instance = os.environ.get("AQ_INSTANCE_ID", "")
        require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", self.instance), "beat_instance_required")
        require(not PG_OVERRIDES.intersection(os.environ), "beat_pg_override")
        self.publisher = os.environ.get("AQ_BEAT_PUBLISHER_ID", uuid4().hex)
        require(re.fullmatch(r"[a-f0-9]{32}", self.publisher), "beat_publisher_invalid")
        cfg = settings.DATABASES["default"]
        require(cfg["ENGINE"] == "django.db.backends.postgresql", "beat_database_engine")
        require(all(cfg[k] for k in ("NAME", "USER", "HOST", "PORT")), "beat_database_config")
        path = Path(os.environ.get("AQ_BEAT_LOCK_FILE", ""))
        self.file = LockFile.open(path)
        self.db = None
        self.active = False
        try:
            self.db = LockSession.connect(
                dbname=cfg["NAME"],
                user=cfg["USER"],
                password=cfg["PASSWORD"],
                host=cfg["HOST"],
                port=cfg["PORT"],
                application_name=f"aq-beat-{self.publisher}",
                connect_timeout=2,
                options="-c default_transaction_read_only=on -c statement_timeout=1800",
                autocommit=True,
            )
            row = self.db.execute(
                """SELECT current_database(), current_user, rolsuper,
                          current_setting(%s, true), current_setting(%s, true),
                          current_setting(%s, true), pg_backend_pid()
                   FROM pg_roles WHERE rolname = current_user""",
                (INSTANCE_MARKER, LOCK_MARKER, MODE_MARKER),
            ).fetchone()
            require(
                row is not None and row[:4] == (cfg["NAME"], cfg["USER"], False, self.instance),
                "beat_database_identity",
            )
            _, _, _, _, self.registered, mode, self.pid = row
            local_mode = "compose" if path == COMPOSE_FILE else "host"
            require(mode in {local_mode, "shared"}, "beat_mode_unregistered")
            self.file.verify(self.registered)
            result = self.db.execute(
                "SELECT pg_try_advisory_lock(%s::int, %s::int)", (LOCK_NAMESPACE, LOCK_KEY)
            ).fetchone()
            require(result == (True,), "beat_database_busy")
            self.active = True
        except BaseException as error:
            self.close()
            if isinstance(error, Error):
                raise BeatGuardError("beat_database_unavailable") from None
            if isinstance(error, OSError):
                raise BeatGuardError("beat_lock_identity") from None
            raise

    def verify(self):
        if not self.active:
            raise BeatLeaseLost("beat_lease_closed")
        try:
            self.file.verify(self.registered)
            row = self.db.execute(
                """SELECT pg_backend_pid(), EXISTS (
                       SELECT 1 FROM pg_locks
                       WHERE locktype = 'advisory' AND pid = pg_backend_pid()
                         AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
                         AND classid = %s::oid AND objid = %s::oid AND objsubid = 2
                         AND mode = 'ExclusiveLock' AND granted
                   )""",
                (LOCK_NAMESPACE, LOCK_KEY),
            ).fetchone()
            require(row == (self.pid, True), "beat_database_lock_lost")
        except (Error, OSError, BeatGuardError) as error:
            self.active = False
            code = str(error) if isinstance(error, BeatGuardError) else "beat_lease_lost"
            raise BeatLeaseLost(code) from None

    def verify_broker(self, channel):
        try:
            check_broker(channel, self.instance)
        except BeatGuardError as error:
            self.active = False
            raise BeatLeaseLost(str(error)) from None

    def close(self):
        self.active = False
        try:
            if self.db is not None:
                self.db.close()
                self.db = None
        finally:
            self.file.close()
