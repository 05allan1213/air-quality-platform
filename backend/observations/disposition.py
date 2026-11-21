"""Durable due work. The filesystem is reconciled before a job can become complete."""

import json
import os
import stat
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django_celery_beat.models import ClockedSchedule, IntervalSchedule, PeriodicTask

from .models import (
    Batch,
    Change,
    CityObservation,
    DispositionJob,
    ImportAttempt,
    QualityIssue,
    Receipt,
    StagedInput,
    StationObservation,
)
from .policy import RAW, REQUIRED, Denied, Scope, applicable, clock, locked_product
from .revisions import key_scope, tables

PACKAGE_SCOPE = Scope("beijing", "*", "*", date(2013, 3, 1), date(2017, 3, 1))


def schedule(product, due_at, reason):
    job = DispositionJob.objects.create(product=product, due_at=due_at, reason=reason)
    clocked, _ = ClockedSchedule.objects.get_or_create(clocked_time=due_at)
    PeriodicTask.objects.create(
        name=f"s02.due.{job.pk}",
        task="observations.dispose",
        queue="s02.history",
        clocked=clocked,
        one_off=True,
        args=json.dumps([product.pk]),
    )
    interval, _ = IntervalSchedule.objects.get_or_create(every=30, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.update_or_create(
        name="s02.reconcile",
        defaults=dict(
            interval=interval, task="observations.dispose", queue="s02.history", enabled=True
        ),
    )
    return job


def private_dir():
    path = Path(settings.S02_STAGE_DIR)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise OSError("stage_directory_unavailable")
    # In the deployed Worker this must be its own bounded tmpfs, never the image layer.
    if str(path) == "/run/s02-history" and not os.path.ismount(path):
        raise OSError("stage_mount_unavailable")
    return path


def clear_stage(stage):
    path = private_dir() / str(stage.pk)
    path.unlink(missing_ok=True)
    try:
        path.lstat()
    except FileNotFoundError:
        stage.state = "cleared"
        stage.save(update_fields=["state"])
        return
    raise OSError("stage_still_present")


def covers(product, op, scope, fields, now, grants):
    try:
        applicable(product, op, scope, fields, now, grants)
        return True
    except Denied:
        return False


def reconcile_data(product, grants, now):
    key_model = CityObservation if product.level == "city" else StationObservation
    keys = key_model.objects.filter(product=product).select_related(
        "city" if product.level == "city" else "site_epoch__city"
    )
    drop = []
    strip = []
    for key in keys.iterator():
        scope = key_scope(key)
        if not covers(product, "retain_normalized", scope, REQUIRED, now, grants):
            drop.append(key.pk)
        elif not covers(product, "retain_normalized", scope, RAW, now, grants):
            strip.append(key.pk)
    _, rev, _, _, rf = tables(product.level)
    receipts = Receipt.objects.filter(batch__product=product, **{rf + "__key_id__in": drop + strip})
    # A batch digest describes the WHOLE material. It must not survive a partial purge.
    affected = Batch.objects.filter(pk__in=receipts.values("batch_id"))
    affected.update(identity=None, redacted=True)
    if product.family == "H" and not covers(
        product, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"}, now, grants
    ):
        Batch.objects.filter(product=product).update(identity=None, redacted=True)
    receipts.update(locator=None)
    rev.objects.filter(key_id__in=strip).update(raw_value=None, raw_unit=None)
    key_model.objects.filter(pk__in=drop).delete()
    # A batch can have authorized structural issues and no observation rows.
    for issue in QualityIssue.objects.filter(product=product, city_key=None, station_key=None):
        if not covers(
            product, "retain_normalized", Scope.from_issue(issue.scope), REQUIRED, now, grants
        ):
            Batch.objects.filter(pk=issue.batch_id).update(identity=None, redacted=True)
            issue.delete()
    from collection.retention import reconcile

    reconcile(product, grants, now)
    Batch.objects.filter(product=product, receipts__isnull=True, qualityissue__isnull=True).delete()


@transaction.atomic
def reconcile_product(product_id):
    product = locked_product(product_id)
    now = clock()
    jobs = DispositionJob.objects.filter(product=product, completed_at=None, due_at__lte=now)
    if not jobs.exists():
        return
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    if jobs.exclude(reason__in=["stage_cleanup", "stage_expiry"]).exists():
        reconcile_data(product, grants, now)
        product.rights_version += 1
        product.save(update_fields=["rights_version"])
        Change.objects.create(
            product=product, kind="disposition", rights_version=product.rights_version
        )
    for stage in StagedInput.objects.filter(product=product).exclude(state="cleared"):
        if (
            stage.expires_at <= now
            or stage.state == "cleanup_pending"
            or not covers(product, "retain_raw", PACKAGE_SCOPE, {"archive"}, now, grants)
        ):
            clear_stage(stage)
            ImportAttempt.objects.filter(stage=stage, state="pending").update(
                state="rejected", error_code="invalid_stage", finished_at=now
            )
    jobs.update(completed_at=clock(), error_code="")


def dispose_due(product_id=None):
    due = DispositionJob.objects.filter(completed_at=None, due_at__lte=clock())
    if product_id is not None:
        due = due.filter(product_id=product_id)
    failed = False
    for pk in list(due.order_by("product_id").values_list("product_id", flat=True).distinct()):
        try:
            reconcile_product(pk)
        except OSError:
            due.filter(product_id=pk).update(error_code="cleanup_failed")
            failed = True
    if failed:
        raise Denied("cleanup_failed")
