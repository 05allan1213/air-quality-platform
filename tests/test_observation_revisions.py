from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from queue import Queue

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from observations.catalog import relocate
from observations.models import (
    Batch,
    Change,
    Product,
    QualityIssue,
    Receipt,
    StationIdentifier,
    StationObservation,
    StationRevision,
)
from observations.policy import Denied, revoke_grant
from observations.revisions import correct_mapping
from s02_support import (
    START,
    allow,
    candidate,
    create_product,
    create_site,
    in_db_session,
    observe_db,
    reject_observation_write,
    sql_copy,
    wait_for_block,
)
from s02_support import ingest_fixture as ingest_batch

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def station_case(django_user_model):
    actor = django_user_model.objects.create_user("s02-ingestor", is_staff=True, is_superuser=True)
    product = create_product()
    epoch = create_site(product)
    allow(actor, product, "process_local")
    permission = allow(actor, product)
    return actor, product, epoch, permission


def ingest(actor, product, identity, *candidates):
    return ingest_batch(actor.pk, product.pk, identity, "f-test-v1", candidates)


def committed_current(product):
    with observe_db() as observer:
        return observer.execute(
            """
            SELECT k.id, k.watermark, k.disputed, k.qualification_version, v.value,
                   v.revision_no, v.id
            FROM observations_stationobservation k
            JOIN observations_stationrevision v ON k.id=v.key_id AND v.is_current
            WHERE k.product_id=%s ORDER BY k.id
            """,
            (product.pk,),
        ).fetchall()


def test_t06_watermark_advances_without_content_change(station_case):
    actor, product, epoch, _ = station_case
    first, _ = ingest(actor, product, "v1", candidate(product, 1, "10"))
    original = committed_current(product)
    ((key, watermark, disputed, qualification, value, revision_number, revision_pk),) = original
    assert (watermark, disputed, value, revision_number) == (1, False, Decimal("10"), 1)
    assert StationObservation.objects.get(pk=key).site_epoch_id == epoch.pk
    assert first.receipts.get().outcome == "first"
    changes = list(Change.objects.filter(station_key_id=key).values_list("id", flat=True))

    same, _ = ingest(actor, product, "v3", candidate(product, 3, "10.0000"))
    assert committed_current(product) == [(key, 3, False, qualification, value, 1, revision_pk)]
    assert same.receipts.get().outcome == "watermark_advanced"
    assert same.receipts.get().source_token == 3
    assert same.receipts.get().station_revision_id == revision_pk
    assert list(Change.objects.filter(station_key_id=key).values_list("id", flat=True)) == changes

    late_candidate = candidate(product, 2, "20")
    late, created = ingest(actor, product, "v2", late_candidate)
    assert created
    assert late.receipts.get().outcome == "late"
    assert late.receipts.get().source_token == 2
    repeated, created = ingest(actor, product, "v2", late_candidate)
    assert (repeated.pk, created) == (late.pk, False)
    equal, _ = ingest(actor, product, "v3-replay", candidate(product, 3, "10.0"))
    assert equal.receipts.get().outcome == "duplicate"
    old_same, _ = ingest(actor, product, "v1-replay", candidate(product, 1, "10.0"))
    assert old_same.receipts.get().outcome == "late"
    assert committed_current(product) == [(key, 3, False, qualification, value, 1, revision_pk)]
    assert StationObservation.objects.count() == StationRevision.objects.count() == 1
    assert QualityIssue.objects.filter(station_key_id=key).exists()


def test_newer_content_keeps_history_and_missing_does_not_fall_back(station_case):
    actor, product, _, _ = station_case
    ingest(actor, product, "v2", candidate(product, 2, "10"))
    newer, _ = ingest(actor, product, "v10", candidate(product, 10, "20"))
    assert newer.receipts.get().outcome == "content_changed"
    missing, _ = ingest(actor, product, "v11", candidate(product, 11, "NA"))
    assert missing.receipts.get().outcome == "content_changed"
    key = StationObservation.objects.get()
    assert key.watermark == 11
    assert list(
        key.revisions.order_by("revision_no").values_list("value", "quality", "is_current")
    ) == [
        (Decimal("10"), "valid", False),
        (Decimal("20"), "valid", False),
        (None, "missing", True),
    ]
    assert Change.objects.filter(station_key=key, kind="content_changed").count() == 2


