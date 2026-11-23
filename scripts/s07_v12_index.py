#!/usr/bin/env python3
"""Rebuild the reviewed S07 V12 operation matrix from its local F/I receipts."""

import json
import re
from collections import defaultdict

from s06_evidence_index import NODE, STATUS
from s06_guard import require
from s07_handoff import ROOT, read_regular, sha256

SPEC = ROOT / "docs/api/openapi.v1.json"
ROUNDS = {
    "user_and_governance": ROOT / "tmp/s06-evidence/s06-78112e9b7ed9/pytest.json",
    "native_auth_boundary": ROOT / "tmp/s06-evidence/s06-508e03ddf5fc/pytest.json",
    "backup": ROOT / "tmp/s06-evidence/s06-9177ca553f6d/pytest.json",
    "fields_and_exact_version": ROOT / "tmp/s06-evidence/s06-9f37c6462bbd/pytest.json",
    "administrator_and_worker": ROOT / "tmp/s07-evidence/s07-b-9b1a54ecb492/V12/admin.json",
    "old_s05_sessions_tickets": ROOT / "tmp/s06-evidence/s06-4021ca35f5d9/upgrade.json",
}
AUTH_BOUNDARY_TEST = (
    "tests/test_s07_contract_user_http.py::test_actual_cookie_errors_and_framework_boundaries"
)


def load(path):
    raw = read_regular(path)
    return json.loads(raw), sha256(raw)


def operation_index():
    spec, spec_sha = load(SPEC)
    definitions = {
        operation["operationId"]: (path, method, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    }
    by_status = defaultdict(lambda: defaultdict(list))
    index = {}
    boundary_checked = False
    for purpose, path in ROUNDS.items():
        data, digest = load(path)
        run = path.parent if path.parent.name != "V12" else path.parent.parent
        guard = run / "V01/result.json"
        receipt, guard_digest = load(guard)
        require(receipt["cleanup"] is True, "v12_guard_cleanup")
        require(receipt.get("guard", receipt.get("status")) == "PASS", "v12_guard_status")
        require(
            receipt["run"] == run.name and data.get("run", run.name) == run.name,
            "v12_run_mismatch",
        )
        source = data.get("source") or receipt.get("source_sha256") or receipt["source_before"]
        require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{64}", source), "v12_source")

        if path.name == "pytest.json":
            summary = data["summary"]
            require(set(summary) == set(STATUS), "v12_summary_status")
            require(
                all(type(count) is int and count >= 0 for count in summary.values()), "v12_count"
            )
            cases = [case for case in data["cases"] if case["status"] == "PASS"]
            if purpose == "user_and_governance":
                failures = [case for case in data["cases"] if case["status"] == "FAIL"]
                require(
                    len(failures) == 1
                    and failures[0]["nodeid"].startswith("tests/test_s07_exact.py::"),
                    "v12_retained_red_round",
                )
        else:
            require(data["status"] == "PASS", "v12_run_status")
            summary, cases = "PASS", [data]
        index[purpose] = {
            "receipt": path.relative_to(ROOT).as_posix(),
            "source": source,
            "sha256": digest,
            "guard_receipt": guard.relative_to(ROOT).as_posix(),
            "guard_receipt_sha256": guard_digest,
            "summary": summary,
        }
        for case in cases:
            node = case["nodeid"] if path.name == "pytest.json" else None
            require(node is None or NODE.fullmatch(node), "v12_node_name")
            if purpose == "native_auth_boundary" and node == AUTH_BOUNDARY_TEST:
                boundary_checked = True
            for row in case.get("http_contract", []):
                oid, status, count = row["operation"], row["status"], row["count"]
                require(
                    oid in definitions
                    and type(status) is int
                    and 200 <= status < 600
                    and type(count) is int
                    and 0 < count <= 10_000,
                    "v12_http_sample",
                )
                by_status[oid][status].append({"round": purpose, "case": node, "count": count})

    operations = []
    for oid, (path, method, operation) in definitions.items():
        samples = by_status[oid]
        require(any(200 <= status < 300 for status in samples), "v12_missing_http_success")
        if oid == "issueCsrf":
            require(boundary_checked, "v12_missing_csrf_method_rejection")
            samples[405].append(
                {
                    "round": "native_auth_boundary",
                    "case": AUTH_BOUNDARY_TEST,
                    "count": 1,
                    "method": "OPTIONS",
                }
            )
        require(any(status >= 400 for status in samples), "v12_missing_http_rejection")
        require(
            all(str(status) in operation["responses"] for status in samples), "v12_status_drift"
        )
        operations.append(
            {
                "operation": oid,
                "method": method.upper(),
                "path": path,
                "security": operation["security"],
                "http": {str(status): rows for status, rows in sorted(samples.items())},
            }
        )

    return {
        "scope": "S07 V12 contract candidate",
        "origin": "F/I",
        "spec": SPEC.relative_to(ROOT).as_posix(),
        "spec_sha256": spec_sha,
        "path_count": len(spec["paths"]),
        "operation_count": len(operations),
        "by_operation": operations,
        "evidence": index,
        "snapshot_notes": [
            "The mixed R round preserved the expected attribution-change RED while the repair was made in the shared worktree. Only passing cases contribute to its HTTP matrix.",
            "The attribution-change assertion and the planned exact-read consumers were re-run on the repaired snapshot. The separate F upgrade receipt checks actual S05-issued Session, cursor, saved query and CSV tickets.",
            "History submit/status used F fixtures with injected broker outcomes. H licensed-input validation remains blocked.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(operation_index(), ensure_ascii=False, indent=2))
