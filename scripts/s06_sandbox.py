"""Linux process isolation for S06 HTTP tests and disposable runtime checks."""

import errno
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

import pytest
from s01_runtime import ROOT, source_hash
from s06_guard import IsolationError, check_database, check_environment, require
from s06_observed import safe_observed

BASELINE_MOUNT = Path("/tmp/s06-baseline-code")


def sandbox_command(env, snapshot, action, *args, old=None):
    root = check_environment(env)
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    require(bwrap is not None, "missing_bwrap")
    python = Path(sys.executable)
    interpreter_home = python.resolve().parents[1]
    command = [
        bwrap,
        "--unshare-user",
        "--disable-userns",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--new-session",
        "--die-with-parent",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--ro-bind",
        "/etc/ssl/certs",
        "/etc/ssl/certs",
    ]
    # Keep the host's home, /run sockets, .env, repository metadata and other
    # evidence directories outside the sandbox. The host supplies only this run's socket.
    require(snapshot.parent == root and snapshot.resolve(strict=True) == snapshot, "snapshot_root")
    for path in snapshot.iterdir():
        require(path.resolve(strict=True) == path, "redirected_source_path")
        command += ["--ro-bind", str(path), str(ROOT / path.name)]
    command += ["--ro-bind", str(ROOT / ".venv"), str(ROOT / ".venv")]
    if not interpreter_home.is_relative_to("/usr"):
        require(interpreter_home.name.startswith("cpython-"), "unexpected_interpreter_home")
        command += ["--ro-bind", str(interpreter_home), str(interpreter_home)]
    if old is not None:
        require(
            action == "upgrade"
            and old.parent.parent == root
            and old.name == "backend"
            and old.resolve(strict=True) == old
            and old.is_dir(),
            "invalid_s05_baseline_mount",
        )
        command += [
            "--ro-bind",
            str(old),
            str(BASELINE_MOUNT),
            "--setenv",
            "S06_BASELINE_CODE",
            str(BASELINE_MOUNT),
        ]
    command += ["--bind", str(root), str(root), "--tmpfs", str(snapshot)]
    if old is not None:
        command += ["--tmpfs", str(old.parent)]
    command += ["--chdir", str(ROOT), "--"]
    return command + [str(python), str(ROOT / "scripts/s06_sandbox.py"), action, *args]


def check_network():
    namespace = os.readlink("/proc/self/ns/net")
    require(namespace != os.environ.get("S06_HOST_NET_NS"), "host_network_namespace")
    require(socket.if_nameindex() == [(1, "lo")], "non_loopback_interface")
    require(not Path("/run").exists(), "visible_host_runtime")
    # A reserved address has no route in this namespace. A UDP connect creates no packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        try:
            datagram.connect(("192.0.2.1", 9))
        except OSError as exc:
            require(exc.errno == errno.ENETUNREACH, "unexpected_network_error")
        else:
            raise IsolationError("external_network_route")
    with socket.create_server(("127.0.0.1", 0)) as listener:
        with socket.create_connection(listener.getsockname(), timeout=2):
            connection, _ = listener.accept()
            connection.close()
    return {
        "private_namespace": True,
        "interfaces": ["lo"],
        "external_route": False,
        "host_runtime_visible": False,
        "loopback_tcp": True,
        "namespace": namespace,
    }


