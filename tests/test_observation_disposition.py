from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from observations.disposition import dispose_due
from observations.models import (
    Batch,
    Change,
    DispositionJob,
    QualityIssue,
    StagedInput,
    StationObservation,
    StationRevision,
)
from observations.policy import Denied, revoke_grant
from s02_support import START, allow, candidate, create_product, create_site
from s02_support import ingest_fixture as ingest_batch

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def disposal_case(django_user_model):
    actor = django_user_model.objects.create_user("s02-disposer", is_staff=True, is_superuser=True)
    product = create_product()
    a = create_site(product, "001", geography="A")
    b = create_site(product, "002", geography="B")
    allow(actor, product, "process_local")
    allow(actor, product, "display")
    return actor, product, a, b


def ingest(actor, product, batch_id, *readings):
    return ingest_batch(actor.pk, product.pk, batch_id, "f-retention-v1", readings)[0]


def test_mixed_batch_disposition_preserves_other_fields_and_independent_scope(disposal_case):
    actor, product, a, b = disposal_case
    primary = allow(actor, product, geography="A")
    allow(actor, product, geography="A", indicator="CO", fields=["normalized", "provenance"])
    allow(actor, product, geography="B")
    initial = ingest(
        actor,
        product,
        "mixed-reconstructible-input",
        candidate(product, 1, "10"),
        candidate(product, 1, "20", indicator="PM10", unit="µg/m³"),
        candidate(product, 1, "30", code="002"),
    )
    ingest(
        actor, product, "a-pm10-newer", candidate(product, 2, "21", indicator="PM10", unit="µg/m³")
    )
    ingest(
        actor,
        product,
        "a-pm10-conflict",
        candidate(product, 2, "22", indicator="PM10", unit="µg/m³"),
    )
    retained_revision_batch = ingest(actor, product, "a-co-revision", candidate(product, 2, "11"))
    station_loss = StationObservation.objects.get(site_epoch=a, indicator="PM10")
    assert station_loss.revisions.count() == 3
    assert station_loss.revisions.filter(candidate=True).count() == 1
    assert QualityIssue.objects.filter(
        station_key=station_loss, code="same_version_conflict"
    ).exists()
    assert Change.objects.filter(station_key=station_loss).exists()
    kept_a = StationObservation.objects.get(site_epoch=a, indicator="CO")
    kept_b = StationObservation.objects.get(site_epoch=b, indicator="CO")
    assert list(kept_a.revisions.order_by("revision_no").values_list("raw_value", flat=True)) == [
        "10",
        "11",
    ]
    assert initial.receipts.count() == 3

    revoke_grant(actor.pk, primary.pk, "withdraw A rights except independent CO")
    deleted_key, deleted_revs = (
        station_loss.pk,
        list(station_loss.revisions.values_list("id", flat=True)),
    )
    dispose_due(product.pk)
    assert not StationObservation.objects.filter(pk=deleted_key).exists()
    assert not StationRevision.objects.filter(pk__in=deleted_revs).exists()
    assert not QualityIssue.objects.filter(station_key_id=deleted_key).exists()
    assert not Change.objects.filter(station_key_id=deleted_key).exists()
    initial.refresh_from_db()
    assert initial.redacted and initial.identity is None
    kept = {
        receipt.station_revision.key_id: receipt
        for receipt in initial.receipts.select_related("station_revision")
    }
    assert set(kept) == {kept_a.pk, kept_b.pk}
    assert kept[kept_a.pk].locator is None
    assert kept[kept_b.pk].locator
    assert all(
        (rev.raw_value, rev.raw_unit) == (None, None) and rev.provenance
        for rev in kept_a.revisions.all()
    )
    assert kept_a.revisions.get(is_current=True).value == 11
    assert kept_b.revisions.get(is_current=True).raw_value == "30"
    assert list(
        Batch.objects.filter(product=product).values_list("pk", "identity").order_by("pk")
    ) == [(initial.pk, None), (retained_revision_batch.pk, None)]
    assert not DispositionJob.objects.filter(product=product, completed_at=None).exists()
    completed = list(DispositionJob.objects.values())
    dispose_due(product.pk)
    assert list(DispositionJob.objects.values()) == completed


