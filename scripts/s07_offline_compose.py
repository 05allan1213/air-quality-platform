"""Formal Compose V14. Reuse the owned project and volume across real service stops."""

import json
import re
import secrets

from s01_runtime import wait_for
from s06_guard import require
from s07_compose_runtime import applications
from s07_offline import OfflineChecks, report_at

APPS = ("web", "worker", "beat")
BROKER_MARKER = "airquality:s07:instance"
WORKER_BINDINGS = {"_kombu.binding_deadlines"} | {
    f"_kombu.{kind}.{exchange}"
    for kind in ("binding", "binding_permanent")
    for exchange in ("s01.fixture", "s02.history", "s03.collection", "celery.pidbox", "celeryev")
}

# UDP connect checks routing without sending traffic. V14 explicitly disables DNS upstreams.
ROUTE_CHECK = """
import errno, json, os, socket
checks = {}
for family, address in ((socket.AF_INET, ('192.0.2.1', 9)),
                        (socket.AF_INET6, ('2001:db8::1', 9))):
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(address)
        except OSError as error:
            assert error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL}
            checks[family.name] = False
        else:
            raise RuntimeError('unexpected_external_route')
assert not any(key.upper().endswith('_PROXY') for key in os.environ)
checks['proxy_variables'] = []
print(json.dumps(checks))
"""


