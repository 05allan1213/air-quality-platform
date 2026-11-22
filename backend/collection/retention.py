"""Erase withdrawn collection evidence under the same product lock as S02 disposition."""

from copy import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from observations.disposition import covers, lost_at
from observations.models import Batch, QualityIssue, Receipt
from observations.policy import Denied, Scope

from . import cnemc_jobs
from .admission import LEDGER, bounds, selected_use
from .models import Job, Run, WorkWindow
from .queries import catalogue_scopes


def dispose_jobs(ids, now):
    Job.objects.filter(pk__in=ids, disposed=False).update(
        disposed=True,
        enabled=False,
        next_due_at=None,
        expected_codes=None,
        selection={},
        standard={},
        backfill_policy={},
        backfill_cursor=None,
        roster_basis="",
        pause_reason="permission_lost",
        changed_at=now,
    )
    Run.objects.filter(job_id__in=ids, status__in=["running", "pending"]).update(
        status="paused",
        error_code="permission_lost",
        finished_at=now,
        dispatch_due=None,
        next_retry_at=None,
    )


def dispose_window(window, batches, now, *, selection=None):
    changes = dict(
        disposed=True,
        expected=None,
        roster_basis="",
        result="disposed",
        owner=None,
        lease_until=None,
    )
    if selection is not None:
        changes.update(selection={"mode": "include", "objects": selection}, standard={})
    WorkWindow.objects.filter(pk=window.pk).update(**changes)
    window.runs.update(
        manifest_count=None,
        result="",
        fetched_at=None,
        next_retry_at=None,
        dispatch_due=None,
    )
    window.runs.filter(status__in=["running", "pending"]).update(
        status="paused", error_code="permission_lost", finished_at=now
    )
    QualityIssue.objects.filter(batch_id__in=batches, city_key=None, station_key=None).delete()
    empty = Batch.objects.filter(pk__in=batches, receipts__isnull=True)
    QualityIssue.objects.filter(batch__in=empty).update(batch=None)
    empty.delete()


def configured_retention(job, start, end, now, grants):
    try:
        for fields in (LEDGER, cnemc_jobs.CATALOG):
            selected_use(job, start, end, "retain_normalized", fields, now, grants)
    except Denied:
        return False
    return True


def retire_validation_ledgers(product, grants, now):
    lost = [
        g.pk
        for g in product.usegrant_set.filter(
            max_total_requests__isnull=False, validation_disabled=False
        )
        if not covers(
            product,
            "retain_normalized",
            cnemc_jobs.validation_scope(g),
            {"validation_ledger"},
            now,
            grants,
        )
    ]
    if not lost:
        return
    # Source, products, jobs and windows are already locked by catalogue disposition.
    # Seal the allocation before removing the data used to count its requests.
    product.usegrant_set.filter(pk__in=lost).update(validation_disabled=True)
    used = Run.objects.filter(validation_grant_id__in=lost)
    for window_id, owner, generation in used.filter(status="running").values_list(
        "window_id", "owner", "generation"
    ):
        WorkWindow.objects.filter(pk=window_id, owner=owner, generation=generation).update(
            owner=None, lease_until=None
        )
    jobs = Job.objects.filter(validation_grant_id__in=lost)
    dispose_jobs(list(jobs.values_list("pk", flat=True)), now)
    jobs.update(validation_grant=None)
    erase_reservations(product, used, now)


def erase_reservations(product, used, now):
    if used.filter(reserved_at__isnull=False).exists():
        # A new grant can tighten a rolling period to the supported maximum (24 h).
        # This operational stop covers the erased reservations without retaining their times.
        source = product.source
        after = now + timedelta(days=1)
        source.collection_resume_after = max(after, source.collection_resume_after or after)
        source.collection_pause = "validation_ledger_disposed"
        source.save(update_fields=["collection_resume_after", "collection_pause"])
    used.update(
        validation_grant=None,
        reserved_at=None,
        occupied_until=None,
        request_state="disposed",
        next_retry_at=None,
        dispatch_due=None,
    )