@pytest.mark.parametrize(
    "token,expected", [(1, "same_version_conflict"), (None, "unordered_conflict")]
)
def test_conflicting_evidence_is_separate_and_replay_does_not_clear_dispute(
    station_case, token, expected
):
    actor, product, _, _ = station_case
    original, _ = ingest(actor, product, "first", candidate(product, token, "10"))
    key_before = StationObservation.objects.get()
    conflicting = candidate(product, token if token else 2, "20")
    conflict, _ = ingest(actor, product, "conflict", conflicting)
    key = StationObservation.objects.get()
    assert key.watermark == token and key.disputed
    assert key.qualification_version > key_before.qualification_version
    current = key.revisions.get(is_current=True)
    assert current.pk == original.receipts.get().station_revision_id and current.value == 10
    other = key.revisions.get(candidate=True)
    assert other.value == 20 and not other.is_current
    assert conflict.receipts.get().outcome == expected
    assert conflict.receipts.get().station_revision_id == other.pk
    issues = list(QualityIssue.objects.filter(station_key=key, code=expected))
    assert len(issues) == 1
    replay, _ = ingest(actor, product, "replay-candidate", conflicting)
    assert replay.receipts.get().station_revision_id == other.pk
    ingest(actor, product, "replay-current", candidate(product, token, "10"))
    assert key.revisions.count() == 2
    key.refresh_from_db()
    assert key.disputed and key.watermark == token
    assert QualityIssue.objects.filter(station_key=key, code=expected).get().pk == issues[0].pk


def test_mapping_correction_records_basis_and_rejects_stale_replay(station_case):
    actor, product, _, _ = station_case
    allow(actor, product, "review")
    correct = candidate(product, 4, "10000", unit="µg/m³", rule=2)
    original = replace(correct, data=replace(correct.data, value=Decimal("40")), rule=1)
    ingest(actor, product, "first", original)
    key = StationObservation.objects.get()
    first = key.revisions.get(is_current=True)
    tampered = replace(correct, data=replace(correct.data, value=Decimal("18")))
    with pytest.raises(Denied, match="mapping_evidence_mismatch"):
        correct_mapping(actor.pk, product.pk, "station", key.pk, first.pk, 4, tampered, "F bad fix")
    assert key.revisions.get(is_current=True).pk == first.pk
    correct_mapping(actor.pk, product.pk, "station", key.pk, first.pk, 4, correct, "F mapping fix")
    key.refresh_from_db()
    current = key.revisions.get(is_current=True)
    assert (key.watermark, current.value, current.kind, current.actor_id) == (
        4,
        Decimal("10"),
        "local_mapping_fix",
        actor.pk,
    )
    assert current.reason == "F mapping fix"
    reimport, _ = ingest(actor, product, "again", original)
    assert reimport.receipts.get().outcome == "retired_mapping"
    assert (
        correct_mapping(actor.pk, product.pk, "station", key.pk, first.pk, 4, correct, "stale")
        == "mapping_review_required"
    )
    assert QualityIssue.objects.filter(station_key=key, code="mapping_review_required").exists()
    assert key.revisions.get(is_current=True).pk == current.pk


def test_direct_sql_cannot_create_second_current_or_promote_candidate(station_case):
    actor, product, _, _ = station_case
    ingest(actor, product, "first", candidate(product, 1))
    row = StationRevision.objects.get(is_current=True)
    with pytest.raises(IntegrityError) as second_current, transaction.atomic():
        sql_copy(row, revision_no=2)
    assert second_current.value.__cause__.diag.constraint_name == "stationrevision_current"
    with pytest.raises(IntegrityError) as candidate_current, transaction.atomic():
        sql_copy(row, revision_no=2, candidate=True)
    assert candidate_current.value.__cause__.diag.constraint_name == "stationrevision_candidate"


def test_product_lock_serializes_initial_key_and_competing_versions(station_case):
    actor, product, _, _ = station_case
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            future = pool.submit(
                in_db_session,
                pid_queue,
                lambda: ingest(actor, product, "v2", candidate(product, 2, "20")),
            )
            wait_for_block(pid_queue.get(timeout=5))
            ingest(actor, product, "v3", candidate(product, 3, "30"))
        second, _ = future.result(timeout=10)
    assert second.receipts.get().outcome == "late"
    (current,) = committed_current(product)
    assert (current[1], current[4]) == (3, Decimal("30"))
    assert StationRevision.objects.count() == 1


def test_commit_failure_leaves_no_partial_initial_or_promoted_observation(station_case):
    actor, product, _, _ = station_case
    with reject_observation_write(Receipt, "INSERT"):
        with pytest.raises(DatabaseError, match="s02_injected_write_failure"):
            ingest(actor, product, "v1", candidate(product, 1))
    assert committed_current(product) == []
    assert not Batch.objects.exists() and not Receipt.objects.exists()
    assert not Change.objects.filter(station_key__isnull=False).exists()

    ingest(actor, product, "v1", candidate(product, 1))
    before = committed_current(product)
    changes = list(Change.objects.values_list("pk", flat=True))
    with reject_observation_write(Receipt, "INSERT"):
        with pytest.raises(DatabaseError, match="s02_injected_write_failure"):
            ingest(actor, product, "v3", candidate(product, 3, "20"))
    assert committed_current(product) == before
    assert list(Change.objects.values_list("pk", flat=True)) == changes
    assert Batch.objects.count() == Receipt.objects.count() == StationRevision.objects.count() == 1
    retry, created = ingest(actor, product, "v3", candidate(product, 3, "20"))
    assert created
    assert retry.receipts.get().outcome == "content_changed"
    assert StationRevision.objects.count() == 2


