#!/usr/bin/env python3
"""Project selected S06 run receipts onto a publishable, deterministic JSON index."""

import argparse
import json
import math
import os
import re
import stat
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from s06_observed import safe_observed

ROOT = Path(__file__).resolve().parents[1]
RUN = re.compile(r"s06-[0-9a-f]{12}")
LABEL = re.compile(r"(?:P0[1-9]|V(?:0[1-9]|1[0-9]))")
TEST = r"tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py"
NAME = r"[A-Za-z_][A-Za-z0-9_]{0,127}"
NODE = re.compile(rf"(?:pytest_session|{TEST}(?:::{NAME})*)(?:\[case-[1-9][0-9]*\])?")
FRAME = re.compile(r"(?:backend|scripts|tests)/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py:[1-9][0-9]*")
STATUS = ("PASS", "FAIL", "NOT RUN")
ACTION_FILE = {
    "guard": None,
    "pytest": "pytest.json",
    "services": "runtime.json",
    "collection": "runtime.json",
    "upgrade": "upgrade.json",
}
SIZE_LIMIT = 8 * 1024 * 1024


class EvidenceError(ValueError):
    pass


@contextmanager
def directory(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def read_receipt(parent, filename, run, *, optional=False):
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return None
        raise
    with os.fdopen(fd, "rb") as file:
        meta = os.fstat(file.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > SIZE_LIMIT:
            raise EvidenceError("unsafe_receipt_file")
        body = file.read(SIZE_LIMIT + 1)
    if len(body) > SIZE_LIMIT:
        raise EvidenceError("oversized_receipt")
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise EvidenceError("malformed_receipt") from exc
    if not isinstance(data, dict) or data.get("run") != run:
        raise EvidenceError("receipt_run_mismatch")
    return data


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def scalar(value):
    return (
        value is None
        or type(value) in (int, bool)
        or (type(value) is float and math.isfinite(value))
        or matches(r"[A-Za-z_][A-Za-z0-9_]{0,95}|[0-9]+(?:\.[0-9]+){1,3}", value)
        or matches(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value)
    )


def keep(data, fields):
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in fields.split() if key in data and scalar(data[key])}


def include(result, data, key, project):
    if isinstance(data, dict) and key in data:
        result[key] = project(data[key])


def tokens(items, pattern):
    return [item for item in items if matches(pattern, item)] if isinstance(items, list) else []


def object_rows(items):
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def digest_fields(data, fields, size):
    return {
        key: data[key]
        for key in fields.split()
        if isinstance(data, dict) and matches(rf"[0-9a-f]{{{size}}}", data.get(key))
    }


def network(data):
    result = keep(data, "private_namespace external_route host_runtime_visible loopback_tcp")
    include(result, data, "interfaces", lambda value: tokens(value, r"lo"))
    return result


def guard_receipt(data):
    result = keep(
        data,
        "origin action guard action_exit cleanup network postgres_stop_exit remaining_containers source_mode failure cleanup_failure",
    )
    if data.get("origin") == "F/I":
        result["origin"] = "F/I"
    result["guard"] = read_status(data.get("guard"))
    result["result"] = read_status(data.get("result"))
    result.update(digest_fields(data, "source_before source_after workspace_source_after", 64))
    if matches(r"sha256:[0-9a-f]{64}", data.get("image")):
        result["image"] = data["image"]
    if isinstance(leaves := data.get("migration_leaves"), list):
        result["migration_leaves"] = [
            pair
            for pair in leaves
            if isinstance(pair, list)
            and len(pair) == 2
            and matches(NAME, pair[0])
            and matches(r"[0-9]{4}_[a-z0-9_]{1,100}", pair[1])
        ]
    include(
        result,
        data,
        "baseline",
        lambda row: digest_fields(row, "commit", 40) | digest_fields(row, "source", 64),
    )
    include(result, data, "sandbox", network)
    include(result, data, "rejected", lambda rows: tokens(rows, NAME))
    include(
        result,
        data,
        "postgres_injection",
        lambda row: keep(row, "owned_container_paused http_acknowledged owned_database_restored"),
    )
    include(
        result,
        data,
        "restore_target",
        lambda row: keep(row, "separate_role isolated_database target_count authority_mount"),
    )
    for key in ("pg_before", "pg_after_rejections"):
        include(
            result, data, key, lambda row: keep(row, "socket_only postgres_version business_tables")
        )
    return result


def read_status(value):
    if value not in STATUS:
        raise EvidenceError("invalid_receipt_status")
    return value


def pytest_receipt(data, labels, rules):
    cases = []
    rows = data.get("cases")
    if not isinstance(rows, list):
        raise EvidenceError("invalid_pytest_cases")
    for row in rows:
        if not isinstance(row, dict) or not matches(NODE, row.get("nodeid")):
            raise EvidenceError("invalid_pytest_node")
        node = row["nodeid"]
        case = keep(row, "duration_seconds") | {
            "nodeid": node,
            "status": read_status(row.get("status")),
        }
        if case["status"] == "PASS" and (facts := safe_observed(row.get("observed"))):
            case["observed"] = facts
        selected = set(labels)
        for rule in rules:
            tags, selector, prefix = rule
            if node == selector or (prefix and node.startswith(selector)):
                selected.update(tags)
        case["labels"] = sorted(selected)
        if case["status"] == "FAIL" and isinstance(row.get("failure"), list):
            case["failure"] = [
                keep(failure, "phase")
                | {
                    "types": tokens(failure.get("types"), NAME),
                    "sqlstates": tokens(failure.get("sqlstates"), r"[A-Z0-9]{5}"),
                    "frames": tokens(failure.get("frames"), FRAME),
                }
                for failure in row["failure"]
                if isinstance(failure, dict)
                and failure.get("phase") in ("setup", "call", "teardown")
            ]
        cases.append(case)
    if any(
        not any(
            case["nodeid"] == selector or (prefix and case["nodeid"].startswith(selector))
            for case in cases
        )
        for _, selector, prefix in rules
    ):
        raise EvidenceError("case_selector_not_observed")
    counts = Counter({status: 0 for status in STATUS})
    counts.update(case["status"] for case in cases)
    summary = data.get("summary")
    if not cases and summary == {} and data.get("receipt_error"):
        summary = {}
    elif summary == dict(counts) and all(type(value) is int for value in summary.values()):
        summary = dict(counts)
    else:
        raise EvidenceError("pytest_summary_mismatch")
    result = keep(data, "exit_code receipt_error") | digest_fields(data, "source", 64)
    result.update(summary=summary, cases=cases)
    result["selectors"] = tokens(data.get("selectors"), rf"tests|{TEST}(?:::{NAME})*")
    return result


def runtime_receipt(data, run):
    queue = re.escape(run) + r"\.(?:fixture|history|collection)"

    def queues(values):
        return tokens(values, queue)

    def proof_leaf(row):
        result = keep(row, "state observed_at valid_until evidence_kind reason_code")
        if isinstance(row, dict) and matches(queue, row.get("queue")):
            result["queue"] = row["queue"]
        return result

    def proof(row):
        result = proof_leaf(row)
        for key in ("queue_readiness", "last_consumption"):
            include(result, row, key, lambda sub: None if sub is None else proof_leaf(sub))
        return result

    def components(rows):
        return {
            key: proof(rows[key])
            for key in ("web", "pg", "redis", "worker", "beat")
            if isinstance(rows, dict) and key in rows
        }

    def coverage(row):
        result = keep(
            row,
            "E O V accepted not_received not_valid received_ratio valid_ratio attempted_cells latest_observed_at latest_valid_at observation_age_seconds",
        )
        include(result, row, "object_slots", lambda sub: keep(sub, "E O"))
        include(
            result,
            row,
            "by_indicator",
            lambda sub: {
                key: keep(sub[key], "E O V")
                for key in ("AQI", "PM2.5", "PM10", "SO2", "NO2", "CO", "O3")
                if isinstance(sub, dict) and key in sub
            },
        )
        return result

    def window(row):
        result = keep(
            row,
            "http window_id state result truncated visible_pending visible_sent_not_started visible_quota_wait visible_unconfirmed_dispatch oldest_visible_pending_age_seconds next_visible_quota_check_at visible_retry visible_running redis_ready_messages",
        )
        include(result, row, "coverage", coverage)
        return result

    def same_window(row):
        if not isinstance(row, dict):
            return {}
        result = keep(
            row,
            "window_id target_from target_until pg_expected_cells pg_attempted_cells run_coverage_matches",
        )
        include(result, row, "http", lambda sub: keep(sub, "run window health impact"))
        include(result, row, "coverage", coverage)
        health = row.get("health", {})
        if isinstance(health, dict):
            result["health"] = {}
            include(result["health"], health, "window", window)
            include(result["health"], health, "components", components)
        result["attempts"] = [
            keep(
                entry,
                "attempt status error_code dispatch request_state deliveries batch_id manifest_count fetched_at started_at finished_at next_retry_at",
            )
            for entry in object_rows(row.get("attempts"))
        ]
        result["impact_aqi_cells"] = [
            keep(entry, "observed_at indicator received accepted valid reason")
            for entry in object_rows(row.get("impact_aqi_cells"))
        ]
        return result

    checks = data.get("checks")
    if not isinstance(checks, dict):
        raise EvidenceError("invalid_runtime_checks")
    observed = keep(
        checks,
        "redis_unix_ping redis_version readiness_http unauthenticated_health_http csrf_failure_http beat_worker_pg_effect second_beat_rejected beat_dispatch_count worker_alive_after_beat_stop redis_failure_detected redis_down_read_readiness redis_restarted",
    )
    for key in (
        "initial_components",
        "worker_without_consumption",
        "worker_ready_after_subscription",
        "all_components",
        "beat_down",
        "beat_recovered",
        "worker_down_components",
        "worker_recovered",
        "redis_down_components",
        "redis_recovered",
        "postgres_recovered",
    ):
        include(observed, checks, key, components)
    include(observed, checks, "worker_queues", queues)
    include(observed, checks, "same_node_expected_queue", proof)
    if isinstance(wrong := checks.get("same_node_wrong_queue"), dict):
        observed["same_node_wrong_queue"] = keep(wrong, "redis_collection_messages")
        include(observed["same_node_wrong_queue"], wrong, "subscriptions", queues)
        include(observed["same_node_wrong_queue"], wrong, "worker", proof)
    for key, fields in {
        "worker_down": "http run_state queue_length",
        "worker_http_pg_effect": "http rows",
        "redis_down_http_observation": "http rows",
        "postgres_down": "readiness_http management_http management_code seconds",
        "collection_redelivery_response": "http code",
        "collection_redelivery": "injected_fault exhausted_actual_deliveries concurrent_sessions http broker_messages replay_messages original_run_deliveries worker_error",
    }.items():
        include(observed, checks, key, lambda row, fields=fields: keep(row, fields))
    redelivery = checks.get("collection_redelivery")
    callback_key = "scanner_preceded_committed_callback"
    if isinstance(redelivery, dict) and type(redelivery.get(callback_key)) is bool:
        observed["collection_redelivery"][callback_key] = redelivery[callback_key]
    quality = checks.get("V05_quality_detail")
    if isinstance(quality, dict):
        detail = {}
        if type(quality.get("loopback_fetches")) is int:
            detail["loopback_fetches"] = quality["loopback_fetches"]
        for group in ("allowed", "ledger_only"):
            row = quality.get(group)
            if not isinstance(row, dict):
                continue
            result = {
                key: row[key]
                for key in (
                    "items",
                    "receipts",
                    "retained_events_after_checks",
                    "structure_event_count",
                    "structure_batches",
                )
                if type(row.get(key)) is int
            }
            for key, allowed in (("legal_empty_status", "no_new"), ("structure_status", "paused")):
                if row.get(key) == allowed:
                    result[key] = allowed
            if type(row.get("conflict_remains_disputed")) is bool:
                result["conflict_remains_disputed"] = row["conflict_remains_disputed"]
            result["event_codes"] = tokens(
                row.get("event_codes"),
                r"concentration_peak|invalid_time|invalid_value|missing_key|missing_null|unordered_conflict",
            )
            detail[group] = result
        observed["V05_quality_detail"] = detail
    observed_wait = checks.get("window_wait")
    if isinstance(observed_wait, dict):
        observed["window_wait"] = {}
        for key in ("with_worker_stopped", "with_worker_restored"):
            include(observed["window_wait"], observed_wait, key, window)
    disposition = checks.get("worker_disposition")
    if isinstance(disposition, dict):
        child = keep(disposition, "hard_cutoff_denied after_cutoff_denied after_regrant_denied")
        for key, fields in {
            "before_restart": "cutoff_at file_present pending",
            "worker_open_reader": "pending error links",
            "after_injected_pg_rollback": "path_present pg_stage_state pg_job_completed",
            "after_worker_sweeps": "completed_at old_path_present old_pg_state independent_pg_state independent_readable idempotent",
        }.items():
            include(child, disposition, key, lambda row, fields=fields: keep(row, fields))
        observed["worker_disposition"] = child
    chain = checks.get("collection_failure_chain")
    if isinstance(chain, dict):
        child = {}
        for key, fields in {
            "resume_product": "http code",
            "worker_f_lineage": "status observations pre_manifest_issue pre_manifest_batches failed_error_code failed_attempt failure_window_reused upstream_http_requests",
            "cold_web_queries": "latest_http observed_at fetched_at fetched_at_basis age_seconds explicit_window_http explicit_window_state unauthorized_diagnostic_http unauthorized_city_http run_delta origin_online",
        }.items():
            include(child, chain, key, lambda row, fields=fields: keep(row, fields))
        include(child, chain, "same_window_diagnostics", same_window)
        observed["collection_failure_chain"] = child
    result = keep(data, "scope remaining_children failure") | {
        "result": read_status(data.get("result")),
        "checks": observed,
    }
    if isinstance(data.get("lifecycle"), list):
        result["lifecycle"] = [keep(row, "component started exit") for row in data["lifecycle"]]
    return result


def upgrade_receipt(data):
    return (
        keep(
            data,
            "type applied second_migration_pending legacy_session_http management_http quality_severity existing_grant_job_private_intent_preserved legacy_validation_allocation_preserved",
        )
        | digest_fields(data, "from", 40)
        | {"result": read_status(data.get("result"))}
    )


def build(root, runs, case_rules):
    result = {"format": "s06-evidence-index-v1", "runs": {}}
    with (
        directory(None, root) as repo,
        directory(repo, "tmp") as tmp,
        directory(tmp, "s06-evidence") as receipts,
    ):
        for run, labels in sorted(runs.items()):
            if not matches(RUN, run):
                raise EvidenceError("invalid_run_id")
            with directory(receipts, run) as path, directory(path, "V01") as v01:
                guard = read_receipt(v01, "result.json", run)
                action = guard.get("action")
                if not isinstance(action, str) or action not in ACTION_FILE:
                    raise EvidenceError("invalid_receipt_action")
                item = result["runs"][run] = {"labels": sorted(labels), "V01": guard_receipt(guard)}
                filename = ACTION_FILE[action]
                rules = case_rules.get(run, [])
                if rules and action != "pytest":
                    raise EvidenceError("case_mapping_requires_pytest")
                if filename is None:
                    continue
                raw = read_receipt(path, filename, run, optional=True)
                if raw is None:
                    if guard.get("result") == "PASS" or rules:
                        raise EvidenceError("missing_action_receipt")
                    item["action_receipt"] = "NOT RUN"
                elif action == "pytest":
                    item["pytest"] = pytest_receipt(raw, labels, rules)
                    if guard.get("source_before") != raw.get("source"):
                        raise EvidenceError("pytest_source_mismatch")
                elif action == "upgrade":
                    item["upgrade"] = upgrade_receipt(raw)
                else:
                    item["runtime"] = runtime_receipt(raw, run)
                if raw is not None and guard.get("result") == "PASS":
                    did_pass = (
                        raw.get("exit_code") == 0
                        and not raw.get("receipt_error")
                        and raw.get("summary", {}).get("FAIL") == 0
                        if action == "pytest"
                        else raw.get("result") == "PASS"
                    )
                    if not did_pass:
                        raise EvidenceError("action_result_mismatch")
    return result


def parse_labels(text):
    labels = set(text.split(","))
    if not all(matches(LABEL, label) for label in labels):
        raise EvidenceError("invalid_vp_label")
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__, fromfile_prefix_chars="@")
    parser.add_argument("--run", metavar="ID=Vxx,Pxx", required=True, action="append")
    parser.add_argument("--case", metavar="ID=Vxx,Pxx=NODE_OR_PREFIX*", action="append", default=[])
    args = parser.parse_args()
    try:
        runs, rules = {}, {}
        for option in args.run:
            run, separator, labels = option.partition("=")
            if not separator or run in runs or not matches(RUN, run):
                raise EvidenceError("invalid_or_duplicate_run")
            runs[run] = parse_labels(labels)
        for option in args.case:
            parts = option.split("=", 2)
            if len(parts) != 3:
                raise EvidenceError("invalid_case_mapping")
            run, labels, selector = parts
            if run not in runs or not matches(NODE, selector.removesuffix("*")):
                # A trailing wildcard can follow a partial function identifier.
                if run not in runs or not matches(rf"{TEST}::(?:{NAME}::)*{NAME}?\*", selector):
                    raise EvidenceError("invalid_case_mapping")
            rules.setdefault(run, []).append(
                (parse_labels(labels), selector.removesuffix("*"), selector.endswith("*"))
            )
        index = build(ROOT, runs, rules)
    except (EvidenceError, OSError) as exc:
        parser.error(str(exc) if isinstance(exc, EvidenceError) else "cannot_open_receipt")
    print(json.dumps(index, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
