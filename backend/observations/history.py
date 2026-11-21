"""The Worker owns all staged bytes. Queue messages contain only attempt UUIDs."""

import io
import os
import stat
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from kombu.exceptions import OperationalError
from samples.normalization import Rejected

from .disposition import PACKAGE_SCOPE, clear_stage, private_dir, schedule
from .import_uci import MAX_ARCHIVE_BYTES, PROFILE, read_archive, validate_archive
from .models import ImportAttempt, Product, StagedInput
from .normalization import CONCENTRATIONS
from .policy import REQUIRED, Denied, Scope, actor, applicable, clock, locked_product
from .revisions import Candidate, ingest_batch
from .uci_catalog import NAMESPACE, STATIONS, WINDOWS


def source_product():
    pk = (
        Product.objects.filter(
            source__namespace=NAMESPACE, code="uci501", family="H", synthetic=False
        )
        .values_list("pk", flat=True)
        .first()
    )
    return locked_product(pk)


def check_import(product, actor_id):
    actor(actor_id, "import_history")
    now = clock()
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    for operation in ("acquire_file", "process_local", "retain_raw"):
        applicable(product, operation, PACKAGE_SCOPE, {"archive"}, now, grants)
    applicable(product, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"}, now, grants)
    for station in STATIONS:
        for start, end in WINDOWS:
            for offset in range((end - start).days):
                day = start + timedelta(days=offset)
                for indicator in CONCENTRATIONS:
                    scope = Scope("beijing", station, indicator, day, day + timedelta(days=1))
                    applicable(product, "retain_normalized", scope, REQUIRED, now, grants)
                    applicable(product, "process_local", scope, {"normalized"}, now, grants)


def stage_uci(actor_id, stream):
    with transaction.atomic():
        product = source_product()
        check_import(product, actor_id)
        private_dir()
        stage = StagedInput.objects.create(
            product=product, owner_id=actor_id, expires_at=clock() + timedelta(minutes=20)
        )
        schedule(product, stage.expires_at, "stage_expiry")
    try:
        with transaction.atomic():
            product = locked_product(product.pk)
            check_import(product, actor_id)
            stage.refresh_from_db()
            if stage.state != "preparing" or stage.expires_at <= clock():
                raise Denied("invalid_stage")
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
            validate_archive(io.BytesIO(data))
            check_import(product, actor_id)
            path = private_dir() / str(stage.pk)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            check_import(product, actor_id)
            stage.state = "ready"
            stage.save(update_fields=["state"])
    except Exception:
        cleanup(stage.pk)
        raise
    return stage.pk


def cleanup(stage_id):
    # Persist the obligation first. A failed unlink must be discoverable on recovery.
    with transaction.atomic():
        pk = StagedInput.objects.values_list("product_id", flat=True).get(pk=stage_id)
        product = locked_product(pk)
        stage = StagedInput.objects.get(pk=stage_id)
        if stage.state == "cleared":
            return
        stage.state = "cleanup_pending"
        stage.save(update_fields=["state"])
        job = schedule(product, clock(), "stage_cleanup")
    with transaction.atomic():
        locked_product(pk)
        clear_stage(stage)
        job.completed_at = clock()
        job.save(update_fields=["completed_at"])


def submit(actor_id, payload):

    with transaction.atomic(durable=True):
        actor(actor_id, "import_history")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"profile", "stage_id", "reason"}
            or payload["profile"] != PROFILE
            or payload["reason"] != "historical_verification"
        ):
            raise Denied("invalid_request")
        try:
            stage_id = UUID(str(payload["stage_id"]))
        except ValueError:
            raise Denied("invalid_request") from None
        product = source_product()
        check_import(product, actor_id)
        stage = StagedInput.objects.filter(
            pk=stage_id, product=product, owner_id=actor_id, state="ready", expires_at__gt=clock()
        ).first()
        if not stage:
            raise Denied()
        run = ImportAttempt.objects.create(
            product=product, stage=stage, actor_id=actor_id, reason=payload["reason"]
        )
        stage.state = "claimed"
        stage.save(update_fields=["state"])
    return publish(run)


def publish(run):
    from .tasks import process_history

    try:
        process_history.apply_async(args=[str(run.pk)], task_id=str(run.pk), retry=False)
        dispatch = "sent"
    except (OperationalError, TimeoutError):
        dispatch = "unconfirmed"
    ImportAttempt.objects.filter(pk=run.pk).update(dispatch=dispatch)
    return str(run.pk), dispatch


def redeliver(actor_id, attempt_id):
    with transaction.atomic(durable=True):
        product = source_product()
        check_import(product, actor_id)
        run = (
            ImportAttempt.objects.select_for_update()
            .filter(pk=attempt_id, product=product, actor_id=actor_id)
            .first()
        )
        if run is None:
            raise Denied()
        if run.state != "pending":
            return str(run.pk), run.state
        if run.stage.state != "claimed" or run.stage.expires_at <= clock():
            raise Denied("invalid_stage")
    return publish(run)


def inspect_input(stage):
    if stage.state != "claimed" or stage.expires_at <= clock():
        raise Denied("invalid_stage")
    fd = os.open(private_dir() / str(stage.pk), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or st.st_uid != os.geteuid()
            or st.st_mode & 0o077
        ):
            raise Denied("invalid_stage")
        return read_archive(stream, fetched_at=clock())


def consume(attempt_id):
    run = ImportAttempt.objects.only("product_id", "stage_id").get(pk=attempt_id)
    started = clock()
    try:
        with transaction.atomic():
            product = locked_product(run.product_id)
            run = ImportAttempt.objects.select_for_update().get(pk=attempt_id)
            run.deliveries += 1
            run.save(update_fields=["deliveries"])
            if run.state != "pending":
                return "redelivery"
            check_import(product, run.actor_id)
            selection = inspect_input(run.stage)
            check_import(product, run.actor_id)
        with transaction.atomic():
            product = locked_product(run.product_id)
            run = ImportAttempt.objects.select_for_update().get(pk=attempt_id)
            if run.state != "pending":
                return "redelivery"
            check_import(product, run.actor_id)
            if run.stage.state != "claimed" or run.stage.expires_at <= clock():
                raise Denied("invalid_stage")
            batch, created = ingest_batch(
                run.actor_id,
                product.pk,
                selection.digest,
                PROFILE,
                (Candidate(r) for r in selection.rows),
            )
            check_import(product, run.actor_id)
            run.batch = batch
            run.state = "succeeded" if created else "duplicate"
            run.started_at, run.finished_at = started, clock()
            run.save(update_fields=["batch", "state", "started_at", "finished_at"])
    except Exception as exc:
        expected = isinstance(exc, (Rejected, Denied))
        with transaction.atomic():
            locked_product(run.product_id)
            run = ImportAttempt.objects.select_for_update().get(pk=attempt_id)
            if run.state == "pending":
                run.state = "rejected" if expected else "failed"
                run.error_code = exc.code if expected else "processing_failed"
                run.started_at, run.finished_at = started, clock()
                run.deliveries = max(run.deliveries, 1)
                run.save(
                    update_fields=["state", "error_code", "started_at", "finished_at", "deliveries"]
                )
    finally:
        cleanup(run.stage_id)
    return run.state