TEST_FILE = r"tests/(?:collection/)?test_[A-Za-z0-9_]+\.py"
TEST_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
SELECTOR = re.compile(rf"{TEST_FILE}(?:::{TEST_NAME})*")
SOURCE_FILE = re.compile(r"(?:backend|scripts|tests)/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach only structural exception evidence to the temporary JUnit XML."""
    from psycopg import Error

    report = yield
    if not report.failed or call.excinfo is None:
        return report
    exc = call.excinfo.value
    frames = []
    states = []
    types = []
    visited = set()
    while exc is not None and id(exc) not in visited and len(visited) < 5:
        visited.add(id(exc))
        if re.fullmatch(TEST_NAME, type(exc).__name__):
            types.append(type(exc).__name__)
        if isinstance(exc, Error) and exc.sqlstate and re.fullmatch(r"[A-Z0-9]{5}", exc.sqlstate):
            states.append(exc.sqlstate)
        tb = exc.__traceback__
        while tb is not None:
            filename = Path(tb.tb_frame.f_code.co_filename)
            if filename.is_relative_to(ROOT):
                path = filename.relative_to(ROOT).as_posix()
                if SOURCE_FILE.fullmatch(path):
                    frames.append(f"{path}:{tb.tb_lineno}")
            tb = tb.tb_next
        exc = exc.__cause__ or exc.__context__
    metadata = {
        "phase": report.when,
        "types": types,
        "sqlstates": list(dict.fromkeys(states)),
        "frames": list(dict.fromkeys(frames))[-8:],
    }
    prop = ("s06_failure", json.dumps(metadata))
    item.user_properties.append(prop)
    report.user_properties.append(prop)
    return report


def summarize_failure(case):
    for prop in case.iterfind("properties/property[@name='s06_failure']"):
        data = json.loads(prop.attrib["value"])
        if data["phase"] not in {"setup", "call", "teardown"}:
            continue
        yield {
            "phase": data["phase"],
            "types": [value for value in data["types"] if re.fullmatch(TEST_NAME, value)],
            "sqlstates": [
                value for value in data["sqlstates"] if re.fullmatch(r"[A-Z0-9]{5}", value)
            ],
            "frames": [
                value
                for value in data["frames"]
                if re.fullmatch(rf"{SOURCE_FILE.pattern}:[0-9]+", value)
            ],
        }


def summarize_observed(case):
    property_node = case.find("properties/property[@name='s06_observed']")
    if property_node is None:
        return {}
    body = property_node.get("value", "")
    if len(body) > 4096:
        return {}
    try:
        return safe_observed(json.loads(body))
    except (ValueError, RecursionError):
        return {}


def summarize_pytest(xml_path):
    """Drop assertion text, output, arbitrary properties, and parameter values."""
    counts = Counter({"PASS": 0, "FAIL": 0, "NOT RUN": 0})
    variants = Counter()
    cases = []
    for case in ElementTree.parse(xml_path).iterfind("testsuite/testcase"):
        file = case.get("file", "")
        function, bracket, _ = case.get("name", "").partition("[")
        names = case.get("classname", "").split(".")[len(file.removesuffix(".py").split("/")) :]
        if re.fullmatch(TEST_FILE, file) and all(
            re.fullmatch(TEST_NAME, n) for n in [*names, function]
        ):
            nodeid = "::".join([file, *names, function])
        elif re.fullmatch(TEST_FILE, file):
            nodeid = file
        else:
            nodeid = "pytest_session"
        if bracket:
            variants[nodeid] += 1
            nodeid += f"[case-{variants[nodeid]}]"
        status = "PASS"
        if case.find("error") is not None or case.find("failure") is not None:
            status = "FAIL"
        elif case.find("skipped") is not None:
            status = "NOT RUN"
        seconds = float(case.get("time", 0))
        require(math.isfinite(seconds) and seconds >= 0, "invalid_pytest_duration")
        result = {"nodeid": nodeid, "status": status, "duration_seconds": seconds}
        if status == "FAIL":
            result["failure"] = list(summarize_failure(case))
        if status == "PASS" and (observed := summarize_observed(case)):
            result["observed"] = observed
        cases.append(result)
        counts[status] += 1
    return dict(counts), cases


def run_pytest(root, network, selectors):
    require(all(SELECTOR.fullmatch(arg) for arg in selectors), "invalid_test_selector")
    xml = Path("/tmp/s06-pytest.xml")
    exit_code = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--ds=s06_settings",
            "-p",
            "s06_sandbox",
            "-o",
            "cache_dir=/tmp/pytest-cache",
            "--basetemp=/tmp/pytest-run",
            "--junitxml",
            str(xml),
            "-o",
            "junit_family=xunit1",
            "-o",
            "junit_logging=no",
            "--tb=short",
            "-q",
            *selectors,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    receipt = {
        "run": os.environ["S06_RUN"],
        "source": source_hash(),
        "network": network,
        "selectors": selectors or ["tests"],
        "exit_code": exit_code,
        "summary": {},
        "cases": [],
    }
    try:
        receipt["summary"], receipt["cases"] = summarize_pytest(xml)
    except (ValueError, OSError, ElementTree.ParseError, IsolationError) as exc:
        receipt["receipt_error"] = type(exc).__name__
        exit_code = exit_code or 1
    finally:
        xml.unlink(missing_ok=True)
        (root / "pytest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"pytest exit={exit_code}: {receipt['summary']}", flush=True)
    for case in receipt["cases"]:
        if case["status"] != "PASS":
            print(f"{case['status']} {case['nodeid']}", flush=True)
            for failure in case.get("failure", []):
                print(json.dumps(failure), flush=True)
    return exit_code


def main():
    root = check_environment(os.environ)
    network = check_network()
    check_database(os.environ, expected_marker=os.environ["S06_RUN"], empty=True)
    action = sys.argv[1]
    if action == "probe":
        import django
        from django.conf import settings
        from django.db.migrations.loader import MigrationLoader

        django.setup()
        print(
            json.dumps(
                {
                    "sandbox": network,
                    "source": source_hash(),
                    "rejected": ["external_network_route"],
                    "leaves": MigrationLoader(None).graph.leaf_nodes(),
                    "queues": sorted(
                        {settings.CELERY_TASK_DEFAULT_QUEUE}
                        | {v["queue"] for v in settings.CELERY_TASK_ROUTES.values()}
                    ),
                    "mode": settings.CNEMC_MODE,
                    "broker": settings.CELERY_BROKER_URL,
                }
            )
        )
        return 0
    if action == "pytest":
        return run_pytest(root, network, sys.argv[2:])
    if action == "upgrade":
        require(
            os.environ.get("S06_BASELINE_CODE") == str(BASELINE_MOUNT)
            and BASELINE_MOUNT.is_dir()
            and os.statvfs(BASELINE_MOUNT).f_flag & os.ST_RDONLY,
            "s05_baseline_mount_not_read_only",
        )
        from s06_upgrade import run

        return run(root)
    if action == "collection":
        from s06_runtime import verify_collection_services

        return verify_collection_services(root, network)
    require(action == "services", "invalid_sandbox_action")
    from s06_runtime import verify_services

    return verify_services(root, network)


if __name__ == "__main__":
    sys.exit(main())
