"""Private endpoints and in-container checks for verify_cnemc_egress.py (no business tasks)."""

import asyncio
import errno
import json
import socket
import ssl
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import BaseRequestHandler, UDPServer
from threading import Thread
from types import SimpleNamespace

HOST = "air.cnemc.cn"
PORT = 18007
BODY = b'{"fixture":"owned-egress-test"}'
# Answer exactly one DNS name. No upstream resolver, including on unexpected queries.
QUESTION = b"\x03air\x05cnemc\x02cn\x00"


def serve(address):
    class DNS(BaseRequestHandler):
        def handle(self):
            packet, stream = self.request
            if len(packet) < 17 or packet[4:6] != b"\x00\x01":
                return
            end = packet.find(b"\0", 12) + 5
            if end < 17 or end > len(packet):
                return
            question = packet[12:end]
            known = question[:-4].lower() == QUESTION
            if known:
                print("FIXTURE DNS target", flush=True)
                if Path("/fixture/nxdomain").exists():
                    known = False
                    print("FIXTURE NXDOMAIN", flush=True)
            answer = b""
            if known and question[-4:] == struct.pack("!HH", 1, 1):
                answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 10, 4) + socket.inet_aton(address)
                print("FIXTURE DNS A", flush=True)
            flags = 0x8580 if known else 0x8583
            header = packet[:2] + struct.pack("!5H", flags, 1, int(bool(answer)), 0, 0)
            stream.sendto(header + question + answer, self.client_address)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/egress-fixture":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *args):
            pass

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain("/fixture/cert.pem", "/fixture/key.pem")

    class Origin(ThreadingHTTPServer):
        def get_request(self):
            sock, peer = super().get_request()
            sock.settimeout(3)
            print(f"FIXTURE TCP {self.server_port}", flush=True)
            return tls.wrap_socket(sock, server_side=True), peer

    dns = UDPServer(("0.0.0.0", 1053), DNS)
    servers = [Origin(("0.0.0.0", port), Handler) for port in (PORT, PORT + 1)]
    for server in servers:
        Thread(target=server.serve_forever, daemon=True).start()
    print("FIXTURE READY", flush=True)
    dns.serve_forever()


def check_denied(address, port):
    try:
        with socket.create_connection((address, port), 1):
            pass
    except OSError as exc:
        # Numeric destinations: a DNS failure or unrelated client error is not a pass.
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED):
            return
        raise
    raise AssertionError(f"unexpected TCP access to {address}:{port}")


def check_internal():
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    assert routes and not any(r.split()[1] == "00000000" for r in routes), routes
    for host, port in [("db", 5432), ("redis", 6379)]:
        with socket.create_connection((host, port), 2):
            pass


def expect_tls_rejection(gateway, server_name):
    context = ssl.create_default_context(cafile="/tmp/egress-cert.pem")
    with socket.create_connection((gateway, PORT), 3) as conn:
        try:
            context.wrap_socket(conn, server_hostname=server_name).close()
        except (ConnectionResetError, ssl.SSLEOFError):
            return
    raise AssertionError(f"gateway accepted {server_name}")


def reject_hellos(gateway):
    for server_name in ("other.invalid", gateway):  # An IP literal sends no SNI.
        expect_tls_rejection(gateway, server_name)
    with socket.create_connection((gateway, PORT), 3) as conn:
        conn.sendall(b"GET /egress-fixture HTTP/1.0\r\n\r\n")
        try:
            assert conn.recv(1) == b""
        except ConnectionResetError:
            pass


async def https(gateway):
    import httpx
    from collection.adapters.transport import WireError, _fetch

    assert socket.gethostbyname_ex(HOST)[2] == [gateway]
    url = f"https://{HOST}:{PORT}"
    # Trust the private CA in this process only. No production trust-store change.
    context = ssl.create_default_context(cafile="/tmp/egress-cert.pem")
    transport = httpx.AsyncHTTPTransport(verify=context, trust_env=False, retries=0)
    async with httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=2, read=5, write=5, pool=1),
    ) as client:
        reply = await client.get(url + "/egress-fixture")
        assert reply.status_code == 200 and reply.content == BODY
        print(
            json.dumps(
                {
                    "worker_dns": gateway,
                    "port": PORT,
                    "httpx": httpx.__version__,
                    "tls": reply.extensions["network_stream"]
                    .get_extra_info("ssl_object")
                    .version(),
                    "https": reply.status_code,
                }
            )
        )
    # Exercise the unmodified application transport against the fixture's untrusted cert.
    try:
        await _fetch(
            SimpleNamespace(method="GET", path="/egress-fixture", max_bytes=128),
            url,
            None,
            None,
            {},
        )
    except WireError as exc:
        assert exc.code == "transport_security", exc.code
    else:
        raise AssertionError("private fixture certificate trusted by application")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "serve":
        serve(*args)
    elif cmd == "default":
        check_internal()
        for addr in args:
            check_denied(addr, PORT)
    elif cmd == "dedicated":
        gateway, outside = args
        check_internal()
        assert socket.gethostbyname_ex(HOST)[2] == [gateway]
        check_denied(gateway, PORT + 1)
        check_denied(outside, PORT)
        check_denied(outside, PORT + 1)
        reject_hellos(gateway)
    elif cmd == "uplink":
        outside, gateway = args
        context = ssl.create_default_context(cafile="/tmp/egress-cert.pem")
        for port in (PORT, PORT + 1):
            with socket.create_connection((outside, port), 2) as conn:
                with context.wrap_socket(conn, server_hostname=HOST) as stream:
                    stream.sendall(b"GET /egress-fixture HTTP/1.0\r\n\r\n")
                    with stream.makefile("rb") as response:
                        assert response.read().endswith(BODY)
        check_denied(gateway, PORT)
    elif cmd == "nxdomain":
        expect_tls_rejection(args[0], HOST)
    elif cmd == "https":
        asyncio.run(https(*args))
    else:
        raise ValueError(cmd)
