#!/usr/bin/env python3
"""Collect S02 verification receipts in a disposable local Compose project."""

import argparse
import sys
from pathlib import Path

from s01_runtime import source_hash
from s02_disposition_checks import expiry_recovery, raw_and_mixed_batch_withdrawal, t06_watermark
from s02_history_checks import (
    PROFILE,
    broker_recovery,
    display_withdrawal,
    first_import,
    read_boundaries,
    redelivery,
    register_uci,
    revoke_while_queued,
    seed_accounts,
    source_file,
    verify_samples,
    worker_event,
)
from s02_runtime import S02Project, fresh, quality, regression, verify_upgrade


class Matrix:
    def __init__(self, project):
        self.project = project
        self.outcomes = {f"V{n:02d}": "NOT RUN" for n in range(1, 17)}
        self.outcomes["quality"] = "NOT RUN"

    def run(self, sections, operation):
        try:
            result = operation()
            if result is False:
                raise AssertionError("one or more required checks failed")
        except Exception as exc:
            # Don’t copy traceback locals, raw rows, cookies or credentials into the receipt.
            for section in sections:
                self.outcomes[section] = "FAIL"
            self.project.record(sections[0], "failure", self.project.clean_text(str(exc)))
            return False
        for section in sections:
            if self.outcomes[section] != "FAIL":
                self.outcomes[section] = "PASS"
        return True

    def block(self, sections, reason):
        for section in sections:
            self.outcomes[section] = "BLOCKED"
            self.project.record(section, "blocker", reason)


def domain_results(p):
    """Identify the F checks that complement the real H/HTTP/Worker scenarios."""
    cases = next(
        item["actual"]["cases"]
        for item in reversed(p.events)
        if item["section"] == "quality" and item["check"] == "pytest full"
    )
    obligations = {
        "V02": ["test_observation_access::"],
        "V03": ["test_observation_catalog::"],
        "V04": ["test_observation_rules::"],
        "V05": [
            "test_direct_sql_rejects_invalid_report_anchors_and_duplicate_keys",
            "test_direct_sql_cannot_create_second_current_or_promote_candidate",
            "test_product_shapes_keep_pollutant_grade_and_concentration_distinct",
        ],
        "V06": ["test_t06_watermark_advances_without_content_change"],
        "V07": [
            "test_newer_content_keeps_history_and_missing_does_not_fall_back",
            "test_conflicting_evidence_is_separate_and_replay_does_not_clear_dispute",
            "test_mapping_correction_records_basis_and_rejects_stale_replay",
        ],
        "V09": [
            "test_product_lock_serializes_initial_key_and_competing_versions",
            "test_overlapping_batch_selection_reuses_key_and_adds_only_unseen_slot",
        ],
        "V10": ["test_history_import::"],
        "V11": ["test_commit_failure_leaves_no_partial_initial_or_promoted_observation"],
        "V14": ["test_observation_disposition::"],
        "V16": ["test_revoke_wins_against_a_writer_already_waiting_for_product_lock"],
    }
    for section, required in obligations.items():
        for fragment in required:
            selected = {name: result for name, result in cases.items() if fragment in name}
            assert selected and all(result == "PASS" for result in selected.values()), fragment
            p.record(section, f"isolated F tests for {fragment}", selected)


