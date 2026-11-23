"""Launch applications only after verify_s07 has checked the fresh infrastructure."""

import json
import re
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError

from s01_runtime import Client
from s06_guard import IsolationError, require


class Applications:
    def __init__(self, project):
        self.project = project
        self.tag = f"{project.name}-app:local"
        self.image = None
        self.ids = {}
        self.http = None

    def initialize(self):
        p = self.project
        p.inspect_started(p.name)
        require(not p.docker("image", "ls", "-q", self.tag), "image_tag_exists")
        p.compose("build", "migrate", timeout=720)
        self.image = p.docker("image", "inspect", "--format", "{{.Id}}", self.tag)
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", self.image), "invalid_application_image")
        lock = (p.root / "beat.lock").stat()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        require(re.fullmatch(r"[a-f0-9-]{36}", boot), "invalid_boot_id")
        # The name, values and two resource IDs were allocated by this guard.
        binding = f"{boot}:{lock.st_dev}:{lock.st_ino}:{lock.st_uid}"
        p.docker(
            "exec",
            "-i",
            p.containers["db"],
            "psql",
            "-Xq",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            p.db,
            stdin=f"ALTER DATABASE {p.db} SET airquality.beat_mode='compose';\nALTER DATABASE {p.db} SET airquality.beat_lock='{binding}';\n",
        )
        redis = p.containers["redis"]
        require(
            p.docker(
                "exec", redis, "redis-cli", "--raw", "SET", "airquality:s07:instance", p.name, "NX"
            )
            == "OK",
            "broker_marker_present",
        )
        p.receipt["application_image"] = self.image
        p.receipt["beat_lock_registration"] = {
            "dev": lock.st_dev,
            "ino": lock.st_ino,
            "mode": "compose",
        }

    def check(self, name, cid):
        p = self.project
        require(re.fullmatch(r"[0-9a-f]{64}", cid), "application_id")
        (info,) = json.loads(p.docker("container", "inspect", cid))
        labels = info["Config"]["Labels"]
        require(
            info["Id"] == cid
            and info["Image"] == self.image
            and labels.get("com.docker.compose.project") == p.name
            and labels.get("com.docker.compose.service") == name,
            "application_owner",
        )
        networks = {f"{p.name}_backend"}
        if name == "web":
            networks.add(f"{p.name}_front")
        require(
            set(info["NetworkSettings"]["Networks"]) == networks
            and not info["HostConfig"]["Privileged"],
            "application_network",
        )
        for network in networks:
            require(
                p.owned_resource("network", network.split("_")[-1])
                == p.resources["network", network.split("_")[-1]],
                "replaced_network",
            )
        mounts = {
            (m["Type"], m.get("Source", ""), m["Destination"], m["RW"]) for m in info["Mounts"]
        }
        expected = {
            "worker": {("tmpfs", "", "/run/s02-history", True)},
            "beat": {("bind", str(p.root / "beat.lock"), "/run/aq-beat.lock", False)},
        }.get("worker" if name == "worker-b" else name, set())
        if name in {"worker", "worker-b"}:
            flags = info["HostConfig"].get("Tmpfs") or {}
            require(
                set(flags) == {"/run/s02-history"}
                and set(flags["/run/s02-history"].split(","))
                == {
                    "rw",
                    "noexec",
                    "nosuid",
                    "nodev",
                    "size=32m",
                    "mode=0700",
                    "uid=10001",
                    "gid=10001",
                },
                "worker_tmpfs",
            )
            require(not mounts or mounts == expected, "application_mounts")
        else:
            require(mounts == expected, "application_mounts")
        return info

    def start(self, name):
        require(
            (
                name in {"migrate", "web", "worker", "beat"}
                or self.project.flows
                and name in {"worker-b", "s03-origin"}
            )
            and name not in self.ids,
            "invalid_application_service",
        )
        p = self.project
        p.compose("create", "--no-recreate", "--pull", "never", "--no-build", name)
        cid = p.compose("ps", "-a", "-q", name)
        self.ids[name] = cid
        if name == "web" and ("network", "front") not in p.resources:
            p.resources["network", "front"] = p.owned_resource("network", "front")
        self.check(name, cid)
        p.docker("start", cid)
        if name == "migrate":
            require(p.docker("wait", cid) == "0", "migration_failed")
        return cid

    def ready(self):
        p = self.project
        address = p.compose("port", "web", "8000")
        require(re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", address), "web_binding")
        self.http = "http://" + address
        client = Client(self.http)
        end = monotonic() + 30
        while monotonic() < end:
            try:
                if client.call("/health/ready/") == (200, {"web": "ok", "pg": "ok"}):
                    info = self.project.docker(
                        "exec", "--user", "10001:10001", self.ids["web"], "cat", "/proc/1/status"
                    )
                    status = dict(line.split(":", 1) for line in info.splitlines() if ":" in line)
                    require(
                        set(status["Uid"].split()) == {"10001"}
                        and int(status["NoNewPrivs"]) == 1
                        and all(int(status[k], 16) == 0 for k in ("CapEff", "CapPrm", "CapAmb")),
                        "wsgi_identity",
                    )
                    routes = self.project.docker(
                        "exec", "--user", "10001:10001", self.ids["web"], "cat", "/proc/net/route"
                    )
                    require(
                        not any(line.split()[1] == "00000000" for line in routes.splitlines()[1:]),
                        "wsgi_external_route",
                    )
                    self.project.receipt["wsgi_network"] = {
                        "loopback_http": 200,
                        "default_ipv4_routes": 0,
                        "uid": 10001,
                        "effective_capabilities": 0,
                        "no_new_privileges": True,
                    }
                    return self.http
            except (URLError, OSError):
                pass
            sleep(0.25)
        raise IsolationError("web_not_ready")

    def cleanup(self):
        p = self.project
        for name, cid in reversed(self.ids.items()):
            info = self.check(name, cid)
            if info["State"]["Running"]:
                p.docker("stop", "--time", "12", cid)
            p.docker("rm", cid)
        if self.image:
            require(
                p.docker("image", "inspect", "--format", "{{.Id}}", self.tag) == self.image,
                "replaced_application_tag",
            )
            p.docker("image", "rm", self.tag)


@contextmanager
def applications(p):
    apps = Applications(p)
    try:
        apps.initialize()
        yield apps
    finally:
        apps.cleanup()


def startup(p):
    evidence = {"scope": "V02-A/startup", "result": "FAIL"}
    try:
        with applications(p) as apps:
            apps.start("migrate")
            web = apps.start("web")
            apps.ready()
            worker = apps.start("worker")
            beat = apps.start("beat")
            end = monotonic() + 35
            while monotonic() < end:
                for name, cid in (("worker", worker), ("beat", beat)):
                    state = apps.check(name, cid)["State"]
                    require(state["Running"], name + "_exit_" + str(state["ExitCode"]))
                count = p.app_sql(
                    "SELECT count(DISTINCT component) FROM accounts_servicepulse WHERE component IN ('beat','worker') AND observed_at>clock_timestamp()-interval '30 seconds'"
                )
                if count == "2":
                    break
                sleep(0.4)
            else:
                raise IsolationError("component_pulses_missing")
            evidence.update(
                result="PASS",
                wsgi=apps.check("web", web)["Config"]["Cmd"],
                health=200,
                recent_worker_and_beat=True,
                image=apps.image,
            )
    except Exception as exc:
        evidence["error"] = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
        raise
    finally:
        (p.root / "startup.json").write_text(json.dumps(evidence, indent=2) + "\n")
