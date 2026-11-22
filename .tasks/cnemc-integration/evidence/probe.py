"""Supplementary F/I probes for the plan's storage, history and quality assertions."""

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_cnemc as v  # noqa: E402
from s03_runtime import S03Project, prepare  # noqa: E402


def privacy(project, marker):
    # Inspect rows inside the disposable database. Only a count leaves PostgreSQL.
    match = project.sql(f"""
    CREATE TEMP TABLE f_private_matches (matches int);
    DO $$ DECLARE t record; hits int; total int:=0; BEGIN
      FOR t IN SELECT schemaname, tablename FROM pg_catalog.pg_tables WHERE schemaname='public' LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I x WHERE strpos(to_jsonb(x)::text,$1)>0',t.schemaname,t.tablename)
        INTO hits USING '{marker}'; total:=total+hits;
      END LOOP; INSERT INTO f_private_matches VALUES(total); END $$;
    SELECT matches FROM f_private_matches;
    """)
    assert match == "0"
    # Kombu uses JSON envelopes with a base64 body. Scan the decoded contents as well.
    inspected = project.compose(
        "exec",
        "-T",
        "web",
        "python",
        "-",
        marker,
        stdin="""
import base64, json, sys, redis
r=redis.Redis(host='redis'); marker=sys.argv[1].encode(); n=0
for k in r.scan_iter():
    t=r.type(k); values=[k]
    if t==b'string': values.append(r.get(k))
    elif t==b'list': values+=r.lrange(k,0,-1)
    elif t==b'hash': values+=list(r.hgetall(k).keys())+list(r.hgetall(k).values())
    elif t==b'set': values+=list(r.smembers(k))
    elif t==b'zset': values+=r.zrange(k,0,-1)
    else: raise ValueError('unexpected broker key type')
    for value in values:
        assert marker not in value
        try: obj=json.loads(value)
        except (ValueError,UnicodeDecodeError): continue
        entries=obj if isinstance(obj,list) else [obj]
        for e in entries:
            if isinstance(e,dict) and isinstance(e.get('body'),str):
                assert marker not in base64.b64decode(e['body'])
    n+=1
print(n)
""",
    )
    assert marker not in project.compose(
        "logs", "--no-color", "web", "worker", "worker-b", "beat", "redis", "db", "s03-origin"
    )
    for f in project.evidence.glob("*.json"):
        assert marker not in f.read_text()
    return dict(
        pg_matches=0,
        broker_keys=int(inspected),
        broker_matches=0,
        log_matches=0,
        receipt_file_matches=0,
    )


def check(p, operator, reader):
    p.stop_beat()
    t = v.current_hour()
    marker = "F-withheld-" + uuid4().hex
    job, product = v.setup_job(p, "A07", {"F001": ["AQI"], "F002": ["AQI"]}, repair=True)
    v.enable(operator, job)
    body = [
        v.packet(t, "F001"),
        dict(v.packet(t, "F999"), Quality=marker, PositionName=marker, SO2=marker),
    ]
    p.script("A07", {"body": body})
    run = p.post("privacy", operator, job, t)
    assert v.wait_run(p, run["run_id"])["status"] == "finished"
    v.verify_counts(p, operator, run, (2, 1, 1))
    p.script("A10", {"body": []})
    fill = p.post("empty", operator, job, t, reason="backfill")
    assert v.wait_run(p, fill["run_id"])["result"] == "no_new"
    v.verify_counts(p, operator, fill, (2, 1, 1))
    rejected = p.post("empty", operator, job, t, reason="backfill", allowed=(403, 409))
    assert rejected["code"] == "backfill_already_attempted" and p.count("A10") == 1
    city_job, city_product = v.setup_job(p, "A03", {v.CITY: ["AQI", "CO"]})
    v.enable(operator, city_job)
    p.script(
        "A03",
        {"body": dict(v.packet(t), AQI=31, CO="NA")},
        {"body": dict(v.packet(t), AQI=39, CO="NA")},
    )
    current = p.post("quality", operator, city_job, t)
    assert v.wait_run(p, current["run_id"])["status"] == "finished"
    v.verify_counts(p, operator, current, (2, 2, 1))
    conflict = p.post("quality", operator, city_job, t, reason="review")
    assert v.wait_run(p, conflict["run_id"])["status"] == "finished"
    v.verify_counts(p, operator, conflict, (2, 2, 0))
    city = int(
        p.sql(
            f"SELECT DISTINCT city_id FROM observations_cityobservation WHERE product_id={int(city_product)}"
        )
    )
    assert v.exact(reader, city_product, "city", city, "AQI", t)["reason"] == "unresolved_dispute"
    stats = privacy(p, marker)
    assert p.count("A03") == 2 and p.count("A07") == 1
    return dict(
        station_run=run["run_id"],
        empty_fill=fill["run_id"],
        empty_coverage=[2, 1, 1],
        quality_run=current["run_id"],
        conflict_run=conflict["run_id"],
        quality_coverage=[2, 2, 0],
        **stats,
    )


def main():
    if not __debug__:
        raise ValueError("Assertions required")
    p = S03Project()
    digest = v.source_hash()
    overlay = p.evidence / "f-internal.yaml"
    overlay.write_text(
        "services:\n"
        + "".join(
            f'  {n}:\n    environment:\n      CNEMC_FIXTURE_HTTP: "1"\n      CNEMC_MODE: "off"\n'
            for n in ("web", "worker", "worker-b", "beat", "migrate")
        )
    )
    p.prefix[6:6] = ["-f", str(overlay)]
    data = dict(
        project=p.name,
        source_hash=digest,
        probe_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        status="FAIL",
        evidence="F/I",
    )
    try:
        p.claim()
        p.up(build=True)
        account, reader, _ = prepare(p)
        data.update(check(p, account, reader))
        assert v.source_hash() == digest
        data["status"] = "PASS"
    except Exception as exc:
        import traceback

        frame = traceback.extract_tb(exc.__traceback__)[-1]
        data["failure"] = f"{frame.name}:{frame.lineno} " + p.clean_text(str(exc))[:500]
    finally:
        p.cleanup()
    receipt = p.evidence / "projection-guard.json"
    receipt.write_text(json.dumps(data, indent=2) + "\n")
    print(data["status"], receipt, data.get("failure", ""))
    return data["status"] == "PASS"


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