def test_revoke_wins_against_a_writer_already_waiting_for_product_lock(station_case):
    actor, product, _, permission = station_case
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            future = pool.submit(
                in_db_session,
                pid_queue,
                lambda: ingest(actor, product, "waiting", candidate(product, 1)),
            )
            wait_for_block(pid_queue.get(timeout=5))
            revoke_grant(actor.pk, permission.pk, "withdraw while writer waits")
        with pytest.raises(Denied):
            future.result(timeout=10)
    assert committed_current(product) == []
    assert not Batch.objects.exists() and not Receipt.objects.exists()


def test_overlapping_batch_selection_reuses_key_and_adds_only_unseen_slot(station_case):
    actor, product, _, _ = station_case
    one = candidate(product, 1)
    first, _ = ingest(actor, product, "a", one)
    next_hour = candidate(product, 1, at=START + timedelta(hours=1))
    second, _ = ingest(actor, product, "b", one, next_hour)
    assert sorted(second.receipts.values_list("outcome", flat=True)) == ["duplicate", "first"]
    assert second.receipts.filter(
        station_revision_id=first.receipts.get().station_revision_id
    ).exists()
    assert StationObservation.objects.count() == StationRevision.objects.count() == 2


def test_code_scoped_write_cannot_replace_key_established_under_another_code(station_case):
    actor, product, epoch, broad_permission = station_case
    StationIdentifier.objects.create(
        source=product.source,
        station=epoch.station,
        code="002",
        active_period=epoch.active_period,
        evidence="F alternate active identifier",
    )
    allow(actor, product, object_code="001")
    allow(actor, product, object_code="002")
    revoke_grant(actor.pk, broad_permission.pk, "replace broad permission with named scopes")
    ingest(actor, product, "first", candidate(product, 1, "10", code="001"))
    snapshot = committed_current(product)
    with pytest.raises(Denied, match="identity_code_conflict"):
        ingest(actor, product, "cross-code", candidate(product, 2, "25", code="002"))
    assert committed_current(product) == snapshot
    assert StationObservation.objects.get().object_code == "001"
    assert StationRevision.objects.count() == 1
    assert Batch.objects.filter(product=product).count() == 1


def test_observation_commit_and_relocation_share_station_lock(station_case):
    actor, product, epoch, _ = station_case
    move_at = START + timedelta(hours=36)
    pid_queue = Queue()

    def move():
        return relocate(
            epoch.station,
            move_at,
            city=epoch.city,
            name="F relocated site",
            certainty="confirmed",
            location_evidence="F synthetic relocation",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            ingest(actor, product, "before", candidate(product, 1, "10"))
            future = pool.submit(in_db_session, pid_queue, move)
            wait_for_block(pid_queue.get(timeout=5))
        new_epoch = future.result(timeout=10)
    ingest(actor, product, "after", candidate(product, 1, "20", at=move_at))
    assert dict(StationObservation.objects.values_list("observed_at_utc", "site_epoch_id")) == {
        START: epoch.pk,
        move_at: new_epoch.pk,
    }


def test_mapping_correction_moves_identity_and_tracks_both_observations(station_case):
    from observations.models import MappingLink
    from observations.queries import read_current
    from s02_support import selector

    actor, product, old_site, _ = station_case
    new_site = create_site(product, "002", geography="B")
    allow(actor, product, "review")
    allow(actor, product, "display")
    original = candidate(product, 4, "10000", unit="µg/m³")
    legacy = replace(original, data=replace(original.data, value=Decimal("40")))
    ingest(actor, product, "first", legacy)
    old = StationObservation.objects.get(site_epoch=old_site)
    base = old.revisions.get(is_current=True)
    new_evidence = candidate(product, 4, "10000", code="002", unit="µg/m³", rule=2)
    assert (
        correct_mapping(
            actor.pk, product.pk, "station", old.pk, base.pk, 4, new_evidence, "F site mapping fix"
        )
        == "local_mapping_fix"
    )
    old.refresh_from_db()
    new = StationObservation.objects.get(site_epoch=new_site)
    assert old.retired and old.revisions.get(is_current=True).pk == base.pk
    assert (new.watermark, new.revisions.get(is_current=True).value) == (4, Decimal("10"))
    link = MappingLink.objects.get(product=product, station_old=old, station_new=new)
    assert (link.rule, link.actor_id) == (2, actor.pk)
    assert read_current(actor.pk, selector(product, old_site))["count"] == 0
    assert read_current(actor.pk, selector(product, new_site))["count"] == 1
    assert set(
        Change.objects.filter(station_key__in=[old, new]).values_list("kind", flat=True)
    ) >= {"mapping_retired", "local_mapping_fix"}
