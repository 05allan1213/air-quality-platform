"""Task-owned F custodian, outside the read-only authority mount of the sandbox."""

import json
import re
import socket
import subprocess
from contextlib import contextmanager
from socketserver import StreamRequestHandler, UnixStreamServer
from threading import Thread
from uuid import UUID

from s06_restore_sign import sign, sign_reviewed, write

LIMIT = 2 * 1024 * 1024


def request(path, fixture, action, data):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(path))
        client.sendall(json.dumps([fixture, action, data]).encode("ascii") + b"\n")
        with client.makefile("rb") as reply:
            result = json.loads(reply.readline(LIMIT))
    if result != {"accepted": True}:
        raise RuntimeError("custodian_fixture_rejected")


@contextmanager
def custodian(path, root):
    publication = root / "public"
    publication.mkdir(mode=0o700)
    key = root / "offline.pem"
    subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)],
        check=True,
        capture_output=True,
    )
    public = subprocess.check_output(["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout"])
    public_path = publication / "public.pem"
    public_path.write_bytes(public)
    public_path.chmod(0o600)

    class Handler(StreamRequestHandler):
        timeout = 10

        def handle(self):
            accepted = False
            try:
                fixture, action, data = json.loads(self.rfile.readline(LIMIT))
                if not re.fullmatch(r"[0-9a-f]{32}", fixture):
                    raise ValueError
                scope = publication / fixture
                if action == "configure":
                    scope.mkdir(mode=0o700)
                    pem = scope / "public.pem"
                    pem.write_bytes(public)
                    pem.chmod(0o600)
                    identity = scope / "authority_id"
                    identity.write_text(f"F-s06-{fixture}")
                    identity.chmod(0o600)
                    write(scope, "trust.json", data["trust"])
                    write(scope, "subjects.json", data["subjects"])
                elif action == "replace_subjects":
                    write(scope, "subjects.json", data)
                elif action == "sign":
                    sign_reviewed(key, scope, data)
                elif action in {
                    "delete_publish",
                    "delete_restore",
                    "replace_publish",
                    "replace_restore",
                }:
                    op = UUID(data["operation"])
                    operation, proof_action = action.split("_")
                    filename = f"{op}.{proof_action}.json"
                    if operation == "replace":
                        write(scope, filename, data["proof"])
                    else:
                        (scope / filename).unlink()
                elif action == "replace_signer":
                    op = UUID(data["operation"])
                    filename = f"{op}.publish.json"
                    original = json.loads((scope / filename).read_bytes())
                    outsider = root / "alternate.pem"
                    subprocess.run(
                        [
                            "/usr/bin/openssl",
                            "genpkey",
                            "-algorithm",
                            "ED25519",
                            "-out",
                            str(outsider),
                        ],
                        check=True,
                        capture_output=True,
                    )
                    original["seal"] = sign(outsider, original["seal"]["payload"])
                    write(scope, filename, original)
                else:
                    raise ValueError
                accepted = True
            except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
                pass
            self.wfile.write(json.dumps({"accepted": accepted}).encode("ascii") + b"\n")

    with UnixStreamServer(str(path), Handler) as server:
        path.chmod(0o600)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield publication
        finally:
            server.shutdown()
            thread.join()
            path.unlink()
