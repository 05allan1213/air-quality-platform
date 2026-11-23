"""Compose side of V03 using the task-owned B Unix sockets and the same file lock."""

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from time import monotonic
from uuid import uuid4

from s01_runtime import ROOT
from s06_guard import IsolationError, require
from s07_host_guard import check_broker, check_database, check_environment
from verify_s06 import docker, inspect

LABEL = "airquality.s07.run"
TARGET_LOCK = "/run/aq-beat.lock"
BEAT = ["celery", "-A", "config", "beat", "-l", "INFO"]


class ComposePublishers:
    def __init__(self, environment, snapshot, receipt):
        self.env = environment
        self.root = check_environment(environment)
        self.run = environment["AQ_INSTANCE_ID"]
        self.snapshot = snapshot
        self.receipt = receipt
        self.tag = f"{self.run}-app:local"
        self.containers = {}
        self.image = None
        self.lock = self.root / "V02" / "beat.lock"
        self.unknown = self.root / "stage" / "foreign-beat.lock"

    def call(self, *args, timeout=50, input=None):
        private = self.env | {
            "AQ_BEAT_LOCK_SOURCE": str(self.lock),
            "AQ_HOST_NETNS": str(os.stat("/proc/self/ns/net").st_ino),
            "POSTGRES_ADMIN_PASSWORD": self.env["POSTGRES_PASSWORD"],
            "COMPOSE_PROJECT_NAME": self.run,
        }
        completed = subprocess.run(
            ["docker", "--host", "unix:///var/run/docker.sock", *args],
            cwd=ROOT,
            env=private,
            check=False,
            capture_output=True,
            text=True,
            input=input,
            timeout=timeout,
        )
        if completed.returncode:
            phase = next((word for word in args if word in {"config", "create", "ps"}), "invoke")
            missing = re.search(r"required variable ([A-Z_][A-Z_0-9]*)", completed.stderr)
            diagnostic = f"_missing_{missing[1]}" if missing else ""
            raise IsolationError(f"singleton_compose_{phase}_{completed.returncode}{diagnostic}")
        return completed.stdout.strip()

    def build(self):
        require(not docker("image", "ls", "-q", self.tag), "singleton_image_tag_exists")
        docker(
            "build",
            "--pull=false",
            "--label",
            f"{LABEL}={self.run}",
            "-t",
            self.tag,
            str(self.snapshot),
            timeout=720,
        )
        self.image = docker("image", "inspect", "--format", "{{.Id}}", self.tag)
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", self.image), "singleton_bad_image_id")
        self.receipt["compose_image"] = self.image
        self.unknown.touch(mode=0o444, exist_ok=False)

    def service(self, mode, publisher):
        primary = self.snapshot / "compose.yaml"
        files = ["--env-file", "/dev/null", "-f", str(primary)]
        if mode.endswith("formal"):
            files += ["-f", str(self.snapshot / "compose.offline.yaml")]
        beat = json.loads(self.call("compose", *files, "config", "--format", "json"))["services"][
            "beat"
        ]
        require(beat["command"] == BEAT and beat["image"] == self.tag, "unexpected_beat_entry")
        require(
            beat["volumes"]
            == [
                {
                    "type": "bind",
                    "source": str(self.lock),
                    "target": TARGET_LOCK,
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            ],
            "unexpected_beat_mount",
        )
        beat.pop("networks")
        beat.pop("depends_on")
        # Keep the launch command and app image from Compose. Only wire the verified private
        # sockets, task label, and host uid needed to share B's isolated Redis socket.
        beat.update(
            network_mode="none",
            user=f"{os.getuid()}:{os.getgid()}",
            labels={LABEL: self.run},
            pull_policy="never",
        )
        beat["environment"].update(
            POSTGRES_HOST="/run/aq-pg",
            CELERY_BROKER_URL="redis+socket:///run/aq-broker/redis.sock",
            AQ_BEAT_PUBLISHER_ID=publisher,
            CNEMC_MODE="off",
        )
        if mode.startswith("unknown-"):
            beat["volumes"][0]["source"] = str(self.unknown)
        for source, target in (("pg", "/run/aq-pg"), ("broker", "/run/aq-broker")):
            beat["volumes"].append(
                {
                    "type": "bind",
                    "source": str(self.root / source),
                    "target": target,
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
        return beat

    def identity(self, name):
        cid, lock = self.containers[name]
        item = inspect(cid)
        labels = item["Config"]["Labels"]
        settings = item["HostConfig"]
        expected = {
            (str(lock), TARGET_LOCK),
            (str(self.root / "pg"), "/run/aq-pg"),
            (str(self.root / "broker"), "/run/aq-broker"),
        }
        require(
            item["Id"] == cid
            and item["Image"] == self.image
            and labels[LABEL] == self.run
            and labels["com.docker.compose.project"] == name
            and labels["com.docker.compose.service"] == "beat"
            and item["Config"]["Cmd"] == BEAT,
            "singleton_compose_ownership",
        )
        require(
            settings["NetworkMode"] == "none"
            and not settings["Privileged"]
            and not settings["PortBindings"]
            and all(not m["RW"] and m["Type"] == "bind" for m in item["Mounts"])
            and {(m["Source"], m["Destination"]) for m in item["Mounts"]} == expected,
            "singleton_compose_isolation",
        )
        return item["State"]

    def start(self, mode):
        require(mode in {"base", "formal", "unknown-formal"}, "singleton_bad_compose_mode")
        check_database(self.env, empty=False)
        check_broker(self.env, empty=False)
        publisher = uuid4().hex
        name = f"{self.run}-{mode}-{publisher[:5]}"
        beat = self.service(mode, publisher)
        config = json.dumps({"services": {"beat": beat}})
        args = ("compose", "--env-file", "/dev/null", "-p", name, "-f", "-")
        self.call(*args, "create", "--no-build", "--pull", "never", "beat", input=config)
        cid = self.call(*args, "ps", "-aq", "beat", input=config)
        require(re.fullmatch(r"[a-f0-9]{64}", cid), "singleton_compose_id")
        self.containers[name] = (cid, Path(beat["volumes"][0]["source"]))
        self.identity(name)
        docker("start", cid)
        return name, publisher

    def connection(self, payload):
        operation, name = payload
        if operation == "start":
            return self.start(name)
        if operation == "replace":
            require(name == "registered", "singleton_bad_rpc")
            old = self.lock.stat().st_ino
            self.lock.unlink()
            self.lock.touch(mode=0o444, exist_ok=False)
            require(self.lock.stat().st_ino != old, "singleton_replacement_identity")
            return [old, self.lock.stat().st_ino]
        require(operation in {"stop", "state"} and name in self.containers, "singleton_bad_rpc")
        state = self.identity(name)
        cid = self.containers[name][0]
        if operation == "stop" and state["Running"]:
            docker("stop", "--time", "5", cid)
            state = self.identity(name)
        if state["Running"]:
            return None
        completed = subprocess.run(
            ["docker", "--host", "unix:///var/run/docker.sock", "logs", cid],
            cwd=ROOT,
            env={"PATH": os.defpath},
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        log = completed.stdout + completed.stderr
        error = re.search(r"beat_(?:file_busy|lock_identity|mode_unregistered)\b", log)
        return {"exit": state["ExitCode"], "error": error.group(0) if error else None}

    def close(self):
        for name, (cid, _) in reversed(self.containers.items()):
            self.connection(("stop", name))
            docker("rm", cid)
        if self.image:
            require(
                docker("image", "inspect", "--format", "{{.Id}}", self.tag) == self.image,
                "singleton_image_replaced",
            )
            docker("image", "rm", self.tag)


def compose_bridge(command, environment, snapshot, receipt):
    compose = ComposePublishers(environment, snapshot, receipt)
    socket_path = compose.root / "rpc"
    child = None
    try:
        compose.build()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            server.settimeout(0.5)
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = monotonic() + 300
            while child.poll() is None and monotonic() < deadline:
                try:
                    peer, _ = server.accept()
                except TimeoutError:
                    continue
                with peer:
                    peer.settimeout(10)
                    with peer.makefile("rb") as stream:
                        payload = json.loads(stream.readline(1024))
                    try:
                        response = {"value": compose.connection(payload)}
                    except IsolationError as error:
                        response = {"error": str(error)}
                    peer.sendall(json.dumps(response).encode() + b"\n")
            require(child.poll() is not None, "singleton_sandbox_timeout")
            return child.returncode
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        socket_path.unlink(missing_ok=True)
        compose.close()
