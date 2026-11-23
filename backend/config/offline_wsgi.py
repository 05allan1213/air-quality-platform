"""Docker needs a gateway to publish loopback. Remove it before starting the offline Web app."""

import os
import socket
import struct
import sys
from pathlib import Path

HEADER = struct.Struct("=IHHII")  # struct nlmsghdr
ROUTE = struct.Struct("=BBBBBBBBI")  # struct rtmsg
RTM_NEWROUTE, RTM_DELROUTE, RTM_GETROUTE = 24, 25, 26
NLMSG_ERROR, NLMSG_DONE = 2, 3
REQUEST, ACK, DUMP = 1, 4, 0x300


def message(kind, flags, seq, body):
    return HEADER.pack(HEADER.size + len(body), kind, flags, seq, 0) + body


def responses(netlink, seq):
    while True:
        packet, sender = netlink.recvfrom(65536)
        if sender[0] != 0:
            raise RuntimeError("unexpected_route_peer")
        offset = 0
        while offset < len(packet):
            size, kind, _, number, _ = HEADER.unpack_from(packet, offset)
            if not HEADER.size <= size <= len(packet) - offset or number != seq:
                raise RuntimeError("unexpected_route_reply")
            payload = packet[offset + HEADER.size : offset + size]
            if kind == NLMSG_DONE:
                return
            if kind == NLMSG_ERROR:
                if struct.unpack_from("=i", payload)[0]:
                    raise RuntimeError("route_change_refused")
                return
            yield kind, payload
            offset += (size + 3) & ~3


def defaults(netlink, seq):
    netlink.send(message(RTM_GETROUTE, REQUEST | DUMP, seq, bytes(ROUTE.size)))
    return [
        body
        for kind, body in responses(netlink, seq)
        if kind == RTM_NEWROUTE
        and body[0] in (socket.AF_INET, socket.AF_INET6)
        and body[1] == 0
        and body[7] == 1
    ]  # RTN_UNICAST


def main():
    host = os.environ.get("AQ_HOST_NETNS", "")
    if (
        os.getpid() != 1
        or os.geteuid() != 0
        or not Path("/.dockerenv").is_file()
        or not host.isascii()
        or not host.isdecimal()
        or int(host) == os.stat("/proc/self/ns/net").st_ino
    ):
        raise SystemExit("Web bootstrap requires a private registered Docker network namespace")
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0) as netlink:
        netlink.settimeout(3)
        seq = 1
        for route in defaults(netlink, seq):
            seq += 1
            netlink.send(message(RTM_DELROUTE, REQUEST | ACK, seq, route))
            list(responses(netlink, seq))
        if defaults(netlink, seq + 1):
            raise SystemExit("external_route")
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
        raise SystemExit("web_identity")
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "gunicorn",
            "config.wsgi:application",
            "--workers",
            "1",
            "--worker-class",
            "gthread",
            "--threads",
            "8",
            "--bind",
            "0.0.0.0:8000",
            "--config",
            "python:config.gunicorn_local",
        ],
    )


if __name__ == "__main__":
    main()
