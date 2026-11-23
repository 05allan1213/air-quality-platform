"""V01-A. Verify a fresh, offline Compose instance before any application process runs."""

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from time import monotonic, sleep

from s01_runtime import ROOT
from s06_guard import IsolationError, require
from s07_handoff import source_digest as handoff_source_digest

LABEL = "airquality.s07.instance"
PROJECT = "com.docker.compose.project"
SERVICES = {"db", "redis", "web", "migrate", "beat", "worker"}
SOCKET = "unix:///var/run/docker.sock"
INIT = ROOT / "docker" / "10-app-role.sql"
PGDATA = "/var/lib/postgresql/data"
PINNED = {
    "db": "postgres:16-alpine@sha256:1d5b02d3a9941fbd52437db4107f8c52d6297dac553ca1e5c299ebb3756b4857",
    "redis": "redis:7-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe",
}


def source_digest():
    """Identify the running source in a checkout or a verified handoff."""
    return handoff_source_digest(ROOT)


def identities(instance):
    require(re.fullmatch(r"s07a-[0-9a-f]{12}", instance or ""), "project_name")
    suffix = instance[5:]
    return f"s07_db_{suffix}", f"s07_app_{suffix}"


def check_config(cfg, instance, *, flows=False):
    db, role = identities(instance)
    require(cfg["name"] == instance, "project_name")
    allowed = SERVICES | ({"worker-b", "s03-origin"} if flows else set())
    require(set(cfg["services"]) == allowed, "service_set")
    require(set(cfg["volumes"]) == {"pgdata"}, "volume_set")
    require(set(cfg["networks"]) == {"front", "backend"}, "network_set")
    for kind, key in (("volumes", "pgdata"), ("networks", "front"), ("networks", "backend")):
        item = cfg[kind][key]
        require(
            item["name"] == f"{instance}_{key}" and not item.get("external"),
            "foreign_volume" if kind == "volumes" else "foreign_network",
        )
        require(item.get("labels", {}).get(LABEL) == instance, "resource_marker")
        require(
            not item.get("driver_opts")
            and item.get("driver", "local" if kind == "volumes" else "bridge")
            == ("local" if kind == "volumes" else "bridge"),
            "resource_driver",
        )
        if kind == "networks":
            require(bool(item.get("internal")) == (key == "backend"), "external_route")
    for name, service in cfg["services"].items():
        require(
            not any(
                service.get(k)
                for k in (
                    "network_mode",
                    "privileged",
                    "external_links",
                    "extra_hosts",
                    "volumes_from",
                    "devices",
                    "pid",
                    "ipc",
                    "use_api_socket",
                    "secrets",
                    "configs",
                )
            ),
            "unsafe_service",
        )
        require(
            set(service.get("networks", {}))
            == ({"front", "backend"} if name == "web" else {"backend"}),
            "external_route",
        )
        for mount in service.get("volumes", []):
            actual = (mount["type"], mount.get("source"), mount["target"])
            require(
                (
                    name == "db"
                    and (
                        actual == ("volume", "pgdata", PGDATA)
                        or (
                            actual
                            == ("bind", str(INIT), "/docker-entrypoint-initdb.d/10-app-role.sql")
                            and mount.get("read_only") is True
                        )
                    )
                )
                or (name == "redis" and actual == ("tmpfs", None, "/data"))
                or (
                    name == "beat"
                    and actual
                    == (
                        "bind",
                        str(ROOT / "tmp" / "s07-evidence" / instance / "beat.lock"),
                        "/run/aq-beat.lock",
                    )
                    and mount.get("read_only") is True
                    and not mount.get("bind", {}).get("create_host_path", True)
                ),
                "foreign_mount",
            )
        if name in PINNED:
            require(
                service["image"] == PINNED[name]
                and service.get("labels", {}).get(LABEL) == instance,
                "infrastructure_image_or_label",
            )
        if name == "web":
            require(
                service.get("command") == ["python", "-I", "/app/backend/config/offline_wsgi.py"]
                and set(service.get("cap_add", ())) == {"NET_ADMIN", "SETUID", "SETGID"}
                and service.get("cap_drop") == ["ALL"]
                and service.get("dns") == ["127.0.0.1"]
                and service.get("security_opt") == ["no-new-privileges:true"]
                and service.get("environment", {}).get("AQ_HOST_NETNS")
                == str(os.stat("/proc/self/ns/net").st_ino),
                "web_bootstrap",
            )
        else:
            require(not service.get("cap_add"), "unsafe_service")
        ports = service.get("ports", [])
        require(
            (
                name == "web"
                and len(ports) == 1
                and ports[0].get("host_ip") == "127.0.0.1"
                and ports[0]["target"] == 8000
            )
            or (name != "web" and not ports),
            "published_port",
        )
        env = service.get("environment", {})
        if name == "s03-origin":
            require(
                flows
                and service.get("command") == ["python", "scripts/s03_http.py"]
                and service["image"] == instance + "-app:local"
                and service.get("read_only")
                and not env
                and not service.get("volumes"),
                "unexpected_fixture_origin",
            )
        elif name != "redis":
            require(
                env.get("POSTGRES_DB") == db
                and env.get("POSTGRES_USER") == ("postgres" if name == "db" else role),
                "foreign_database",
            )
            require(
                env.get("CNEMC_MODE", "off") == "off"
                and env.get("CNEMC_FIXTURE_HTTP", "0") == "0"
                and env.get("S03_FIXTURE_HTTP", "0")
                == ("1" if flows and name in {"web", "worker", "worker-b"} else "0"),
                "source_mode",
            )
        require(
            not any(
                k in env
                for k in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "DOCKER_HOST",
                    "PGSERVICE",
                    "DATABASE_URL",
                )
            ),
            "override",
        )
    require(cfg["services"]["db"]["environment"].get("APP_DB_USER") == role, "foreign_database")
    require(
        cfg["services"]["db"].get("command")
        == ["postgres", "-c", f"airquality.s07_instance={instance}"],
        "database_marker",
    )
    require(INIT.resolve(strict=True) == INIT, "redirected_init")