def reconcile_cnemc(product, grants, now, *, lost_cities=(), lost_epochs=(), lost_identifiers=None):
    lost_identifiers = lost_identifiers or {}
    lost_entities = lost_cities if product.level == "city" else lost_epochs
    jobs = {
        job.pk: job
        for job in Job.objects.select_for_update(of=("self",))
        .filter(product=product)
        .select_related("city")
        .order_by("pk")
    }
    disposed = set()
    erase_windows = []
    # Reassess configurations with no historical Window too.
    target = now.replace(minute=0, second=0, microsecond=0)
    for job in jobs.values():
        job.product = product
        if job.disposed:
            continue
        start, end = bounds(job, target)
        configured = set(job.selection.get("objects", {})) | set(job.expected_codes or {})
        if (
            job.city_id in lost_cities
            or any(lost_at(lost_identifiers, code, target) for code in configured)
            or not configured_retention(job, start, end, now, grants)
        ):
            disposed.add(job.pk)

    for window in WorkWindow.objects.select_for_update().filter(product=product).order_by("pk"):
        associations = list(
            window.runs.order_by("created_at", "pk").values_list("job_id", "batch_id")
        )
        if not associations:
            continue
        batches = [batch for _, batch in associations if batch is not None]
        job = copy(jobs[associations[0][0]])
        job.selection = window.selection
        selected = window.selection.get("objects", {})
        lost = (
            window.disposed
            or job.city_id in lost_cities
            or not configured_retention(job, window.target_start, window.target_end, now, grants)
        )
        expected_entries = window.expected or {}
        items = list(window.items.prefetch_related("receipts"))
        entries = expected_entries | {
            i.item_key: (i.object_code, i.observed_at, i.indicator) for i in items
        }
        scopes = catalogue_scopes(product, entries)

        retained = set()
        for key, scope in scopes.items():
            _, at, _ = entries[key]
            if isinstance(at, str):
                at = datetime.fromisoformat(at)
            if (
                int(key.split("/", 1)[0]) not in lost_entities
                and not lost_at(lost_identifiers, scope.object_code, at)
                and covers(product, "retain_normalized", scope, LEDGER, now, grants)
            ):
                retained.add(key)
        lost = lost or len(retained) != len(entries)
        survivors = {}
        drop_items = []
        drop_receipts = []
        rewrite_receipts = []
        for item in items:
            receipts = list(item.receipts.all())
            if (
                item.item_key not in retained
                or item.accepted
                and not receipts
                or window.selection.get("mode") == "include"
                and item.indicator not in selected.get(item.object_code, ())
            ):
                lost = True
                drop_items.append(item.pk)
                drop_receipts.extend(r.pk for r in receipts)
                continue
            survivors.setdefault(item.object_code, set()).add(item.indicator)
            scope = scopes[item.item_key]
            for receipt in receipts:
                if receipt.collection_metadata:
                    metadata = {
                        key: value
                        for key, value in receipt.collection_metadata.items()
                        if covers(product, "retain_normalized", scope, {key}, now, grants)
                    }
                    if metadata != receipt.collection_metadata:
                        receipt.collection_metadata = metadata or None
                        rewrite_receipts.append(receipt)
        Receipt.objects.filter(pk__in=drop_receipts).delete()
        window.items.filter(pk__in=drop_items).delete()
        Receipt.objects.bulk_update(rewrite_receipts, ["collection_metadata"], batch_size=256)
        if lost:
            zone = ZoneInfo(product.source_timezone)
            ledger_scope = Scope(
                "*",
                "*",
                "*",
                window.target_start.astimezone(zone).date(),
                (window.target_end - timedelta(microseconds=1)).astimezone(zone).date()
                + timedelta(days=1),
            )
            if not covers(
                product, "retain_normalized", ledger_scope, {"request_ledger"}, now, grants
            ):
                erase_windows.append(window.pk)
            disposed.update(jid for jid, _ in associations)
            dispose_window(
                window,
                batches,
                now,
                selection={code: sorted(inds) for code, inds in survivors.items()},
            )

    dispose_jobs(disposed, now)
    ordinary = Run.objects.filter(window_id__in=erase_windows, validation_grant=None).exclude(
        request_state="disposed"
    )
    erase_reservations(product, ordinary, now)
    retire_validation_ledgers(product, grants, now)


def reconcile(product, grants, now, **directory_loss):
    if product.contract_version == cnemc_jobs.cnemc.VERSION:
        reconcile_cnemc(product, grants, now, **directory_loss)
        return
    for window in WorkWindow.objects.filter(product=product).iterator():
        run = (
            window.runs.select_related("job__city", "job__product__source")
            .order_by("created_at")
            .first()
        )
        if run is None:
            continue
        job = run.job
        try:
            selected_use(
                job,
                window.target_start,
                window.target_end,
                "retain_normalized",
                LEDGER,
                now,
                grants,
            )
            lost = False
        except Denied:
            lost = True
        batches = list(window.runs.exclude(batch=None).values_list("batch_id", flat=True))
        for item in window.items.all().iterator():
            from observations.catalog import resolve

            day = item.observed_at.astimezone(ZoneInfo(product.source_timezone)).date()
            try:
                _, geo = resolve(product, item.object_code, item.observed_at)
            except Denied:
                geo = "*"
            scope = Scope(geo, item.object_code, item.indicator, day, day + timedelta(days=1))
            if not covers(product, "retain_normalized", scope, LEDGER, now, grants) or (
                item.accepted and not item.receipts.exists()
            ):
                lost = True
                item.receipts.all().delete()
                item.delete()
                continue
            for receipt in item.receipts.exclude(collection_metadata=None):
                metadata = {
                    k: v
                    for k, v in receipt.collection_metadata.items()
                    if covers(product, "retain_normalized", scope, {k}, now, grants)
                }
                if metadata != receipt.collection_metadata:
                    Receipt.objects.filter(pk=receipt.pk).update(
                        collection_metadata=metadata or None
                    )
        if lost:
            # Do not reinterpret a withdrawn footprint as an unobserved historical gap.
            dispose_window(window, batches, now)