def uci_checks(matrix, archive):
    p = matrix.project
    required = ("V08", "V09", "V10", "V11", "V12", "V13", "V16")
    if archive is None:
        matrix.block(required, "No authorized UCI archive supplied")
        return
    if not matrix.run(("V08",), lambda: source_file(p, archive)):
        matrix.block(required[1:], "Supplied archive did not match the frozen UCI material")
        return
    clients = seed_accounts(p)
    reader, operator, _ = clients
    product, epochs = register_uci(p)
    # The first real import supplies all following H scenarios. A failure still
    # leaves the independent F-only, quality and S01 regression paths runnable.
    result = []
    if not matrix.run(
        ("V09", "V10", "V11"),
        lambda: result.extend(first_import(p, archive, product, reader, operator)),
    ):
        matrix.block(
            ("V08", "V12", "V13", "V16"),
            "Confirmed H batch unavailable after initial Worker checks",
        )
        return
    batch, successful = result
    matrix.run(
        ("V08",),
        lambda: (
            worker_event(p, "V08", successful["id"], "succeeded"),
            verify_samples(p, reader, product, epochs, batch, successful),
        ),
    )
    matrix.run(("V09",), lambda: redelivery(p, product, successful, "V09"))
    matrix.run(("V16",), lambda: broker_recovery(p, archive, operator, product, batch))
    matrix.run(("V12",), lambda: read_boundaries(p, reader, operator, product, epochs, batch))
    matrix.run(("V13",), lambda: display_withdrawal(p, clients, product, epochs))
    matrix.run(("V16",), lambda: revoke_while_queued(p, archive, operator, product, successful))
    return clients


def verify(args):
    project = S02Project()
    matrix = Matrix(project)
    pending = None
    try:
        project.claim()
        initial = source_hash()
        project.record(
            "V01",
            "source baseline and digest",
            [project.cmd(["git", "rev-parse", "HEAD"]), initial],
        )
        project.record(
            "V01",
            "Docker Engine / Compose",
            [
                project.cmd(["docker", "version", "--format", "{{.Server.Version}}"]),
                project.cmd(["docker", "compose", "version", "--short"]),
            ],
        )
        print("RUN", project.name, flush=True)
        matrix.run(
            ("V01",),
            lambda: (
                verify_upgrade(project),
                project.compose("down", "--volumes", "--timeout", "5", timeout=100),
                fresh(project),
            ),
        )
        if matrix.outcomes["V01"] == "PASS":
            matrix.run(("quality",), lambda: quality(project))
            if matrix.outcomes["quality"] == "PASS":
                domain_results(project)
                for section in ("V02", "V03", "V04", "V05", "V06", "V07"):
                    matrix.outcomes[section] = "PASS"
            try:
                clients = uci_checks(matrix, args.uci_archive)
            except Exception as exc:
                project.record("V08", "setup failure", project.clean_text(str(exc)))
                matrix.outcomes["V08"] = "FAIL"
                clients = None
            if clients is None:
                existing = project.sql("SELECT count(*) FROM auth_user WHERE username='operator'")
                if existing == "1":
                    from s01_runtime import Client

                    clients = tuple(
                        Client(project.base).login(name, project.password)
                        for name in ("reader", "operator", "supervisor")
                    )
                else:
                    clients = seed_accounts(project)
            reader, operator, _ = clients
            matrix.run(("V06",), lambda: t06_watermark(project, reader))
            matrix.run(
                ("V14",),
                lambda: (
                    raw_and_mixed_batch_withdrawal(project, reader, operator),
                    expiry_recovery(project, reader),
                ),
            )
        matrix.run(("V15",), lambda: regression(project))
        project.equal("quality", "source unchanged during verification", source_hash(), initial)
    except Exception as exc:
        pending = project.clean_text(str(exc))
        project.record("runner", "verification failure", pending)
    finally:
        try:
            project.cleanup()
        except Exception as exc:
            pending = project.clean_text(str(exc))
            project.record("runner", "cleanup failure", pending)
    outcomes = matrix.outcomes
    if pending or "FAIL" in outcomes.values():
        status = "FAIL"
    elif "BLOCKED" in outcomes.values():
        status = "BLOCKED"
    elif "NOT RUN" in outcomes.values():
        status = "NOT RUN"
    else:
        status = "PASS"
    project.receipt(status, outcomes)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "NOT RUN": 3}[status]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uci-archive", type=Path, help="operator-supplied authorized official UCI ZIP"
    )
    parser.add_argument("--profile", choices=(PROFILE,), default=PROFILE)
    arguments = parser.parse_args()
    if not __debug__:
        sys.exit("Assertions must be enabled")
    if arguments.uci_archive and not arguments.uci_archive.is_file():
        parser.error("--uci-archive must identify a local regular file")
    sys.exit(verify(arguments))
