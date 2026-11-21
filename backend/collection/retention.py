"""Erase withdrawn collection evidence under the same product lock as S02 disposition."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from observations.disposition import covers
from observations.models import Batch, QualityIssue, Receipt
from observations.policy import Denied, Scope

from .admission import LEDGER, selected_use
from .models import WorkWindow


def reconcile(product, grants, now):
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
            window.disposed = True
            window.expected = None
            window.roster_basis = ""
            window.result = "disposed"
            window.owner = None
            window.lease_until = None
            window.save(
                update_fields=[
                    "disposed",
                    "expected",
                    "roster_basis",
                    "result",
                    "owner",
                    "lease_until",
                ]
            )
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
            QualityIssue.objects.filter(
                batch_id__in=batches, city_key=None, station_key=None
            ).delete()
            empty = Batch.objects.filter(pk__in=batches, receipts__isnull=True)
            QualityIssue.objects.filter(batch__in=empty).update(batch=None)
            empty.delete()
