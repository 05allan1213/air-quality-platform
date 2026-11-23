"""Native-entry V14. The sandbox can only stop/start its guarded PostgreSQL container."""

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic

from s01_runtime import ROOT, wait_for
from s06_guard import require
from s07_host_guard import (
    broker_client,
    check_broker,
    check_database,
    check_environment,
    check_postgres_container,
    check_process_network,
)
from s07_offline import OfflineChecks, report_at
from s07_startup_checks import StartupChecks, connect, prepare, verify_lock
from verify_s06 import docker, inspect, ready

LABEL = "airquality.s07.run"


def volume_identity(run, volume):
    require(volume == f"{run}-pgdata", "v14_foreign_volume")
    (info,) = json.loads(docker("volume", "inspect", volume))
    require(
        info["Name"] == volume
        and info["Labels"] == {LABEL: run}
        and info["Driver"] == "local"
        and info["Scope"] == "local"
        and not info.get("Options"),
        "v14_volume_owner",
    )
    return {"name": volume, "created_at": info["CreatedAt"]}


@contextmanager
def owned_volume(env, receipt, *, enabled):
    if not enabled:
        yield None
        return
    run = env["AQ_INSTANCE_ID"]
    volume = f"{run}-pgdata"
    require(
        not docker("volume", "ls", "-q", "--filter", f"name=^{volume}$"),
        "v14_volume_exists",
    )
    require(
        docker("volume", "create", "--label", f"{LABEL}={run}", volume) == volume,
        "v14_volume_create",
    )
    recorded = receipt["postgres_volume"] = volume_identity(run, volume)
    try:
        yield volume
    finally:
        require(volume_identity(run, volume) == recorded, "v14_volume_replaced")
        docker("volume", "rm", volume)


def postgres_bridge(command, env, receipt):
    """Allowlist only exact lifecycle operations. No Docker socket enters bwrap."""
    root = check_environment(env)
    owner = env["AQ_INSTANCE_ID"]
    recorded = receipt["postgres_volume"]
    cid, image = receipt["postgres_container"], receipt["postgres_image"]
    address = root / "pg-control"
    require(len(os.fsencode(address)) < 108, "v14_control_path_length")

    def operate(raw):
        require(raw in {b"start\n", b"stop\n", b"status\n"}, "v14_invalid_postgres_opcode")
        before = volume_identity(owner, recorded["name"])
        require(before == recorded, "v14_volume_replaced")
        info = inspect(cid)
        check_postgres_container(info, owner, cid, image, recorded["name"])
        if raw == b"stop\n":
            require(info["State"]["Running"], "v14_pg_already_stopped")
            check_database(env, empty=False)
            docker("stop", "--time", "15", cid)
        elif raw == b"start\n":
            require(not info["State"]["Running"], "v14_pg_already_running")
            docker("start", cid)
            wait_for("V14 host PostgreSQL restart", lambda: ready(cid), seconds=30)
            check_database(env, empty=False)
        latest = inspect(cid)
        check_postgres_container(latest, owner, cid, image, recorded["name"])
        return {"running": latest["State"]["Running"], "volume": before}

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(address))
            server.settimeout(0.5)
            server.listen(1)
            child = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            try:
                deadline = monotonic() + 400
                while child.poll() is None:
                    require(monotonic() < deadline, "v14_host_timeout")
                    try:
                        peer, _ = server.accept()
                    except TimeoutError:
                        continue
                    with peer, peer.makefile("rwb") as stream:
                        peer.settimeout(5)
                        data = operate(stream.readline(24))
                        stream.write(json.dumps(data).encode() + b"\n")
                        stream.flush()
                return child.returncode
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=4)
    finally:
        address.unlink(missing_ok=True)


