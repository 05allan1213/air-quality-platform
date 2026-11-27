"""Exercise the UCI input lifecycle with synthetic bytes in a private directory."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from queue import Queue
from threading import Event

import pytest
from django.db import transaction
from observations import history, import_uci, revisions
from observations.disposition import dispose_due
from observations.models import Batch, DispositionJob, ImportAttempt, Receipt, StagedInput
from observations.normalization import BatchContext, normalize
from observations.policy import Denied, clock, register_grant, revoke_grant
from observations.uci_catalog import register_uci
from s02_support import in_db_session, observe_db

pytestmark = pytest.mark.django_db(transaction=True)
PAYLOAD = b"synthetic input for historical generation regression"


@pytest.fixture
def source(django_user_model, settings, tmp_path, monkeypatch):
    user = django_user_model.objects.create_user("historical_operator", is_superuser=True)
    product = register_uci(user.pk)
    tmp_path.chmod(0o700)
    settings.S02_STAGE_DIR = str(tmp_path)

    def validate(stream):
        assert stream.read() == PAYLOAD
        return import_uci.SHA256

    def read(stream, *, fetched_at):
        validate(stream)
        return import_uci.Selection(import_uci.SHA256, (sample(fetched_at),), 1, 1, len(PAYLOAD))

    monkeypatch.setattr(history, "validate_archive", validate)
    monkeypatch.setattr(history, "read_archive", read)
    monkeypatch.setattr(history, "publish", lambda run: (str(run.pk), "unconfirmed"))
    return user, product, tmp_path


def sample(fetched_at):
    return normalize(
        import_uci.NORMALIZATION,
        {
            "object_code": "Changping",
            "indicator": "PM2.5",
            "source_time": "2013-03-01T00:00:00",
            "raw_unit": "µg/m³",
            "raw_value": "12",
            "row_locator": "synthetic:1",
        },
        BatchContext(fetched_at),
    )


def stage(user):
    return history.stage_uci(user.pk, BytesIO(PAYLOAD))


def submit(user, stage_id):
    attempt_id, _ = history.submit(
        user.pk,
        {"profile": import_uci.PROFILE, "stage_id": stage_id, "reason": "historical_verification"},
    )
    return ImportAttempt.objects.get(pk=attempt_id)


def restore_grant(user, product, grant):
    return register_grant(
        user.pk,
        product.pk,
        **{
            field: getattr(grant, field)
            for field in (
                "operation",
                "audience",
                "geography",
                "object_code",
                "indicator",
                "data_from",
                "data_until",
                "fields",
                "terms",
                "terms_version",
                "attribution",
            )
        },
    )


@pytest.mark.parametrize(
    "fields", [["archive"], ["batch_identity"], ["normalized", "provenance", "raw_field"]]
)
def test_old_stages_cannot_be_submitted_retried_or_deduplicated_after_loss(source, fields):
    user, product, _ = source
    grants = product.usegrant_set.filter(fields=fields)
    if "normalized" in fields:
        grants = grants.filter(
            operation="retain_normalized", object_code="Changping", data_from="2013-03-01"
        )
    elif "archive" in fields:
        grants = grants.filter(operation="retain_raw")
    grant = grants.get()
    ready = stage(user)
    claimed = stage(user)
    attempt = submit(user, claimed)

    revoke_grant(user.pk, grant.pk, "synthetic lifetime test")
    replacement = restore_grant(user, product, grant)
    boundary = DispositionJob.objects.get(boundary_grant=grant, reason="revocation")
    assert boundary.completed_at is None and replacement.pk not in boundary.boundary_basis
    with pytest.raises(Denied):
        submit(user, ready)
    with pytest.raises(Denied):
        history.redeliver(user.pk, attempt.pk)
    with pytest.raises(Denied):
        revisions.ingest_batch(
            user.pk,
            product.pk,
            import_uci.SHA256,
            import_uci.PROFILE,
            [revisions.Candidate(sample(clock()))],
            stage_id=claimed,
        )
    assert not Batch.objects.filter(product=product).exists()

    dispose_due(product.pk)
    boundary.refresh_from_db()
    assert boundary.completed_at is not None
    with pytest.raises(Denied):
        submit(user, ready)
    # Opening a fresh stage under the replacement grant can ingest and deduplicate.
    fresh = submit(user, stage(user))
    assert history.consume(fresh.pk) == "succeeded"
    duplicate = submit(user, stage(user))
    assert history.consume(duplicate.pk) == "duplicate"
    fresh.refresh_from_db()
    duplicate.refresh_from_db()
    assert fresh.batch_id == duplicate.batch_id
    assert Receipt.objects.filter(batch=fresh.batch).count() == 1


def test_overlapping_retention_preserves_stage_and_its_input(source):
    user, product, root = source
    grant = product.usegrant_set.get(fields=["batch_identity"])
    replacement = restore_grant(user, product, grant)
    stage_id = stage(user)
    revoke_grant(user.pk, grant.pk, "overlapping synthetic lifetime test")
    dispose_due(product.pk)
    boundary = DispositionJob.objects.get(boundary_grant=grant)
    assert boundary.completed_at is not None and replacement.pk in boundary.boundary_basis
    assert (root / str(stage_id)).is_file()
    attempt = submit(user, stage_id)
    assert history.consume(attempt.pk) == "succeeded"


def test_old_stage_registration_cannot_read_stream_after_disposition_and_regrant(
    source, monkeypatch
):
    user, product, root = source
    grant = product.usegrant_set.get(fields=["batch_identity"])
    registered = Event()
    proceed = Event()
    read = Event()
    actual = history.schedule

    class TracedUpload(BytesIO):
        def read(self, size=-1):
            read.set()
            return super().read(size)

    def pause_after_registration_commit(product, due_at, reason):
        job = actual(product, due_at, reason)
        if reason == "stage_expiry":

            def wait_before_read():
                registered.set()
                assert proceed.wait(20)

            transaction.on_commit(wait_before_read)
        return job

    monkeypatch.setattr(history, "schedule", pause_after_registration_commit)
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            in_db_session, pid_queue, lambda: history.stage_uci(user.pk, TracedUpload(PAYLOAD))
        )
        try:
            pid_queue.get(timeout=5)
            assert registered.wait(10)
            staged = StagedInput.objects.get(product=product, state="preparing")
            revoke_grant(user.pk, grant.pk, "synthetic upload lifetime test")
            dispose_due(product.pk)
            restore_grant(user, product, grant)
        finally:
            proceed.set()
        with pytest.raises(Denied):
            future.result(timeout=15)

    assert not read.is_set()
    staged.refresh_from_db()
    assert staged.state == "cleared" and not (root / str(staged.pk)).exists()


def test_parsed_rows_cannot_commit_after_disposition_and_regrant(source, monkeypatch):
    user, product, root = source
    grant = product.usegrant_set.get(fields=["batch_identity"])
    stage_id = stage(user)
    attempt = submit(user, stage_id)
    parsed = Event()
    proceed = Event()
    actual = history.read_archive

    def pause_after_parse_commit(stream, *, fetched_at):
        selection = actual(stream, fetched_at=fetched_at)

        def wait_between_transactions():
            parsed.set()
            assert proceed.wait(20)

        transaction.on_commit(wait_between_transactions)
        return selection

    monkeypatch.setattr(history, "read_archive", pause_after_parse_commit)
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(in_db_session, pid_queue, lambda: history.consume(attempt.pk))
        try:
            worker_pid = pid_queue.get(timeout=5)
            assert parsed.wait(10)
            with observe_db() as observer:
                record = observer.execute(
                    "SELECT state, deliveries, pg_backend_pid() "
                    "FROM observations_importattempt WHERE id = %s",
                    (attempt.pk,),
                ).fetchone()
                assert record[:2] == ("pending", 1) and record[2] != worker_pid
            revoke_grant(user.pk, grant.pk, "synthetic parsed-stage lifetime test")
            dispose_due(product.pk)
            restore_grant(user, product, grant)
            assert DispositionJob.objects.get(boundary_grant=grant).completed_at is not None
        finally:
            proceed.set()
        assert future.result(timeout=15) in {"rejected", "redelivery"}

    attempt.refresh_from_db()
    assert (attempt.state, attempt.batch_id) == ("rejected", None)
    assert StagedInput.objects.get(pk=stage_id).state == "cleared"
    assert not (root / str(stage_id)).exists()
    with observe_db() as observer:
        for relation in ("batch", "stationobservation", "stationrevision", "receipt"):
            assert observer.execute(f"SELECT count(*) FROM observations_{relation}").fetchone() == (
                0,
            )


def test_history_lookup_recovers_only_original_actor_stage_command(
    source, django_user_model, monkeypatch
):
    from django.test import Client
    from observations.disposition import clear_stage

    user, product, _ = source
    other = django_user_model.objects.create_superuser("other-history-reader")
    submissions = []

    def publish(attempt):
        submissions.append(attempt.pk)
        return str(attempt.pk), "unconfirmed"

    monkeypatch.setattr(history, "publish", publish)
    stages = [stage(user) for _ in range(2)]
    attempts = [submit(user, stage_id) for stage_id in stages]
    clear_stage(StagedInput.objects.get(pk=stages[0]))
    web = Client()
    web.force_login(user)
    url = "/api/v1/admin/command-receipts/lookup/"

    def lookup(target, **overrides):
        command = {
            "stage_id": target,
            "profile": import_uci.PROFILE,
            "reason": "historical_verification",
        }
        return web.post(
            url,
            {"family": "history_import", "target": target, "command": command | overrides},
            content_type="application/json",
        )

    for i in (0, 1, 0):
        result = lookup(stages[i])
        assert (result.status_code, result.json()) == (200, {"attempt_id": str(attempts[i].pk)})
    assert lookup(stages[0], stage_id=stages[1]).status_code == 404
    assert lookup("not-uuid").status_code == 400
    web.force_login(other)
    assert lookup(stages[1]).json() == {"code": "not_found"}
    web.force_login(user)
    grant = product.usegrant_set.get(operation="retain_normalized", fields=["batch_identity"])
    revoke_grant(user.pk, grant.pk, "fixture withdrawal")
    assert lookup(stages[0]).status_code == 404
    assert ImportAttempt.objects.count() == len(submissions) == 2
