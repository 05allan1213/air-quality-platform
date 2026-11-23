"""Whitelist observed S06 case facts for both machine receipts and their public index."""

import re

OBSERVATION_ENUMS = {
    "condition": {"C01", "C02", "C03", "C04", "V06"},
    "order": {
        "withdrawal",
        "publication",
        "restore",
        "regrant",
        "sweep",
        "first_resume",
        "candidate",
        "governance",
        "revoke",
        "adjudicate",
        "revision",
        "download",
        "failed_commit",
        "cutoff",
        "before_expiry",
        "during_insert",
        "after_registration",
        "constraints_checked",
    },
    "lock_target": {"source", "source_and_product", "product", "account"},
    "control_target": {"source", "product"},
    "segment_state": {"blocked", "removed"},
    "saved_state": {"current"},
}
OBSERVATION_COUNTS = frozenset(
    """
    csv_bytes peer_csv_bytes replay_csv_bytes recovery_rows target_rows_after current_keys current_revisions
    watermark_before watermark_same_value watermark_late receipts_before receipts_same_value
    receipts_late rights_version control_version_before control_version_after
    event_version_before event_version_after quality_clearances success_audit_count
    old_revisions_after sibling_rows_after copy_files_after
""".split()
)
OBSERVATION_HTTP = frozenset(
    """
    http peer_http replay_http sibling_http details_http restore_http stale_http
""".split()
)
OBSERVATION_FLAGS = frozenset(
    """
    pg_lock_wait same_content input_changed_on_same_value input_unchanged_on_late
    receipt_late dispute retained_old_revision old_eligible_at_cutoff
""".split()
)
OBSERVATION_UTC = frozenset({"pg_utc", "cutoff_utc", "registered_utc"})


def safe_observed(raw):
    if not isinstance(raw, dict):
        return {}
    result = {
        key: raw[key]
        for key, choices in OBSERVATION_ENUMS.items()
        if isinstance(raw.get(key), str) and raw[key] in choices
    }
    for keys, minimum, maximum in (
        (OBSERVATION_COUNTS, 0, 2**63 - 1),
        (OBSERVATION_HTTP, 100, 599),
    ):
        result.update(
            (key, raw[key])
            for key in keys
            if type(raw.get(key)) is int and minimum <= raw[key] <= maximum
        )
    result.update((key, raw[key]) for key in OBSERVATION_FLAGS if type(raw.get(key)) is bool)
    result.update(
        (key, raw[key])
        for key in OBSERVATION_UTC
        if isinstance(raw.get(key), str)
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?\+00:00", raw[key]
        )
    )
    return result


CSV_PHASES = {"input_version", "hidden_version", "prepare", "stale", "download", "candidate_limit"}
CSV_ERRORS = {
    "query_too_large",
    "input_changed",
    "method_changed",
    "expired",
    "use_denied",
    "permission_denied",
    "not_authenticated",
    "not_found",
    "rate_limited",
    "database_unavailable",
    "invalid_request",
    "csrf_failed",
    "parse_error",
}
CSV_TRANSPORT_ERRORS = {
    "ReadTimeout",
    "WriteTimeout",
    "ConnectTimeout",
    "PoolTimeout",
    "ConnectError",
    "ReadError",
    "WriteError",
    "CloseError",
    "RemoteProtocolError",
    "LocalProtocolError",
    "ProxyError",
    "UnsupportedProtocol",
}


def bounded_metrics(raw, fields, limit):
    return {
        name: raw[name]
        for name in fields.split()
        if type(raw.get(name)) in (int, float) and 0 <= raw[name] <= limit
    }


def safe_csv_probe(raw):
    """Describe a request made by the test without logging bodies, SQL, or ticket IDs."""
    if not isinstance(raw, dict) or not isinstance(phase := raw.get("phase"), str):
        return {}
    if phase not in CSV_PHASES or raw.get("authorized_rows") not in (9999, 10000, 10001):
        return {}
    result = {"phase": phase, "authorized_rows": raw["authorized_rows"]}
    result.update(
        bounded_metrics(
            raw,
            "correlation http_ms sql_count sql_ms max_statement_ms response_bytes "
            "csv_bytes load_1m test_rss_kib",
            2**40,
        )
    )
    for name, values in (("code", CSV_ERRORS), ("exception", CSV_TRANSPORT_ERRORS)):
        if isinstance(value := raw.get(name), str) and value in values:
            result[name] = value
    if type(raw.get("status")) is int and 100 <= raw["status"] <= 599:
        result["status"] = raw["status"]
    if type(raw.get("response_started")) is bool:
        result["response_started"] = raw["response_started"]
    if isinstance(states := raw.get("sqlstates"), list):
        result["sqlstates"] = sorted(
            {
                state
                for state in states
                if isinstance(state, str) and re.fullmatch("[0-9A-Z]{5}", state)
            }
        )
    return result


def safe_csv_environment(raw):
    if not isinstance(raw, dict):
        return {}
    result = bounded_metrics(
        raw,
        "logical_cpus allowed_cpus memory_total_kib memory_available_kib "
        "transaction_budget_ms statement_budget_ms lock_budget_ms http_client_timeout_ms",
        2**40,
    )
    for name, pattern in (
        ("architecture", r"x86_64|aarch64|arm64"),
        ("python", r"[0-9]+\.[0-9]+\.[0-9]+"),
        ("postgres", r"[0-9]+\.[0-9]+(?:\.[0-9]+)?"),
        ("shared_buffers", r"[0-9]+(?:kB|MB|GB|TB)"),
        ("work_mem", r"[0-9]+(?:kB|MB|GB|TB)"),
    ):
        if isinstance(value := raw.get(name), str) and re.fullmatch(pattern, value):
            result[name] = value
    return result
