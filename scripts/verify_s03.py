#!/usr/bin/env python3
"""Verify S03 V01–V19 with synthetic data and injected faults in owned Compose projects."""

import argparse
import json
import sys

from s01_runtime import source_hash
from s03_checks import (
    backoff_and_pause,
    beat_race,
    city,
    closed_review,
    historical_boundary,
    kill_after_commit,
    lease,
    lost_confirmation,
    lost_delivery,
    no_new,
    partial_block,
    permission_change,
    request_scope_and_budget,
)
from s03_runtime import S03Project, prepare, register_job, rollback_empty, topology, upgrade
from verify_s03_products import PRODUCT_CASES
from verify_s03_products import verify as verify_products

F_CASES = tuple(f"V{n:02d}" for n in range(1, 20))


def verify(args):
    p = S03Project()
    outcomes = {f"V{n:02d}": "NOT RUN" for n in range(1, 21)}
    receipts = {}
    selected = tuple(dict.fromkeys(args.cases or F_CASES))
    runtime = set(selected).intersection(set(F_CASES) - PRODUCT_CASES.keys())
    products = tuple(case for case in selected if case in PRODUCT_CASES)

    def run(case, scenario, probe):
        key = f"{case}/{scenario}"
        try:
            probe()
        except Exception as exc:
            p.record(case, scenario + " failure", p.clean_text(str(exc)))
            receipts[key] = outcomes[case] = "FAIL"
            print("FAIL", key, p.clean_text(str(exc))[:300], flush=True)
        else:
            receipts[key] = "PASS"
            if outcomes[case] != "FAIL":
                outcomes[case] = "PASS"
            print("PASS", key, flush=True)

    def chosen(case):
        return case in runtime

    failed = False
    build = not chosen("V01")
    try:
        if runtime:
            p.claim()
        initial = source_hash()
        p.record(
            "runner",
            "evidence type F+I, sealed base and source digest",
            {
                "types": ["F", "I"],
                "head": p.cmd(["git", "rev-parse", "HEAD"]),
                "source_hash": initial,
            },
        )
        print("RUN", p.name, flush=True)
        if runtime:
            p.compose("config", "-q")
        if not build:
            run("V01", "S02 to S03", lambda: upgrade(p))
            failed = outcomes["V01"] == "FAIL"
            if failed:
                p.cleanup()
                p.claim()
            build = failed
        if build and runtime:
            p.up(build=True)
        if chosen("V01"):
            run(
                "V01",
                "Independent empty PostgreSQL rollback and forward",
                lambda: rollback_empty(p),
            )
        if chosen("V19"):
            run("V19", "isolated two Workers and one Beat", lambda: topology(p))
        if runtime - {"V01", "V19"}:
            account, reader, archivist = prepare(p)
            c_job = register_job(p, "C")
            for case, scenario, probe in (
                ("V03", "City read through Worker and Session", lambda: city(p, account, reader)),
                (
                    "V07",
                    "Beat and manual race in a retryable window",
                    lambda: beat_race(p, account, c_job),
                ),
                (
                    "V08",
                    "Redis outage, lost delivery, same UUID",
                    lambda: lost_delivery(p, account, c_job),
                ),
                (
                    "V08",
                    "Redis accepted publication with missing confirmation",
                    lambda: lost_confirmation(p, c_job),
                ),
                (
                    "V10",
                    "Final commit then kill before ACK",
                    lambda: kill_after_commit(p, account, c_job),
                ),
                ("V09", "Expired acquisition owner", lambda: lease(p, account, c_job)),
                (
                    "V10",
                    "Partial block and unchanged acquisition",
                    lambda: partial_block(p, account),
                ),
                (
                    "V10",
                    "Partial block and changed acquisition",
                    lambda: partial_block(p, account, changed=True),
                ),
                (
                    "V14",
                    "Partial block and vanished original window",
                    lambda: partial_block(p, account, switched=True),
                ),
                (
                    "V14",
                    "Latest-only cannot fetch historical target",
                    lambda: historical_boundary(p, account),
                ),
                (
                    "V12",
                    "Bounded retries and shared pause",
                    lambda: backoff_and_pause(p, account, c_job),
                ),
                (
                    "V15",
                    "Revoked queued identity and in-flight rights",
                    lambda: permission_change(p, account, reader, archivist, c_job),
                ),
                (
                    "V16",
                    "Package authority and source concurrency",
                    lambda: request_scope_and_budget(p, account, c_job),
                ),
                (
                    "V18",
                    "Closed-window independent review",
                    lambda: closed_review(p, account, reader, c_job),
                ),
                ("V11", "Empty, old and malformed data", lambda: no_new(p, account, c_job)),
            ):
                if chosen(case):
                    p.stop_beat()
                    run(case, scenario, probe)
                    # LocMem endpoint rate windows can otherwise couple unrelated cases.
                    if outcomes[case] != "FAIL":
                        p.reset_web_limits()
                        account.base = reader.base = archivist.base = p.base
            p.record("runner", "internal synthetic HTTP events", p.origin("/_events"))
        if products:
            project = S03Project()
            status = verify_products(products, project=project)
            component = project.evidence / "result.json"
            report = json.loads(component.read_text())
            outcomes.update(report["matrix"])
            failed = failed or status != 0
            p.record("runner", "independent product Compose project receipt", str(component))
        p.equal("runner", "tested source unchanged during verification", source_hash(), initial)
    except Exception as exc:
        failed = True
        p.record("runner", "execution failure", p.clean_text(str(exc)))
        print("FAIL", p.clean_text(str(exc)), flush=True)
    finally:
        try:
            p.cleanup()
            if p.owned:
                p.owned = False
                p.claim()
                p.owned = False
                p.record("runner", "owned Compose resources removed", True)
        except Exception as exc:
            failed = True
            p.record("runner", "cleanup failure", p.clean_text(str(exc)))
    p.record(
        "V20",
        "S02 and real H regression are separate from this synthetic runner",
        "scripts/verify_s02.py --uci-archive <authorized local archive>",
    )
    global_status = (
        "FAIL"
        if failed or "FAIL" in outcomes.values()
        else ("PASS" if all(outcomes[key] == "PASS" for key in selected) else "NOT RUN")
    )
    p.receipt(global_status, outcomes)
    path = p.evidence / "result.json"
    report = json.loads(path.read_text())
    path.write_text(
        json.dumps(
            report
            | {
                "scope": "synthetic F and injected faults",
                "selected": selected,
                "scenarios": receipts,
            },
            indent=2,
        )
        + "\n"
    )
    print(global_status, p.name, path, flush=True)
    return {"PASS": 0, "FAIL": 1, "NOT RUN": 3}[global_status]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        dest="cases",
        action="append",
        choices=[f"V{i:02d}" for i in range(1, 21)],
        help="select a synthetic scenario family; V20 uses the separate S02 verifier",
    )
    if not __debug__:
        sys.exit("Assertions must be enabled")
    sys.exit(verify(parser.parse_args()))