class HostOffline:
    def __init__(self, root, work, env, infra, start_broker, report, password):
        self.root = root
        self.env = env
        self.infra = infra
        self.start_broker = start_broker
        self.password = password
        self.launcher = StartupChecks(root, work, env.copy(), report)
        self.base = self.launcher.base
        self.apps = {}
        self.stack = ExitStack()

    def command(self, action):
        check_database(self.env, empty=False)
        check_broker(self.env, empty=False)
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/s07_offline_seed.py"), action],
            env=self.env | {"S07_OFFLINE_PASSWORD": self.password},
            capture_output=True,
            text=True,
            timeout=25,
        )
        require(result.returncode == 0, f"v14_host_fixture_{action}_{result.returncode}")
        return json.loads(result.stdout)

    def seed(self):
        return self.command("seed")

    def expire(self):
        return self.command("expire")

    def pg(self, action):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(42)
            peer.connect(str(self.root / "pg-control"))
            with peer.makefile("rwb") as stream:
                stream.write(action.encode() + b"\n")
                stream.flush()
                return json.loads(stream.readline(1024))

    def sql(self, text):
        with connect(self.env) as conn:
            return json.loads(conn.execute(text).fetchone()[0])

    def app(self, name, *args):
        require(name not in self.apps, "v14_application_exists")
        lifetime = ExitStack()
        self.stack.callback(lifetime.close)
        proc, events, case = lifetime.enter_context(self.launcher.launch("v14_" + name, *args))
        self.apps[name] = (proc, events, case, lifetime)
        roles = ("worker", "beat", "web") if name == "all" else (name,)
        self.launcher.wait_ready(proc, events, case, roles)

    def stop_app(self, name):
        proc, _, case, lifetime = self.apps.pop(name)
        if proc.poll() is None:
            self.launcher.terminate(proc, signal.SIGTERM, case)
        lifetime.close()

    def all_start(self):
        self.app("all")

    def all_stop(self):
        self.stop_app("all")

    def start(self, name):
        if name == "pg":
            require(self.pg("start")["running"], "v14_pg_start")
        elif name == "redis":
            self.start_broker(self.infra, self.env)
        else:
            self.app(name, name)

    def stop(self, name):
        if name == "pg":
            require(not self.pg("stop")["running"], "v14_pg_stop")
        elif name == "redis":
            self.infra.stop("redis")
        else:
            self.stop_app(name)

    def reject_applications(self):
        require(not self.pg("status")["running"], "v14_reverse_requires_pg_down")
        check_broker(self.env, empty=False)
        rejected = {}
        for name in ("worker", "beat"):
            with self.launcher.launch(f"v14_early_{name}", name) as (proc, events, _):
                require(proc.wait(timeout=12) == 1, "v14_reverse_entry_started")
                require(
                    "[s07] startup_failed_FileNotFoundError" in events(),
                    "v14_reverse_entry_cause",
                )
                rejected[name] = {"exit": proc.returncode, "failure": "FileNotFoundError"}
        return rejected

    def recover_applications(self):
        for name in ("worker", "beat"):
            if self.apps[name][0].poll() is not None:
                self.stop_app(name)
                self.start(name)

    def beat_lost(self):
        require(not self.pg("status")["running"], "v14_beat_loss_requires_pg_down")
        proc, events, _, _ = self.apps["beat"]
        require(proc.wait(timeout=18) == 1, "v14_beat_loss_exit_code")
        require("[s07] beat_exited_1" in events(), "v14_beat_loss_cause")
        return {"exit": proc.returncode, "diagnostic": "beat_exited_1", "pg_unavailable": True}

    def volume(self):
        return self.pg("status")["volume"]

    def broker_id(self):
        check_broker(self.env, empty=False)
        with broker_client(self.env) as broker:
            return broker.info("server")["run_id"]

    def queued_id(self, run):
        with broker_client(self.env) as broker:
            return any(
                json.loads(msg)["headers"]["id"] == run
                for msg in broker.lrange("s01.fixture", 0, -1)
            )

    def stopped(self):
        states = {
            "pg": self.pg("status")["running"],
            "redis": "redis" in self.infra.children,
            **{role: bool(self.apps) for role in ("web", "worker", "beat")},
        }
        require(not any(states.values()), "v14_host_still_running")
        return states

    def network(self):
        network = check_process_network(self.env["S06_HOST_NET_NS"])
        network["resolver_visible"] = Path("/etc/resolv.conf").exists()
        network["proxy_variables"] = sorted(k for k in self.env if k.upper().endswith("_PROXY"))
        require(
            not network["resolver_visible"] and not network["proxy_variables"],
            "v14_host_dns_or_proxy",
        )
        return network


def verify(root, guards, runtime, start_broker):
    require(guards["status"] == "PASS", "v01_required")
    env = os.environ.copy()
    require(check_environment(env) == root, "v14_host_root")
    env["AQ_BEAT_LOCK_FILE"] = str(root / "V02" / "beat.lock")
    result = root / "V14" / "host.json"
    try:
        with report_at(result, env["AQ_INSTANCE_ID"], "python main.py") as report:
            report["cases"] = []
            with tempfile.TemporaryDirectory(prefix="offline-", dir=root) as folder:
                work = Path(folder)
                env["AQ_STATIC_ROOT"] = str(work / "public-static")
                verify_lock(env)
                prepare(env, work)
                password = secrets.token_hex(24)
                host = HostOffline(root, work, env, runtime, start_broker, report, password)
                # A clean B has no staged media. Web/Worker use the launcher's default stage.
                host.launcher.env.pop("S02_STAGE_DIR")
                with host.stack:
                    OfflineChecks(host, report, password).run()
    finally:
        for role in ("web", "worker", "beat"):
            (root / f"launch-{role}.lock").unlink(missing_ok=True)