class ComposeOffline:
    def __init__(self, project, apps, password):
        self.p = project
        self.apps = apps
        self.password = password
        self.base = None
        self.redis_stops = []
        self.redis_starts = []

    def id(self, name):
        if name in APPS:
            cid = self.apps.ids[name]
            self.apps.check(name, cid)
        else:
            cid = self.p.containers["db" if name == "pg" else name]
            self.p.owned_container("db" if name == "pg" else name, cid)
        return cid

    def inspect(self, name):
        cid = self.id(name)
        result = self.p.docker("container", "inspect", cid)
        (info,) = json.loads(result)
        return info

    def fixture(self, action):
        require(action in {"seed", "expire"}, "v14_fixture_action")
        output = self.p.docker(
            "exec",
            "--user",
            "10001:10001",
            "--env",
            "AQ_INSTANCE_ID",
            "--env",
            "PYTHONPATH",
            "--env",
            "S07_OFFLINE_PASSWORD",
            self.id("web"),
            "python",
            "-B",
            "scripts/s07_offline_seed.py",
            action,
            extra_env={
                "AQ_INSTANCE_ID": self.p.name,
                "S07_OFFLINE_PASSWORD": self.password,
                "PYTHONPATH": "/app/backend:/app/tests",
            },
        )
        return json.loads(output)

    def seed(self):
        self.apps.start("migrate")
        self.start("web")
        result = self.fixture("seed")
        self.stop("web")
        return result

    def expire(self):
        self.start("web")
        result = self.fixture("expire")
        self.stop("web")
        return result

    def sql(self, text):
        return json.loads(self.p.app_sql(text))

    def start(self, name):
        reconnecting = (
            name == "redis"
            and "worker" in self.apps.ids
            and self.inspect("worker")["State"]["Running"]
        )
        if name in APPS and name not in self.apps.ids:
            self.apps.start(name)
        else:
            cid = self.id(name)
            require(not self.inspect(name)["State"]["Running"], "v14_compose_already_running")
            self.p.docker("start", cid)
        if name == "web":
            address = self.apps.ready()
            self.base = address
        elif name in {"pg", "redis"}:

            def healthy():
                state = self.inspect(name)["State"]
                require(state["Running"], "v14_compose_infra_exited")
                return state["Health"]["Status"] == "healthy"

            wait_for("V14 Compose infrastructure", healthy, seconds=35)
            if name == "pg":
                self.volume()
                row = self.p.app_sql(
                    "SELECT current_database(), current_user, current_setting('airquality.s07_instance')"
                )
                require(
                    row.split("|") == [self.p.db, self.p.role, self.p.name], "v14_database_identity"
                )
            else:
                new_id = self.redis_run_id()
                require(new_id not in self.redis_stops, "v14_restart_broker_generation")
                keys = set(self.redis("KEYS", "*").splitlines())
                binding_keys = sorted(keys & WORKER_BINDINGS)
                # A live Worker may restore its transport bindings before Redis is
                # healthy. The cold restart with all apps down must still be empty.
                unexpected = keys - WORKER_BINDINGS if reconnecting else keys
                self.redis_starts.append(
                    {
                        "run_id": new_id,
                        "worker_was_running": reconnecting,
                        "transport_keys": binding_keys,
                        "unexpected_keys": len(unexpected),
                    }
                )
                require(not unexpected, "v14_restart_broker_unexpected_keys")
                require(
                    self.redis("SET", BROKER_MARKER, self.p.name, "NX") == "OK",
                    "v14_restart_broker_identity",
                )

    def stop(self, name):
        cid = self.id(name)
        if self.inspect(name)["State"]["Running"]:
            if name == "redis":
                self.redis_stops.append(self.broker_id())
            self.p.docker("stop", "--time", "10", cid)
        require(not self.inspect(name)["State"]["Running"], "v14_compose_stop_failed")

    def all_start(self):
        for name in APPS:
            self.start(name)

    def all_stop(self):
        for name in reversed(APPS):
            self.stop(name)

    def reject_applications(self):
        require(not self.inspect("pg")["State"]["Running"], "v14_reverse_requires_pg_down")
        self.broker_id()
        for name in ("worker", "beat"):
            self.start(name)
        rejected = {}
        for name in ("worker", "beat"):
            exit_code = int(self.p.docker("wait", self.id(name), timeout=18))
            require(exit_code != 0, "v14_compose_early_application_started")
            state = self.inspect(name)["State"]
            rejected[name] = {
                "exit": exit_code,
                "started_at": state["StartedAt"],
                "finished_at": state["FinishedAt"],
                "pg_unavailable": True,
            }
        return rejected

    def recover_applications(self):
        for name in ("worker", "beat"):
            if not self.inspect(name)["State"]["Running"]:
                self.start(name)

    def beat_lost(self):
        require(not self.inspect("pg")["State"]["Running"], "v14_beat_loss_requires_pg_down")
        exit_code = int(self.p.docker("wait", self.id("beat"), timeout=18))
        require(exit_code != 0, "v14_beat_loss_exit_code")
        state = self.inspect("beat")["State"]
        return {"exit": exit_code, "finished_at": state["FinishedAt"], "pg_unavailable": True}

    def volume(self):
        name = self.p.owned_resource("volume", "pgdata")
        require(name == self.p.resources["volume", "pgdata"], "v14_compose_unowned_volume")
        (info,) = json.loads(self.p.docker("volume", "inspect", name))
        return {"name": name, "created_at": info["CreatedAt"]}

    def redis(self, *command):
        return self.p.docker("exec", self.id("redis"), "redis-cli", "--raw", *command)

    def broker_id(self):
        require(self.redis("GET", BROKER_MARKER) == self.p.name, "v14_compose_broker_marker")
        return self.redis_run_id()

    def redis_run_id(self):
        info = self.redis("INFO", "server")
        match = re.search(r"^run_id:([a-f0-9]{40})\s*$", info, re.M)
        require(match, "v14_compose_broker_id")
        return match[1]

    def queued_id(self, run):
        return any(
            json.loads(line)["headers"]["id"] == run
            for line in self.redis("LRANGE", "s01.fixture", "0", "-1").splitlines()
        )

    def stopped(self):
        states = {name: self.inspect(name)["State"]["Running"] for name in (*APPS, "redis", "pg")}
        require(not any(states.values()), "v14_compose_still_running")
        return states

    def network(self):
        evidence = {}
        for name in APPS:
            config = self.inspect(name)["HostConfig"]
            require(config["Dns"] == ["127.0.0.1"], "v14_compose_dns")
            evidence[name] = json.loads(
                self.p.docker(
                    "exec",
                    "--user",
                    "10001:10001",
                    self.id(name),
                    "python",
                    "-c",
                    ROUTE_CHECK,
                )
            ) | {"dns_upstream": config["Dns"]}
        return evidence


def verify(project):
    password = secrets.token_hex(24)
    with report_at(
        project.root / "V14" / "compose.json", project.name, "formal WSGI Compose"
    ) as report:
        with applications(project) as apps:
            report["image"] = apps.image
            offline = ComposeOffline(project, apps, password)
            report["redis_starts"] = offline.redis_starts
            OfflineChecks(offline, report, password).run()