def test_reconciles_due_work_even_when_one_off_schedule_is_missing(
    disposal_case, settings, tmp_path
):
    actor, product, a, _ = disposal_case
    deadline = timezone.now() + timedelta(hours=1)
    policy = allow(actor, product, geography="A", valid_until=deadline, expiry_mode="recoverable")
    ingest(actor, product, "short-lived F batch", candidate(product, 1))
    assert StationObservation.objects.filter(site_epoch=a).exists()
    settings.S02_STAGE_DIR = str(tmp_path)
    tmp_path.chmod(0o700)
    now = timezone.now()
    stage = StagedInput.objects.create(
        product=product, owner=actor, expires_at=now - timedelta(seconds=1), state="available"
    )
    stage_path = tmp_path / str(stage.pk)
    stage_path.mkdir()
    job = DispositionJob.objects.get(product=product, completed_at=None)
    assert PeriodicTask.objects.filter(name=f"s02.due.{job.pk}", one_off=True).exists()
    PeriodicTask.objects.filter(name=f"s02.due.{job.pk}").delete()
    # Move the registered boundary into the past without sleeping or mocking the database clock.
    type(policy).objects.filter(pk=policy.pk).update(valid_until=now)
    DispositionJob.objects.filter(pk=job.pk).update(due_at=now)
    with pytest.raises(Denied, match="use_denied"):
        ingest(actor, product, "new after deadline", candidate(product, 2))

    with pytest.raises(Denied, match="cleanup_failed"):
        dispose_due(product.pk)
    job.refresh_from_db()
    stage.refresh_from_db()
    assert job.completed_at is None and job.error_code == "cleanup_failed"
    assert stage.state != "cleared" and stage_path.is_dir()
    assert StationObservation.objects.filter(site_epoch=a).exists()
    stage_path.rmdir()
    stage_path.write_bytes(b"self-authored F controlled bytes")
    dispose_due(product.pk)
    job.refresh_from_db()
    stage.refresh_from_db()
    assert job.completed_at and job.error_code == ""
    assert stage.state == "cleared" and not stage_path.exists()
    assert not StationObservation.objects.filter(site_epoch=a).exists()
    assert not Batch.objects.filter(product=product).exists()
    finished = job.completed_at
    dispose_due(product.pk)
    job.refresh_from_db()
    assert job.completed_at == finished


def test_hard_erasure_deadline_is_rejected_at_persistence_boundary(disposal_case):
    actor, product, _, _ = disposal_case
    future = timezone.now() + timedelta(days=7)
    allow(actor, product, geography="A", retain_until=future)
    allow(actor, product, geography="A", valid_until=future, expiry_mode="hard")
    with pytest.raises(Denied, match="use_denied"):
        ingest(actor, product, "unmaintainable-deadline", candidate(product, 1, at=START))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM observations_stationobservation WHERE product_id=%s",
            (product.pk,),
        )
        assert cursor.fetchone() == (0,)
    assert not Batch.objects.filter(product=product).exists()


def test_distinct_natural_expiry_boundaries_each_advance_the_change_cursor(disposal_case):
    actor, product, a, b = disposal_case
    deadline = timezone.now() + timedelta(hours=1)
    expires_a = allow(
        actor, product, geography="A", valid_until=deadline, expiry_mode="recoverable"
    )
    expires_b = allow(
        actor,
        product,
        geography="B",
        valid_until=deadline + timedelta(hours=1),
        expiry_mode="recoverable",
    )
    ingest(
        actor, product, "expiring-scopes", candidate(product, 1), candidate(product, 1, code="002")
    )
    for policy, dropping, surviving in [(expires_a, a, b), (expires_b, b, None)]:
        job = DispositionJob.objects.get(product=product, due_at=policy.valid_until)
        before = Change.objects.filter(product=product).latest("pk").pk
        now = timezone.now()
        type(policy).objects.filter(pk=policy.pk).update(valid_until=now)
        DispositionJob.objects.filter(pk=job.pk).update(due_at=now)
        dispose_due(product.pk)
        job.refresh_from_db()
        assert job.completed_at
        assert not StationObservation.objects.filter(site_epoch=dropping).exists()
        if surviving:
            assert StationObservation.objects.filter(site_epoch=surviving).exists()
        assert Change.objects.filter(product=product, kind="disposition", pk__gt=before).exists()
