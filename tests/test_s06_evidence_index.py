"""Verify the evidence projection without starting application services."""

import json

import pytest
from s06_evidence_index import EvidenceError, build, runtime_receipt

RUN = "s06-0123456789ab"
SOURCE = "a" * 64
NODE = "tests/test_s06_backup.py::test_v14_failed_restore[case-1]"


def write(path, body):
    path.write_text(json.dumps({"run": RUN, **body}))


@pytest.fixture
def receipts(tmp_path):
    path = tmp_path / "tmp" / "s06-evidence" / RUN
    (path / "V01").mkdir(parents=True)
    write(
        path / "V01" / "result.json",
        {
            "action": "pytest",
            "guard": "PASS",
            "result": "FAIL",
            "cleanup": True,
            "source_before": SOURCE,
            "cookie": "fixture cookie should never be published",
        },
    )
    return path


def test_projection_keeps_observed_status_and_scopes_only(receipts, tmp_path):
    write(
        receipts / "pytest.json",
        {
            "source": SOURCE,
            "summary": {"PASS": 1, "FAIL": 1, "NOT RUN": 1},
            "cases": [
                {
                    "nodeid": NODE,
                    "status": "FAIL",
                    "failure": [
                        {
                            "phase": "call",
                            "types": ["AssertionError"],
                            "frames": ["tests/test_s06_backup.py:102", "/unrelated/local/file:7"],
                            "message": "synthetic plaintext credential",
                        }
                    ],
                    "payload": "secret",
                },
                {
                    "nodeid": "tests/test_s06_accounts.py::test_csv_denied",
                    "status": "PASS",
                    "observed": {
                        "condition": "C04",
                        "csv_bytes": 0,
                        "order": "secret",
                        "cookie": "secret",
                    },
                },
                {"nodeid": "tests/test_s02_history.py::test_actual_history", "status": "NOT RUN"},
            ],
        },
    )
    rules = {RUN: [({"V14", "P07"}, "tests/test_s06_backup.py::test_v14_", True)]}
    index = build(tmp_path, {RUN: {"V18", "P09"}}, rules)
    assert index == build(tmp_path, {RUN: {"P09", "V18"}}, rules)
    run = index["runs"][RUN]
    assert run["V01"]["guard"] == "PASS" and run["V01"]["result"] == "FAIL"
    assert [case["status"] for case in run["pytest"]["cases"]] == ["FAIL", "PASS", "NOT RUN"]
    assert run["pytest"]["cases"][0]["labels"] == ["P07", "P09", "V14", "V18"]
    assert run["pytest"]["cases"][0]["failure"][0]["frames"] == ["tests/test_s06_backup.py:102"]
    assert run["pytest"]["cases"][1]["observed"] == {"condition": "C04", "csv_bytes": 0}
    result = json.dumps(index)
    assert not any(value in result for value in ("cookie", "credential", "payload", "unrelated"))


def test_rejects_unsafe_run_or_indirect_receipt(receipts, tmp_path):
    with pytest.raises(EvidenceError, match="invalid_run_id"):
        build(tmp_path, {"../outside": {"V01"}}, {})
    outside = tmp_path / "outside.json"
    write(outside, {"source": SOURCE})
    (receipts / "pytest.json").symlink_to(outside)
    with pytest.raises(OSError):
        build(tmp_path, {RUN: {"V01"}}, {})


def test_rejects_false_summary_and_unmatched_rule(receipts, tmp_path):
    row = {"nodeid": NODE, "status": "NOT RUN"}
    write(receipts / "pytest.json", {"source": SOURCE, "summary": {"PASS": 1}, "cases": [row]})
    with pytest.raises(EvidenceError, match="pytest_summary_mismatch"):
        build(tmp_path, {RUN: {"V14"}}, {})
    write(
        receipts / "pytest.json",
        {
            "source": SOURCE,
            "summary": {"PASS": 0, "FAIL": 0, "NOT RUN": 1},
            "cases": [row],
        },
    )
    with pytest.raises(EvidenceError, match="case_selector_not_observed"):
        build(tmp_path, {RUN: {"V14"}}, {RUN: [({"V13"}, "tests/test_cases.py", False)]})


def test_runtime_projects_only_known_quality_codes_counts_and_real_callback_flag():
    checks = {
        "collection_redelivery": {"scanner_preceded_committed_callback": True},
        "V05_quality_detail": {
            "loopback_fetches": 6,
            "allowed": {
                "items": 7,
                "receipts": 15,
                "structure_event_count": 1,
                "legal_empty_status": "no_new",
                "structure_status": "not_safe",
                "event_codes": ["invalid_time", "unreviewed_text"],
                "conflict_remains_disputed": True,
            },
            "ledger_only": {"items": "not_safe", "event_codes": []},
        },
    }
    receipt = runtime_receipt({"result": "PASS", "checks": checks}, RUN)["checks"]
    assert receipt["collection_redelivery"] == {"scanner_preceded_committed_callback": True}
    assert receipt["V05_quality_detail"] == {
        "loopback_fetches": 6,
        "allowed": {
            "items": 7,
            "receipts": 15,
            "structure_event_count": 1,
            "legal_empty_status": "no_new",
            "event_codes": ["invalid_time"],
            "conflict_remains_disputed": True,
        },
        "ledger_only": {"event_codes": []},
    }