def check_negative(cfg, instance, *, flows=False):
    rejects = {}
    cases = [
        ("project_name", ("name",), instance + "-other"),
        ("foreign_database", ("services", "worker", "environment", "POSTGRES_DB"), "s07_fake_db"),
        ("external_route", ("networks", "backend", "internal"), False),
        ("foreign_volume", ("volumes", "pgdata", "name"), f"{instance}_other"),
        ("source_mode", ("services", "worker", "environment", "CNEMC_MODE"), "limited"),
        ("override", ("services", "web", "environment", "PGSERVICE"), "s07_fake"),
        ("resource_marker", ("volumes", "pgdata", "labels", LABEL), "absent"),
    ]
    for expected, path, value in cases:
        trial = deepcopy(cfg)
        node = trial
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        try:
            check_config(trial, instance, flows=flows)
        except IsolationError as error:
            require(str(error) == expected, "wrong_denial")
            rejects[expected] = str(error)
        else:
            raise IsolationError("unsafe_config_accepted")
    return rejects


class ComposeRun:
    def __init__(self, *, offline=False, flows=False):
        self.flows = flows
        self.offline = offline
        self.name = f"s07a-{secrets.token_hex(6)}"
        self.db, self.role = identities(self.name)
        self.root = ROOT / "tmp" / "s07-evidence" / self.name
        self.private = self.root / "compose.env"
        self.values = {
            "AQ_INSTANCE_ID": self.name,
            "AQ_HOST_NETNS": str(os.stat("/proc/self/ns/net").st_ino),
            "COMPOSE_PROJECT_NAME": self.name,
            "POSTGRES_DB": self.db,
            "POSTGRES_USER": self.role,
            "POSTGRES_PASSWORD": secrets.token_hex(32),
            "POSTGRES_ADMIN_PASSWORD": secrets.token_hex(32),
            "DJANGO_SECRET_KEY": secrets.token_hex(48),
            "WEB_PORT": "0",
            "AQ_BEAT_LOCK_SOURCE": str(self.root / "beat.lock"),
        }
        self.cmd = ["docker", "--host", SOCKET]
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
        self.files = (
            ROOT / "compose.yaml",
            ROOT / "compose.offline.yaml",
            ROOT / "scripts" / "s07_isolation.yaml",
        )
        if flows:
            self.files += (ROOT / "scripts" / "s03_compose.yaml",)
        if offline:
            self.files += (ROOT / "scripts" / "s07_offline_dns.yaml",)
        self.containers = {}
        self.resources = {}
        self.receipt = {
            "run": self.name,
            "evidence_type": ["F", "I"],
            "scope": "V01-A",
            "status": "FAIL",
        }

    def docker(self, *args, extra_env=None, timeout=90, check=True, stdin=None):
        result = subprocess.run(
            self.cmd + list(args),
            cwd=ROOT,
            env=self.env | (extra_env or {}),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode:
            # Do not include environment, connection strings, stdout or stderr in receipts.
            raise IsolationError(f"docker_{args[0]}_{result.returncode}")
        return result.stdout.strip()

    def compose(self, *args, **kwargs):
        return self.docker(
            "compose",
            "--env-file",
            str(self.private),
            "-p",
            self.name,
            *(x for f in self.files for x in ("-f", str(f))),
            *args,
            **kwargs,
        )

    def inventory(self):
        return {
            k: self.docker(*command, "-q", "--filter", f"label={PROJECT}={self.name}").splitlines()
            for k, command in {
                "containers": ("ps", "-a"),
                "networks": ("network", "ls"),
                "volumes": ("volume", "ls"),
            }.items()
        }

    def empty(self):
        before = self.inventory()
        require(not any(before.values()), "project_already_exists")
        for kind, keys in (("volume", ("pgdata",)), ("network", ("backend", "front"))):
            names = self.docker(kind, "ls", "--format", "{{.Name}}").splitlines()
            require(all(f"{self.name}_{k}" not in names for k in keys), "resource_already_exists")
        self.receipt["before"] = before
        self.receipt["before_absent"] = True

    def owned_resource(self, kind, key):
        name = f"{self.name}_{key}"
        (info,) = json.loads(self.docker(kind, "inspect", name))
        require(
            info["Name"] == name
            and info["Labels"].get(PROJECT) == self.name
            and info["Labels"].get(LABEL) == self.name,
            "resource_owner",
        )
        if kind == "network":
            require(
                info["Driver"] == "bridge" and info["Internal"] == (key == "backend"),
                "external_route",
            )
        else:
            require(info["Driver"] == "local" and not info.get("Options"), "foreign_volume")
        return info.get("Id", name)

    def owned_container(self, name, cid):
        require(re.fullmatch(r"[0-9a-f]{64}", cid), "container_id")
        (info,) = json.loads(self.docker("container", "inspect", cid))
        labels = info["Config"]["Labels"]
        require(
            info["Id"] == cid
            and labels.get(PROJECT) == self.name
            and labels.get("com.docker.compose.service") == name
            and labels.get(LABEL) == self.name,
            "container_owner",
        )
        require(info["Image"] == self.receipt["images"][name], "image_identity")
        require(
            set(info["NetworkSettings"]["Networks"]) == {f"{self.name}_backend"}, "external_route"
        )
        require(
            not info["HostConfig"]["Privileged"] and not info["HostConfig"]["PortBindings"],
            "unsafe_service",
        )
        mounts = {
            (m["Type"], m["Destination"], m.get("Name", m.get("Source", "")), m["RW"])
            for m in info["Mounts"]
        }
        expected = (
            {
                ("volume", PGDATA, f"{self.name}_pgdata", True),
                ("bind", "/docker-entrypoint-initdb.d/10-app-role.sql", str(INIT), False),
            }
            if name == "db"
            else {("tmpfs", "/data", "", True)}
        )
        require(mounts == expected, "foreign_mount")
        return info

    def app_sql(self, sql):
        return self.docker(
            "exec",
            "--env",
            "PGPASSWORD",
            "--env",
            "PGOPTIONS",
            self.containers["db"],
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-U",
            self.role,
            "-d",
            self.db,
            "-c",
            sql,
            extra_env={
                "PGPASSWORD": self.values["POSTGRES_PASSWORD"],
                "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=2500",
            },
        )

    def inspect_started(self, expected_marker):
        db = self.app_sql(
            "SELECT current_database(), current_user, current_setting('airquality.s07_instance',true), current_setting('transaction_read_only'), rolsuper, (SELECT count(*) FROM information_schema.tables WHERE table_schema='public'), current_setting('server_version') FROM pg_roles WHERE rolname=current_user"
        ).split("|")
        require(
            db[:6] == [self.db, self.role, expected_marker, "on", "f", "0"], "database_identity"
        )
        r = self.containers["redis"]
        info = self.docker("exec", r, "redis-cli", "--raw", "INFO", "server")
        run_id = next(
            (line.split(":", 1)[1] for line in info.splitlines() if line.startswith("run_id:")), ""
        )
        require(
            re.fullmatch(r"[0-9a-f]{40}", run_id)
            and self.docker("exec", r, "redis-cli", "--raw", "DBSIZE") == "0",
            "broker_identity",
        )
        routes = {}
        for name, cid in self.containers.items():
            self.owned_container(name, cid)
            rows = self.docker("exec", cid, "cat", "/proc/net/route").splitlines()[1:]
            routes[name] = sum(row.split()[1] == "00000000" for row in rows)
        require(not any(routes.values()), "external_route")
        self.receipt.update(
            database={
                "name": db[0],
                "role": db[1],
                "marker": db[2],
                "read_only": True,
                "superuser": False,
                "public_tables": 0,
                "version": db[6],
            },
            broker={"id": run_id, "keys": 0},
            default_routes=routes,
        )

    def setup(self):
        self.empty()
        cfg = json.loads(self.compose("config", "--format", "json"))
        check_config(cfg, self.name, flows=self.flows)
        self.receipt["pre_write_denials"] = check_negative(cfg, self.name, flows=self.flows)
        if self.offline:
            require(
                all(svc.get("dns") == ["127.0.0.1"] for svc in cfg["services"].values()),
                "v14_compose_dns",
            )
        self.receipt["config_files"] = {
            str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in (*self.files, INIT)
        }
        self.receipt["migration_files_sha256"] = hashlib.sha256(
            b"".join(
                str(p.relative_to(ROOT)).encode() + b"\0" + p.read_bytes() + b"\0"
                for p in sorted((ROOT / "backend").glob("*/migrations/*.py"))
            )
        ).hexdigest()
        self.receipt["docker_version"] = self.docker("version", "--format", "{{.Server.Version}}")
        self.receipt["compose_version"] = self.compose("version", "--short")
        self.receipt["images"] = {
            name: self.docker("image", "inspect", "--format", "{{.Id}}", version)
            for name, version in PINNED.items()
        }
        self.compose("create", "--pull", "never", "--no-build", "db", "redis")
        for service in ("db", "redis"):
            cid = self.compose("ps", "-a", "-q", service)
            self.containers[service] = cid
        for kind, key in (("volume", "pgdata"), ("network", "backend")):
            self.resources[kind, key] = self.owned_resource(kind, key)
        if self.docker(
            "network", "ls", "--format", "{{.Name}}", "--filter", f"name=^{self.name}_front$"
        ):
            self.resources["network", "front"] = self.owned_resource("network", "front")
        for name, cid in self.containers.items():
            self.owned_container(name, cid)
        self.docker("start", *self.containers.values())
        end = monotonic() + 85
        while not all(
            self.owned_container(name, cid)["State"].get("Health", {}).get("Status") == "healthy"
            for name, cid in self.containers.items()
        ):
            require(monotonic() < end, "readiness_timeout")
            sleep(0.5)
        try:
            self.inspect_started(self.name + "-wrong")
        except IsolationError as error:
            require(str(error) == "database_identity", "wrong_live_denial")
            self.receipt["pre_write_denials"]["live_marker"] = str(error)
        else:
            raise IsolationError("foreign_marker_accepted")
        self.inspect_started(self.name)

    def cleanup(self):
        # Work with our freshly created container IDs. Recheck identities before removal.
        for name, cid in self.containers.items():
            self.owned_container(name, cid)
            self.docker("stop", "--time", "12", cid)
            self.owned_container(name, cid)
            self.docker("rm", cid)
        for (kind, key), recorded in self.resources.items():
            require(self.owned_resource(kind, key) == recorded, "replaced_resource")
            self.docker(kind, "rm", recorded)
        self.receipt["after"] = self.inventory()
        require(not any(self.receipt["after"].values()), "cleanup_incomplete")

    @contextmanager
    def guarded(self):
        for parent in (self.root.parent.parent, self.root.parent):
            parent.mkdir(exist_ok=True, mode=0o700)
            require(
                parent.resolve(strict=True) == parent
                and parent.stat().st_uid == os.getuid()
                and not parent.stat().st_mode & 0o022,
                "unsafe_evidence_root",
            )
        self.root.mkdir(mode=0o700)
        lock_file = self.root / "beat.lock"
        lock_file.touch(exist_ok=False)
        lock_file.chmod(0o444)
        with os.fdopen(
            os.open(
                self.private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR
            ),
            "w",
        ) as f:
            f.writelines(f"{k}={v}\n" for k, v in self.values.items())
        self.receipt["source_sha256"] = source_digest()
        self.receipt["lock_sha256"] = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        try:
            self.setup()
            yield self
            self.receipt["status"] = "PASS"
        except Exception as error:
            self.receipt["error"] = (
                str(error) if isinstance(error, IsolationError) else type(error).__name__
            )
            raise
        finally:
            try:
                self.cleanup()
            except Exception as error:
                self.receipt["status"] = "FAIL"
                self.receipt["cleanup_error"] = (
                    str(error) if isinstance(error, IsolationError) else type(error).__name__
                )
                raise
            finally:
                self.private.unlink(missing_ok=True)
                evidence = self.root / "environment.json"
                evidence.write_text(json.dumps(self.receipt, indent=2, sort_keys=True) + "\n")
                print(evidence.relative_to(ROOT), self.receipt["status"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["guard", "startup", "performance", "offline", "flows"])
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("smoke", "check", "diagnose", "heat", "reverse", "cold", "stable"),
    )
    args = parser.parse_args()
    if (args.action == "performance") != bool(args.mode):
        parser.error("Specify a measurement mode for performance only")
    with ComposeRun(
        offline=args.action == "offline", flows=args.action == "flows"
    ).guarded() as project:
        if args.action == "flows":
            from s07_flows import verify_flows

            return verify_flows(project)
        if args.action == "startup":
            from s07_compose_runtime import startup

            startup(project)
        elif args.action == "offline":
            from s07_offline_compose import verify

            verify(project)
        elif args.action == "performance":
            from s07_t09 import measure

            measure(project, args.mode)


if __name__ == "__main__":
    main()
