"""Foreground supervision for the local, single-instance backend."""

import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

from .host_config import StartupError, require


def process_start(pid):
    record = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if record[0] != "Z":
        return int(record[19])
    return None


def process_alive(pid, start):
    try:
        return process_start(pid) == start
    except FileNotFoundError:
        return False


def group_alive(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Children:
    def __init__(self, root, backend, environment):
        self.root = root
        self.backend = backend
        self.environment = environment
        self.children = {}
        self.stopping = False

    @contextmanager
    def signals(self):
        def request_stop(*_):
            self.stopping = True

        previous = {
            sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    @contextmanager
    def lock(self, roles):
        guards = {}
        try:
            for role in sorted(roles):
                fd = os.open(
                    self.root / f"launch-{role}.lock",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                )
                guards[role] = fd
                st = os.fstat(fd)
                require(
                    stat.S_ISREG(st.st_mode)
                    and st.st_uid == os.geteuid()
                    and stat.S_IMODE(st.st_mode) == 0o600
                    and st.st_nlink == 1
                    and st.st_size <= 8192,
                    "invalid_startup_lock",
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise StartupError(f"{role}_already_supervised") from None
                # SIGKILL leaves this record intact. Either a matching process or
                # a surviving group is sufficient to stop a new launch.
                payload = os.read(fd, 8192)
                if payload:
                    try:
                        processes = json.loads(payload)
                        require(isinstance(processes, list), "invalid_startup_identity")
                        for record in processes:
                            pid, started = record
                            require(
                                type(pid) is int
                                and pid > 1
                                and type(started) is int
                                and started > 0,
                                "invalid_startup_identity",
                            )
                            require(
                                not process_alive(pid, started) and not group_alive(pid),
                                f"{role}_residual_process",
                            )
                    except (ValueError, TypeError):
                        raise StartupError("invalid_startup_identity") from None
            self.guards = guards
            yield
        finally:
            for fd in guards.values():
                os.close(fd)

    def save_identity(self, role, record):
        fd = self.guards[role]
        payload = json.dumps(record).encode("ascii")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)
        os.fsync(fd)

    def start(self, name, args, *, role=None, extra_env=None, pass_fds=(), quiet=False):
        if self.stopping:
            raise InterruptedError
        role = role or name
        fd = self.guards[role]
        child = subprocess.Popen(
            [sys.executable, *args],
            cwd=self.backend,
            env=self.environment | (extra_env or {}),
            start_new_session=True,
            pass_fds=(fd, *pass_fds),
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
        )
        self.children[name] = (child, role)
        started = process_start(child.pid)
        if started is None:
            raise StartupError(f"{name}_exited_{child.wait()}")
        self.save_identity(role, [[child.pid, started]])
        print(f"[s07] started {name} pid={child.pid}", flush=True)
        return child

    def status(self, name):
        return self.children[name][0].poll()

    def wait(self, name, seconds):
        deadline = monotonic() + seconds
        while monotonic() < deadline and not self.stopping:
            if (code := self.status(name)) is not None:
                if code != 0:
                    raise StartupError(f"{name}_exited_{code}")
                _, role = self.children.pop(name)
                self.save_identity(role, [])
                return
            sleep(0.05)
        if self.stopping:
            raise InterruptedError
        raise StartupError(f"{name}_timeout")

    def ready(self, name, condition, seconds):
        deadline = monotonic() + seconds
        while monotonic() < deadline and not self.stopping:
            self.assert_running()
            if condition():
                self.assert_running()
                print(f"[s07] ready {name}", flush=True)
                return
            sleep(0.2)
        if self.stopping:
            raise InterruptedError
        raise StartupError(f"{name}_readiness_timeout")

    def assert_running(self):
        for name in self.children:
            if (code := self.status(name)) is not None:
                raise StartupError(f"{name}_exited_{code}")

    def serve(self):
        while not self.stopping:
            self.assert_running()
            sleep(0.2)

    def stop(self):
        forced = False
        order = ["beat", "worker", "s03_reconcile_due", "s02_dispose_due", "web"]
        for name in order:
            if name not in self.children:
                continue
            child, role = self.children[name]
            # Warm TERM must reach Celery's parent only. Its pool members
            # finish their tasks before the parent returns.
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
            deadline = monotonic() + 12
            while monotonic() < deadline:
                child.poll()
                if not group_alive(child.pid):
                    break
                sleep(0.1)
            if group_alive(child.pid):
                forced = True
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait(timeout=5)
            if not group_alive(child.pid):
                self.save_identity(role, [])
            print(f"[s07] stopped {name} exit={child.returncode}", flush=True)
        return not forced
