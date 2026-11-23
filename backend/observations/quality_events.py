"""Quality event metadata and versioning shared by ingestion and review."""

from django.db.models import F, Q

from .models import CandidateClearance, CityObservation, QualityIssue

SOURCE_CONFLICTS = frozenset({"same_version_conflict", "unordered_conflict"})
MAPPING_CONFLICTS = frozenset({"mapping_conflict", "mapping_review_required"})
QUALITY_DETAIL = frozenset({"quality_detail"})


def collection_issue(event):
    # Historical keyed events may only be attached through their collection batch.
    return event.run_id is not None or (
        event.batch_id is not None and hasattr(event.batch, "collection_run")
    )


def check_detail(product, scope, now, grants=None, *, review=False):
    from collection.admission import LEDGER

    from .policy import applicable

    for operation in ("retain_normalized", "review") if review else ("retain_normalized",):
        applicable(product, operation, scope, LEDGER, now, grants)
        applicable(product, operation, scope, QUALITY_DETAIL, now, grants)


def severity(code):
    if code in SOURCE_CONFLICTS | MAPPING_CONFLICTS | {
        "structure_drift",
        "invalid_structure",
        "invalid_payload",
        "identity_window_crossing",
        "identity_unresolved",
    }:
        return "blocking"
    if code in {"late", "duplicate", "outside_target", "outside_roster"}:
        return "info"
    return "warning"


def keyed_issues(key):
    fk = "city_key" if isinstance(key, CityObservation) else "station_key"
    return QualityIssue.objects.filter(product_id=key.product_id, **{fk: key})


def active_candidates(key):
    fk = "city_revision" if isinstance(key, CityObservation) else "station_revision"
    cleared = CandidateClearance.objects.filter(**{f"{fk}__key": key})
    return key.revisions.filter(candidate=True).exclude(pk__in=cleared.values(f"{fk}_id"))


def advance_key_events(key):
    # Retained closed events must also reject stale snapshots and replay with new evidence.
    keyed_issues(key).update(event_version=F("event_version") + 1)


def add_key_issue(key, code, *, batch=None, receipt=None, rule=None):
    fk = "city_key" if isinstance(key, CityObservation) else "station_key"
    issue, _ = QualityIssue.objects.get_or_create(
        product_id=key.product_id,
        **{fk: key},
        code=code,
        resolved_at__isnull=True,
        defaults={
            "batch": batch,
            "receipt": receipt,
            "rule_version": rule,
            "severity": severity(code),
        },
    )
    return issue


def record_run_issue(run, code, scope, *, item=None, batch=None, receipt=None, rule=None):
    """Link only ledger objects from the same actual Run and product."""
    from .policy import Denied, clock

    product = run.job.product
    if (
        run.window.product_id != product.pk
        or (item is not None and (item.run_id != run.pk or item.window_id != run.window_id))
        or (batch is not None and (run.batch_id != batch.pk or batch.product_id != product.pk))
        or (
            receipt is not None
            and (
                item is None
                or batch is None
                or receipt.collection_item_id != item.pk
                or receipt.batch_id != batch.pk
            )
        )
    ):
        raise Denied("invalid_issue_lineage")
    try:
        check_detail(product, scope, clock())
    except Denied:
        return None
    row, _ = QualityIssue.objects.get_or_create(
        run=run,
        run_item=item,
        code=code,
        defaults={
            "product": product,
            "scope": scope.serialize(),
            "batch": batch,
            "receipt": receipt,
            "rule_version": rule,
            "severity": severity(code),
        },
    )
    return row


def issue_linkage(event):
    """Fail closed for new cross-table links if an old or inconsistent row is encountered."""
    from .policy import Denied

    if event.batch_id and event.batch.product_id != event.product_id:
        raise Denied("use_denied")
    if event.run_id and (
        event.run.job.product_id != event.product_id
        or event.run.window.product_id != event.product_id
        or (event.batch_id and event.run.batch_id != event.batch_id)
    ):
        raise Denied("use_denied")
    if event.run_item_id and (
        event.run_id != event.run_item.run_id or event.run.window_id != event.run_item.window_id
    ):
        raise Denied("use_denied")
    if event.receipt_id:
        revision = event.receipt.city_revision or event.receipt.station_revision
        expected_key = event.city_key_id if event.receipt.city_revision_id else event.station_key_id
        if (
            event.receipt.batch_id != event.batch_id
            or revision.key.product_id != event.product_id
            or (
                event.run_item_id is not None
                and event.receipt.collection_item_id != event.run_item_id
            )
            or (expected_key and revision.key_id != expected_key)
            or (event.city_key_id and not event.receipt.city_revision_id)
            or (event.station_key_id and not event.receipt.station_revision_id)
        ):
            raise Denied("use_denied")


def clear_event_state(key, codes, now):
    keyed_issues(key).filter(code__in=codes).filter(
        Q(resolved_at__isnull=True) | ~Q(status="resolved"),
    ).update(status="resolved", resolved_at=now)
