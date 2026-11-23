"""Apply the V01 Web namespace guard before executing the base Compose Web command."""

import os
import runpy
import socket
import sys
from pathlib import Path

BASE_WEB = ["python", "backend/manage.py", "runserver", "0.0.0.0:8000", "--noreload"]


def main():
    host = os.environ.get("AQ_HOST_NETNS", "")
    if (
        sys.argv[1:] != BASE_WEB
        or os.getpid() != 1
        or os.geteuid() != 0
        or not Path("/.dockerenv").is_file()
        or not host.isdecimal()
        or int(host) == os.stat("/proc/self/ns/net").st_ino
    ):
        raise SystemExit("invalid_legacy_web_namespace")
    guard = runpy.run_path("/app/backend/config/offline_wsgi.py")
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0) as netlink:
        netlink.settimeout(3)
        sequence = 1
        for route in guard["defaults"](netlink, sequence):
            sequence += 1
            netlink.send(
                guard["message"](
                    guard["RTM_DELROUTE"], guard["REQUEST"] | guard["ACK"], sequence, route
                )
            )
            list(guard["responses"](netlink, sequence))
        if guard["defaults"](netlink, sequence + 1):
            raise SystemExit("legacy_web_external_route")
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if (
        any(int(status[field], 16) for field in ("CapEff", "CapPrm", "CapAmb"))
        or int(status["NoNewPrivs"]) != 1
    ):
        raise SystemExit("legacy_web_identity")
    os.execv(sys.executable, BASE_WEB)


if __name__ == "__main__":
    main()
