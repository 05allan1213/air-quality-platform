"""Real H supplement for the guarded formal WSGI entry. Pass the verified inner ZIP on stdin."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from h_case import READER, call, check, discover, favorite, load_inner
from s04_checks import Probe
from s07_compose_runtime import applications
from s07_offline_compose import ComposeOffline
from verify_s07 import ComposeRun

TOOLS = Path(__file__).resolve().parent
CASE = (TOOLS / "h_case.py").read_bytes()
PAYLOAD = """
import sys, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.contrib.auth.models import User
from s04_checks import Probe
operation,run=sys.argv[1:]
guard(run)
password=os.environ['H_SESSION_PASSWORD']
if operation == 'register':
    product,op,until=register(password)
    output=grants(product)
elif operation == 'stage':
    output=stage(load_inner(sys.stdin.buffer),User.objects.get(username=OPERATOR))
else:
    client=Probe('http://127.0.0.1:8000').login(READER,password)
    rows=discover(client)
    if operation in ('full','cold'):
        output=reconcile(client,rows,load_inner(sys.stdin.buffer),full=operation=='full')
    else:
        assert operation=='withdraw'
        output=withdrawal(client,rows)
print(json.dumps(output,default=str,ensure_ascii=False))
"""


def invoke(p, offline, service, action, raw=b""):
    cid = offline.id(service)
    # Transfer only pinned archive bytes via stdin. No host data directory enters an app.
    result = subprocess.run(
        [
            *p.cmd,
            "exec",
            "-i",
            "--user",
            "10001:10001",
            "--env",
            "H_SESSION_PASSWORD",
            "--env",
            "PYTHONPATH=/app/backend:/app/scripts:/app/tests",
            cid,
            "python",
            "-B",
            "-c",
            CASE.decode() + PAYLOAD,
            action,
            p.name,
        ],
        env=p.env | {"H_SESSION_PASSWORD": offline.password},
        input=raw,
        capture_output=True,
        timeout=230,
    )
    if result.returncode:
        # In the owned private evidence directory only. Never copy this into the GPT archive.
        (p.root / (action + "-diagnostic.txt")).write_bytes(result.stderr)
        raise RuntimeError("H case failed: " + action)
    return json.loads(result.stdout)


def run(raw):
    p = ComposeRun(offline=True)
    print(p.name, flush=True)
    p.receipt.update(evidence_type=["H"], scope="V01-A/H")
    report = {
        "run": p.name,
        "origin": "H",
        "status": "FAIL",
        "started_utc": datetime.now(UTC).isoformat(),
        "replay_sha256": {
            "h_compose.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "h_case.py": hashlib.sha256(CASE).hexdigest(),
        },
        "input_sha256": hashlib.sha256(raw).hexdigest(),
    }
    try:
        with p.guarded(), applications(p) as apps:
            off = ComposeOffline(p, apps, secrets.token_urlsafe(32))
            apps.start("migrate")
            off.all_start()
            report["network_before"] = off.network()
            report["image"] = apps.image
            basis = invoke(p, off, "web", "register")
            (p.root / "h-grants.json").write_text(
                json.dumps(basis, indent=2, ensure_ascii=False) + "\n"
            )
            report["active_grants"] = len(basis)
            report["import"] = invoke(p, off, "worker", "stage", raw)
            reader = Probe(off.base).login(READER, off.password)
            rows = discover(reader)
            fav = favorite(reader, rows)
            report["navigation"] = {
                "catalog_pages": len(rows),
                "named_sites": [r["name"] for r in rows],
                "site_and_history_links": 2 * len(rows),
                "favorite": 200,
            }
            report["research"] = invoke(p, off, "web", "full", raw)
            volume = off.volume()
            before = off.broker_id()
            off.all_stop()
            off.stop("redis")
            off.stop("pg")
            report["all_stopped"] = off.stopped()
            off.start("pg")
            off.start("redis")
            off.all_start()
            check(off.volume() == volume and off.broker_id() != before, "cold start identity")
            report["network_after"] = off.network()
            # The loopback port is allocated again on restart. Keep the existing cookie jar.
            reader.base = off.base
            check(
                call(reader, fav)["id"] and discover(reader) == rows,
                "old session after full cold start",
            )
            new = Probe(off.base).login(READER, off.password)
            check(discover(new) == rows, "new session after full cold start")
            report["cold_research"] = invoke(p, off, "web", "cold", raw)
            same = [r for r in report["research"]["results"] if r["indicator"] == "CO"][0]
            check(report["cold_research"]["results"] == [same], "cold SQL/HTTP/CSV same result")
            report["cold"] = {
                "volume_before": volume,
                "volume_after": off.volume(),
                "new_broker": True,
                "old_session": 200,
                "new_session": 200,
            }
            report["withdrawal"] = invoke(p, off, "web", "withdraw")
            report["source_access"] = p.app_sql(
                "SELECT (SELECT count(*) FROM collection_job),(SELECT count(*) FROM collection_run)"
            )
            check(report["source_access"] == "0|0", "no collection activity")
        report["status"] = "PASS"
    finally:
        if p.root.exists():
            report["cleanup"] = p.receipt.get("after")
            report["finished_utc"] = datetime.now(UTC).isoformat()
            (p.root / "h-compose.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            )
    print("A", report["status"], p.name)


if __name__ == "__main__":
    os.umask(0o077)
    run(load_inner(sys.stdin.buffer))
