"""Exercise the actual A03 Docker network with a private DNS/TLS origin on a different bridge."""

import json
import os
import secrets
import subprocess
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ["python", "scripts/cnemc_egress_fixture.py"]


def run(*args, env=None):
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, check=True, timeout=240
    ).stdout.strip()


def verify(tmp, cleanup):
    project = "egressf" + secrets.token_hex(6)
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    env.update(
        CNEMC_MODE="off",
        WEB_PORT="0",
        DJANGO_SECRET_KEY=secrets.token_hex(32),
        POSTGRES_PASSWORD=secrets.token_hex(24),
        POSTGRES_ADMIN_PASSWORD=secrets.token_hex(24),
    )
    image = project + "-app:local"
    cmd = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        project,
        "-f",
        "compose.yaml",
        "-f",
        "compose.cnemc.yaml",
    ]
    base_cmd = cmd.copy()
    cmd.extend(["-f", "compose.cnemc.once.yaml"])

    def base(*args):
        return run(*base_cmd, *args, env=env)

    def compose(*args):
        return run(*cmd, *args, env=env)

    def inspect(container, template):
        return run("docker", "inspect", "--format", template, container)

    def attachments(container):
        return json.loads(inspect(container, "{{json .NetworkSettings.Networks}}"))

    def logs(tag):
        return run("docker", "logs", fixture).splitlines().count(tag)

    # No published LAN ports: bind the fixture to its own Docker bridge's WSL address.
    outside_net = run("docker", "network", "create", project + "_fixture")
    cleanup.callback(run, "docker", "network", "rm", outside_net)
    outside = run(
        "docker",
        "network",
        "inspect",
        "--format",
        "{{(index .IPAM.Config 0).Gateway}}",
        outside_net,
    )
    overlay = tmp / "fixture.yaml"
    overlay.write_text(f"services:\n  cnemc-gateway:\n    dns: [{outside}]\n")
    cmd.extend(["-f", str(overlay)])
    compose("build", "migrate")
    cleanup.callback(run, "docker", "image", "rm", image)
    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-noenc",
        "-days",
        "1",
        "-subj",
        "/CN=private-egress-fixture",
        "-addext",
        "subjectAltName=DNS:air.cnemc.cn",
        "-keyout",
        tmp / "key.pem",
        "-out",
        tmp / "cert.pem",
    )
    fixture = run(
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--network",
        outside_net,
        "--cap-drop=ALL",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{tmp}:/fixture:ro",
        "-p",
        f"{outside}:53:1053/udp",
        "-p",
        f"{outside}:18007:18007",
        "-p",
        f"{outside}:18008:18008",
        image,
        *PROBE,
        "serve",
        outside,
    )
    cleanup.callback(run, "docker", "rm", "-f", fixture)
    deadline = time.monotonic() + 15
    while not logs("FIXTURE READY"):
        if time.monotonic() >= deadline:
            raise RuntimeError("private DNS/TLS origin did not start")
        time.sleep(0.2)

    cleanup.callback(compose, "down", "-v", "--timeout", "5")
    base("up", "--wait", "--wait-timeout", "90", "-d", "web", "redis")
    offline_db = base("ps", "-q", "db")
    offline_redis = base("ps", "-q", "redis")
    offline_backend = attachments(offline_db)[project + "_backend"]["NetworkID"]
    assert not base("ps", "-q", "worker", "worker-cnemc", "cnemc-gateway", "beat")
    compose("up", "--wait", "--wait-timeout", "90", "-d", "web", "worker-cnemc")
    worker = compose("ps", "-q", "worker-cnemc")
    gateway = compose("ps", "-q", "cnemc-gateway")
    backend, inside, uplink = (
        project + "_" + n for n in ("backend", "cnemc-egress", "cnemc-uplink")
    )
    assert set(attachments(worker)) == {backend, inside}
    assert set(attachments(gateway)) == {inside, uplink}
    gate_ip = attachments(gateway)[inside]["IPAddress"]
    uplink_ip = attachments(gateway)[uplink]["IPAddress"]
    assert json.loads(inspect(gateway, "{{json .HostConfig.Dns}}")) == [outside]
    assert run("docker", "exec", gateway, "cat", "/proc/sys/net/ipv4/ip_forward") == "0"
    assert not compose("ps", "-a", "-q", "beat", "worker")
    assert compose("ps", "-q", "db") == offline_db and compose("ps", "-q", "redis") == offline_redis
    assert attachments(offline_db)[backend]["NetworkID"] == offline_backend
    for srv in ("db", "redis"):
        container = compose("ps", "-q", srv)
        assert set(attachments(container)) == {backend}
        assert not run("docker", "port", container)
        routes = run("docker", "exec", container, "cat", "/proc/net/route").splitlines()[1:]
        assert routes and not any(r.split()[1] == "00000000" for r in routes)
    for net in (inside, backend):
        data = json.loads(run("docker", "network", "inspect", net))[0]
        assert data["Internal"] and not data["EnableIPv6"]
        assert data["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert not run("docker", "port", gateway)
    deadline = time.monotonic() + 40
    while True:
        try:
            pong = compose(
                "exec", "-T", "worker-cnemc", "celery", "-A", "config", "inspect", "ping"
            )
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 69 or time.monotonic() >= deadline:
                raise
        else:
            assert "pong" in pong
            break
    print(
        f"PASS {project}: single real-source Worker, internal DB/Redis, Beat/default replicas zero"
    )

    compose("run", "--rm", "-T", "--no-deps", "worker", *PROBE, "default", gate_ip, outside)
    # Positive controls on both target ports from the uplink; the gateway has no uplink listener.
    run(
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        uplink,
        "-v",
        f"{tmp}/cert.pem:/tmp/egress-cert.pem:ro",
        image,
        *PROBE,
        "uplink",
        outside,
        uplink_ip,
    )
    tcp = logs("FIXTURE TCP 18007")
    assert tcp == 1 and logs("FIXTURE TCP 18008") == 1, run("docker", "logs", fixture)
    run("docker", "cp", tmp / "cert.pem", f"{worker}:/tmp/egress-cert.pem")
    compose("exec", "-T", "worker-cnemc", *PROBE, "dedicated", gate_ip, outside)
    assert logs("FIXTURE DNS target") == 0 and logs("FIXTURE TCP 18007") == tcp
    print(
        "PASS default/direct IP/wrong port/wrong or absent SNI/plain HTTP denied; no upstream TCP"
    )
    try:
        result = compose("exec", "-T", "worker-cnemc", *PROBE, "https", gate_ip)
    except subprocess.CalledProcessError:
        print(run("docker", "logs", fixture), flush=True)
        raise
    assert logs("FIXTURE DNS A") > 0 and logs("FIXTURE TCP 18007") == tcp + 2
    assert logs("FIXTURE TCP 18008") == 1
    print(
        f"PASS {result}; gateway {uplink_ip} -> WSL fixture {outside}:18007; default CA rejected test cert"
    )
    compose("stop", "cnemc-gateway")
    # Use the actual client hostname after shutdown (never fall back to a routable endpoint).
    closed = """import socket
try:
    socket.create_connection(('air.cnemc.cn', 18007), 2).close()
except OSError:
    pass
else:
    raise AssertionError('gateway shutdown still permits connections')
"""
    compose("exec", "-T", "worker-cnemc", "python", "-c", closed)
    assert logs("FIXTURE TCP 18007") == tcp + 2
    print("PASS stopped gateway closes Worker path")
    (tmp / "nxdomain").touch()
    compose("up", "-d", "--wait", "--no-deps", "cnemc-gateway")  # Empty DNS cache.
    compose("exec", "-T", "worker-cnemc", *PROBE, "nxdomain", gate_ip)
    assert logs("FIXTURE NXDOMAIN") > 0 and logs("FIXTURE TCP 18007") == tcp + 2
    compose("stop", "worker-cnemc", "cnemc-gateway")
    base("up", "-d", "--no-deps", "--force-recreate", "web")
    assert not base("ps", "-q", "worker-cnemc", "worker", "cnemc-gateway", "beat")
    assert base("ps", "-q", "db") == offline_db and base("ps", "-q", "redis") == offline_redis
    assert attachments(offline_db)[backend]["NetworkID"] == offline_backend
    assert base("exec", "-T", "web", "printenv", "CNEMC_MODE") == "off"
    base("exec", "-T", "web", "python", "backend/manage.py", "check", "--database", "default")
    print("PASS upstream NXDOMAIN closes path; offline Web reuses original DB/Redis and network")
    return project


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="cnemc-egress-") as folder, ExitStack() as cleanup:
        project = verify(Path(folder), cleanup)
    for kind in ("container", "network", "volume"):
        assert not run(
            "docker",
            kind,
            "ls",
            *(["-a"] if kind == "container" else []),
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + project,
        )
    print("PASS disposable project removed; real-source requests 0")
