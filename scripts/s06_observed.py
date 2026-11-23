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
