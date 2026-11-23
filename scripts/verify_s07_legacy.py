#!/usr/bin/env python3
"""Verify the documented base Compose F demo and the legacy runner's registered Beat."""

import argparse
import json
import os
import re
import sys
import traceback
from copy import deepcopy
from datetime import UTC, datetime

from s01_runtime import (
    AUTH,
    BROKER_MARKER,
    FIXTURE_POST,
    OBS,
    ROOT,
    RUNS,
    Client,
    Project,
    wait_for,
)
from s06_guard import IsolationError, require
from s07_handoff import checkout, sha256
from s07_legacy_web import BASE_WEB
from s07_offline_compose import ROUTE_CHECK
from verify_s07 import LABEL, PGDATA, PINNED, PROJECT, SERVICES, SOCKET
from verify_s07_host import freeze_source

ENTRY = ["python", "-I", "/app/scripts/s07_legacy_web.py"]
BASE_BEAT = ["celery", "-A", "config", "beat", "-l", "INFO"]
SOURCE_FLAGS = {"CNEMC_MODE": "off", "CNEMC_FIXTURE_HTTP": "0", "S03_FIXTURE_HTTP": "0"}


class LegacyProject(Project):
    def __init__(self, report):
        super().__init__()
        self.report = report
        self.env = {
            k: self.env[k]
            for k in (
                "PATH",
                "LANG",
                "HOME",
                "POSTGRES_DB",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
                "POSTGRES_ADMIN_PASSWORD",
                "DJANGO_SECRET_KEY",
                "WEB_PORT",
                "COOKIE_SECURE",
                "DJANGO_ALLOWED_HOSTS",
            )
            if k in self.env
        }
        config = self.evidence / "docker-config"
        config.mkdir(mode=0o700)
        self.env.update(DOCKER_HOST=SOCKET, DOCKER_CONFIG=str(config))
        self.env["AQ_HOST_NETNS"] = str(os.stat("/proc/self/ns/net").st_ino)

    def prepare(self, snapshot):
        base = snapshot / "compose.yaml"
        self.prefix[self.prefix.index("-f") + 1] = str(base)
        original = json.loads(self.compose("config", "--format", "json"))
        depend = original["services"]
        require(
            set(depend) == SERVICES
            and depend["web"]["command"] == BASE_WEB
            and depend["beat"]["command"] == BASE_BEAT
            and "beat"
            not in set(depend["web"]["depends_on"]) | set(depend["worker"]["depends_on"]),
            "legacy_base_service_contract",
        )
        require(
            depend["beat"]["environment"]["AQ_INSTANCE_ID"] == "unregistered"
            and depend["beat"]["volumes"][0]["bind"]["create_host_path"] is False,
            "legacy_unregistered_beat_default",
        )
        self.claim()
        self.report["before_absent"] = True
        self.report["default_beat_requires_registration"] = True
        overlay = self.evidence / "guard.json"
        marker = {LABEL: self.name}
        overrides = {
            service: {"labels": marker, "dns": ["127.0.0.1"]}
            | ({"environment": SOURCE_FLAGS} if service not in PINNED else {})
            for service in SERVICES
        }
        overrides["web"].update(
            entrypoint=ENTRY,
            user="0:0",
            cap_drop=["ALL"],
            cap_add=["NET_ADMIN", "SETUID", "SETGID"],
            security_opt=["no-new-privileges:true"],
            environment=SOURCE_FLAGS | {"AQ_HOST_NETNS": self.env["AQ_HOST_NETNS"]},
        )
        overlay.write_text(
            json.dumps(
                {
                    "services": overrides,
                    "volumes": {"pgdata": {"labels": marker}},
                    "networks": {"backend": {"labels": marker}, "front": {"labels": marker}},
                }
            )
        )
        self.prefix[-2:-2] = ["-f", str(overlay)]
        config = json.loads(self.compose("config", "--format", "json"))
        self.validate_config(config, snapshot)
        rejections = []
        for expected, path, value in (
            ("legacy_project", ["name"], "other"),
            ("legacy_resource", ["volumes", "pgdata", "name"], "foreign"),
            ("legacy_network", ["networks", "backend", "internal"], False),
            ("legacy_source_mode", ["services", "worker", "environment", "CNEMC_MODE"], "limited"),
            ("legacy_database", ["services", "worker", "environment", "POSTGRES_DB"], "foreign"),
            ("legacy_web_binding", ["services", "web", "ports", 0, "host_ip"], "0.0.0.0"),
        ):
            trial = deepcopy(config)
            row = trial
            for key in path[:-1]:
                row = row[key]
            row[path[-1]] = value
            try:
                self.validate_config(trial, snapshot)
            except IsolationError as error:
                require(str(error) == expected, "legacy_unexpected_rejection")
                rejections.append(expected)
            else:
                raise IsolationError("legacy_unsafe_configuration_allowed")
        self.report.update(
            config_pass=True,
            rejected_before_create=rejections,
            base_compose_sha256=sha256(base.read_bytes()),
            safety_overlay_sha256=sha256(overlay.read_bytes()),
            web_command=config["services"]["web"]["command"],
            beat_command=config["services"]["beat"]["command"],
        )

    def validate_config(self, config, snapshot):
        require(config["name"] == self.name, "legacy_project")
        require(set(config["services"]) == SERVICES, "legacy_services")
        require(not config.get("secrets") and not config.get("configs"), "legacy_external_inputs")
        for section, kind, names in (
            ("volumes", "local", {"pgdata"}),
            ("networks", "bridge", {"backend", "front"}),
        ):
            require(set(config[section]) == names, "legacy_resource")
            for name in names:
                row = config[section][name]
                require(
                    row["name"] == f"{self.name}_{name}"
                    and row["labels"][LABEL] == self.name
                    and not row.get("external")
                    and not row.get("driver_opts")
                    and row.get("driver", kind) == kind,
                    "legacy_resource",
                )
                if section == "networks":
                    require(bool(row.get("internal")) == (name == "backend"), "legacy_network")
        for name, spec in config["services"].items():
            require(
                spec["labels"][LABEL] == self.name and spec["dns"] == ["127.0.0.1"],
                "legacy_service_marker",
            )
            require(
                set(spec["networks"]) == ({"front", "backend"} if name == "web" else {"backend"}),
                "legacy_network",
            )
            require(
                not any(
                    spec.get(field)
                    for field in (
                        "privileged",
                        "network_mode",
                        "devices",
                        "secrets",
                        "configs",
                        "extra_hosts",
                        "external_links",
                        "volumes_from",
                        "use_api_socket",
                        "pid",
                        "ipc",
                        "container_name",
                    )
                ),
                "legacy_service_access",
            )
            require(spec["image"] == PINNED.get(name, self.name + "-app:local"), "legacy_image")
            env = spec.get("environment", {})
            require(
                not any(
                    key.upper().endswith("_PROXY")
                    or key
                    in {
                        "DOCKER_HOST",
                        "PGSERVICE",
                        "DATABASE_URL",
                        "CELERY_BROKER_URL",
                        "POSTGRES_HOST",
                    }
                    for key in env
                ),
                "legacy_environment_override",
            )
            if name != "redis":
                require(
                    env["POSTGRES_DB"] == "s01"
                    and env["POSTGRES_USER"] == ("postgres" if name == "db" else "s01_app"),
                    "legacy_database",
                )
            if name not in PINNED:
                require(
                    all(env[key] == value for key, value in SOURCE_FLAGS.items()),
                    "legacy_source_mode",
                )
            expected = {
                "db": {
                    ("volume", "pgdata", PGDATA, False),
                    (
                        "bind",
                        str(snapshot / "docker/10-app-role.sql"),
                        "/docker-entrypoint-initdb.d/10-app-role.sql",
                        True,
                    ),
                },
                "redis": {("tmpfs", None, "/data", False)},
                "beat": {("bind", str(self.beat_lock), "/run/aq-beat.lock", True)},
            }.get(name, set())
            volumes = spec.get("volumes", [])
            require(
                {
                    (v["type"], v.get("source"), v["target"], v.get("read_only", False))
                    for v in volumes
                }
                == expected,
                "legacy_mount",
            )
            if name == "beat":
                require(
                    volumes[0]["bind"]["create_host_path"] is False
                    and env["AQ_INSTANCE_ID"] == self.name,
                    "legacy_binding",
                )
            if name == "migrate":
                require(
                    spec["build"]["context"] == str(snapshot)
                    and spec["build"].get("dockerfile", "Dockerfile") == "Dockerfile",
                    "legacy_build_context",
                )
            else:
                require(not spec.get("build"), "legacy_build_context")
            expected_ports = spec.get("ports", [])
            if name == "web":
                require(
                    len(expected_ports) == 1
                    and expected_ports[0]["host_ip"] == "127.0.0.1"
                    and expected_ports[0]["target"] == 8000,
                    "legacy_web_binding",
                )
                require(
                    spec["command"] == BASE_WEB
                    and spec["entrypoint"] == ENTRY
                    and spec["user"] == "0:0"
                    and spec["cap_drop"] == ["ALL"]
                    and set(spec["cap_add"]) == {"NET_ADMIN", "SETUID", "SETGID"}
                    and spec["security_opt"] == ["no-new-privileges:true"],
                    "legacy_web_entry",
                )
            else:
                require(not expected_ports and not spec.get("cap_add"), "legacy_external_ports")

    def inspect(self, kind, identity):
        (info,) = json.loads(self.cmd(["docker", kind, "inspect", identity]))
        return info

    def resource(self, kind, name):
        info = self.inspect(kind, f"{self.name}_{name}")
        require(
            info["Labels"].get(PROJECT) == info["Labels"].get(LABEL) == self.name,
            "legacy_live_resource_owner",
        )
        if kind == "network":
            require(
                info["Driver"] == "bridge" and info["Internal"] == (name == "backend"),
                "legacy_live_network",
            )
        else:
            require(info["Driver"] == "local" and not info["Options"], "legacy_live_volume")
        return info

    def container(self, service):
        cid = self.compose("ps", "-a", "-q", service)
        require(re.fullmatch(r"[0-9a-f]{64}", cid), "legacy_container_id")
        info = self.inspect("container", cid)
        labels = info["Config"]["Labels"]
        require(
            info["Id"] == cid
            and labels.get(PROJECT) == labels.get(LABEL) == self.name
            and labels.get("com.docker.compose.service") == service
            and not info["HostConfig"]["Privileged"]
            and info["HostConfig"]["Dns"] == ["127.0.0.1"],
            "legacy_live_container_owner",
        )
        expected = {"front", "backend"} if service == "web" else {"backend"}
        require(
            set(info["NetworkSettings"]["Networks"])
            == {f"{self.name}_{name}" for name in expected},
            "legacy_live_container_network",
        )
        for network in expected:
            self.resource("network", network)
        if service == "db":
            self.resource("volume", "pgdata")
        if service in PINNED:
            require(
                info["Config"]["Image"] == PINNED[service]
                and info["Image"] == self.report["infra_images"][service],
                "legacy_live_image",
            )
        return info

    def fresh_infra(self):
        self.report["infra_images"] = {
            service: self.cmd(["docker", "image", "inspect", "--format", "{{.Id}}", ref])
            for service, ref in PINNED.items()
        }
        self.compose("create", "--pull", "never", "--no-build", "db", "redis")
        ids = [self.container(service)["Id"] for service in PINNED]
        self.cmd(["docker", "start", *ids])
        for service in PINNED:
            wait_for(
                service + " healthy",
                lambda name=service: self.container(name)["State"]["Health"]["Status"] == "healthy",
                85,
            )
        self.infrastructure_id("db")
        self.infrastructure_id("redis")
        pg = self.sql(
            "SELECT current_database(), (SELECT rolname FROM pg_roles WHERE oid=datdba), (SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')), current_setting('airquality.s07_instance',true) FROM pg_database WHERE datname=current_database()"
        )
        redis = self.compose("exec", "-T", "redis", "redis-cli", "--raw", "DBSIZE")
        require(pg == "s01|s01_app|0|" and redis == "0", "legacy_infra_not_empty")
        self.report["initial_business_tables"] = 0
        self.report["initial_redis_keys"] = 0

    def inventory(self):
        return {
            kind: self.cmd(
                ["docker", *args, "-q", "--filter", f"label={PROJECT}={self.name}"]
            ).splitlines()
            for kind, args in {
                "containers": ["ps", "-a"],
                "networks": ["network", "ls"],
                "volumes": ["volume", "ls"],
            }.items()
        }

    def live(self, registered):
        expected = SERVICES if registered else SERVICES - {"beat"}
        require(
            set(self.compose("ps", "-a", "--services").splitlines()) == expected,
            "legacy_unexpected_services",
        )
        info = {service: self.container(service) for service in sorted(expected)}
        require(
            all(e["State"]["Running"] == (k != "migrate") for k, e in info.items())
            and info["migrate"]["State"]["ExitCode"] == 0,
            "legacy_not_running",
        )
        probes = {}
        for name in expected & {"web", "worker", "beat"}:
            probes[name] = json.loads(
                self.cmd(
                    [
                        "docker",
                        "exec",
                        "--user",
                        "10001:10001",
                        info[name]["Id"],
                        "python",
                        "-c",
                        ROUTE_CHECK,
                    ]
                )
            )
        argv = (
            self.cmd(
                [
                    "docker",
                    "exec",
                    "--user",
                    "10001:10001",
                    info["web"]["Id"],
                    "cat",
                    "/proc/1/cmdline",
                ]
            )
            .strip("\0")
            .split("\0")
        )
        require(argv == BASE_WEB, "legacy_live_web_command")
        raw_status = self.cmd(
            [
                "docker",
                "exec",
                "--user",
                "10001:10001",
                info["web"]["Id"],
                "cat",
                "/proc/1/status",
            ]
        )
        status = dict(line.split(":", 1) for line in raw_status.splitlines() if ":" in line)
        require(
            set(status["Uid"].split()) == {"10001"}
            and int(status["NoNewPrivs"]) == 1
            and all(int(status[key], 16) == 0 for key in ("CapEff", "CapPrm", "CapAmb")),
            "legacy_live_web_identity",
        )
        return {
            "services": sorted(expected - {"migrate"}),
            "migrate_exit": 0,
            "network_probes": probes,
            "web_uid": 10001,
        }

    def origin(self):
        address = self.compose("port", "web", "8000")
        require(re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", address), "legacy_loopback_origin")
        self.base = "http://" + address
        self.wait_ready()

    def beat_publisher(self, exclude=None):
        def probe():
            result = self.sql("""SELECT substring(application_name from 'aq-beat-(.*)')
                FROM pg_stat_activity JOIN pg_locks USING(pid)
                JOIN accounts_servicepulse p ON p.component='beat' AND application_name='aq-beat-'||p.instance
                WHERE application_name ~ '^aq-beat-[a-f0-9]{32}$' AND locktype='advisory' AND granted
                AND p.observed_at > clock_timestamp() - interval '30 seconds'""")
            return result if result != exclude and re.fullmatch(r"[a-f0-9]{32}", result) else False

        return wait_for("actual Beat PostgreSQL lock", probe)

    def broker_state(self):
        cid = self.infrastructure_id("redis")
        info = self.cmd(["docker", "exec", cid, "redis-cli", "--raw", "INFO", "server"])
        identity = re.search(r"^run_id:([0-9a-f]{40})$", info, re.MULTILINE)
        require(identity, "legacy_missing_broker_identity")
        marker = self.cmd(["docker", "exec", cid, "redis-cli", "--raw", "GET", BROKER_MARKER])
        return identity[1], marker


def fixture_submission(p, admin):
    status, data = admin.call(RUNS, FIXTURE_POST)
    require(status == 202, "legacy_fixture_dispatch")
    key = data["run_id"]
    row = p.done(key, "succeeded")
    p.receive(key, "succeeded")
    require(
        row["dispatch"] == "sent" and row["observation_id"] is not None, "legacy_fixture_receipt"
    )
    return {
        "http": status,
        "run": key,
        "dispatch": row["dispatch"],
        "state": row["state"],
        "observation": row["observation_id"],
        "worker_confirmed": True,
    }


def read_fixture(reader):
    status, data = reader.call(OBS)
    require(status == 200 and data["count"] == 1, "legacy_fixture_read")
    (row,) = data["results"]
    require(
        row["value"] == "1.250"
        and row["unit"] == "mg/m³"
        and row["synthetic"] is True
        and row["product"] == "F"
        and row["indicator"] == "CO",
        "legacy_fixture_value",
    )
    return {"id": row["id"], "value": row["value"], "unit": row["unit"]}


def scenario(p, registered):
    p.report["phase"] = "isolated_infrastructure"
    p.fresh_infra()
    p.report["phase"] = "first_boot"
    if registered:
        p.up(build=True)
    else:
        p.compose("up", "-d", "--build", "web", "worker", timeout=720)
        p.origin()
    volume = p.resource("volume", "pgdata")["CreatedAt"]
    stage = p.report["first_boot"] = p.live(registered)
    first_beat = p.beat_publisher() if registered else None
    first_broker, marker = p.broker_state()
    require(marker == (p.name if registered else ""), "legacy_first_broker_marker")
    stage["postgres_beat_lock_holder"] = first_beat
    stage["broker_instance_marker"] = bool(marker)
    stage["initial_observation_count"] = p.snapshot()[1]
    require(stage["initial_observation_count"] == 0, "legacy_observation_not_empty")
    p.compose(
        "exec",
        "-T",
        "--user",
        "10001:10001",
        "web",
        "python",
        "backend/manage.py",
        "shell",
        "--verbosity",
        "0",
        stdin=f"from django.contrib.auth import get_user_model\nget_user_model().objects.create_superuser('legacyadmin', password={p.password!r})\n",
    )
    admin = Client(p.base).login("legacyadmin", p.password)
    reader = Client(p.base)
    reader.csrf()
    status, _ = reader.call(
        AUTH + "register/", {"username": "legacyreader", "password": p.password}
    )
    require(status == 201, "legacy_reader_registration")
    reader.login("legacyreader", p.password)
    observed = stage["submitted"] = fixture_submission(p, admin)
    stage["observed"] = read_fixture(reader)
    require(stage["observed"]["id"] == observed["observation"], "legacy_observation_id")
    p.report["phase"] = "rebuild"
    p.compose("down", "--timeout", "5")
    if registered:
        p.up()
    else:
        p.compose("up", "-d", "web", "worker")
        p.origin()
    stage2 = p.report["rebuild"] = p.live(registered)
    stage2["same_volume"] = p.resource("volume", "pgdata")["CreatedAt"] == volume
    require(stage2["same_volume"], "legacy_volume_replaced")
    rebuild_beat = p.beat_publisher(exclude=first_beat) if registered else None
    prior_broker, marker = p.broker_state()
    require(
        prior_broker != first_broker and marker == (p.name if registered else ""),
        "legacy_rebuilt_broker",
    )
    stage2["postgres_beat_lock_holder"] = rebuild_beat
    stage2["new_broker"] = True
    reader = Client(p.base).login("legacyreader", p.password)
    admin = Client(p.base).login("legacyadmin", p.password)
    stage2["observed"] = read_fixture(reader)
    require(stage2["observed"] == stage["observed"], "legacy_rebuild_value")
    p.report["phase"] = "redis_recovery"
    stage3 = p.report["redis_recovery"] = {}
    worker = p.container("worker")["Id"]
    p.compose("stop", "redis")
    stage3["read_while_redis_down"] = read_fixture(reader)
    assert stage3["read_while_redis_down"] == stage["observed"]
    status, data = admin.call(RUNS, FIXTURE_POST)
    require(status == 503 and data["code"] == "dispatch_unconfirmed", "legacy_redis_down_dispatch")
    pending = p.run_row(data["run_id"])
    require(
        [pending["state"], pending["dispatch"]] == ["pending", "unconfirmed"],
        "legacy_redis_down_ledger",
    )
    stage3["outage"] = {
        "http": status,
        "run": pending["id"],
        "dispatch": pending["dispatch"],
        "state": pending["state"],
    }
    if registered:
        # Confirm the previous publisher is stopped before re-registering a new broker generation.
        p.compose("stop", "beat")
        before = p.redis_generation
        p.start_redis()
        require(p.redis_generation != before, "legacy_broker_generation_not_changed")
        p.compose("start", "beat")
        stage3["postgres_beat_lock_holder"] = p.beat_publisher(exclude=rebuild_beat)
        stage3["redis_new_generation"] = True
    else:
        p.compose("up", "-d", "--wait", "redis")
    new_broker, marker = p.broker_state()
    require(
        new_broker != prior_broker and marker == (p.name if registered else ""),
        "legacy_recovered_broker",
    )
    stage3["redis_new_generation"] = True
    stage3["broker_instance_marker"] = bool(marker)
    stage3["same_worker"] = p.container("worker")["Id"] == worker
    require(stage3["same_worker"], "legacy_worker_replaced")
    stage3["submitted"] = fixture_submission(p, admin)
    stage3["observed"] = read_fixture(reader)
    require(
        stage3["observed"] == stage["observed"] and p.snapshot()[1] == 1,
        "legacy_fixture_idempotence",
    )


def run(registered, preflight):
    report = {
        "finding": "S07-L3-01",
        "origin": ["F", "I"],
        "registered_beat": registered,
        "status": "FAIL",
        "start_utc": datetime.now(UTC).isoformat(),
    }
    p = LegacyProject(report)
    report["project"] = p.name
    report["checkout"] = checkout(ROOT)
    receipt = p.evidence / "s07-legacy.json"
    print(receipt.relative_to(ROOT), flush=True)
    try:
        with freeze_source(p.evidence) as (snapshot, digest):
            report["source_sha256"] = digest
            p.prepare(snapshot)
            if preflight:
                report["status"] = "PASS"
                report["scope"] = "configuration_only"
            else:
                try:
                    scenario(p, registered)
                    report["status"] = "PASS"
                    report["scope"] = "base_compose_fixture_and_recovery"
                finally:
                    try:
                        p.cleanup()
                        report["after"] = p.inventory()
                        require(not any(report["after"].values()), "legacy_resources_remaining")
                        require(
                            not p.cmd(["docker", "image", "ls", "-q", p.name + "-app:local"]),
                            "legacy_image_remaining",
                        )
                        report["cleanup"] = True
                    except Exception:
                        report["status"] = "FAIL"
                        report["cleanup"] = False
                        raise
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = str(error) if isinstance(error, IsolationError) else type(error).__name__
        report["frames"] = [
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(error.__traceback__)[-6:]
        ]
        return 1
    finally:
        report["end_utc"] = datetime.now(UTC).isoformat()
        receipt.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        print(report["status"], report.get("error", report.get("scope", "")), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("manual", "registered"))
    parser.add_argument("--preflight", action="store_true")
    options = parser.parse_args()
    return run(options.mode == "registered", options.preflight)


if __name__ == "__main__":
    sys.exit(main())
