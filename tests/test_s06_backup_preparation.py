"""V12/V13/V17: lost backup preparation commits leave discoverable cleanup work."""

import os
from contextlib import contextmanager
from datetime import timedelta

import pytest
from accounts.models import GovernanceAction
from django.db import DatabaseError, connection, connections
from observations import backup, backup_restore
from observations.backup_models import BackupSegment
from observations.backup_restore import run
from observations.controlled_storage import register_volume
from observations.disposition import dispose_due, reconcile_product
from observations.models import DispositionJob
from observations.policy import Denied, clock, revoke_grant
from s02_support import observe_db
from s05_support import grant
from s06_restore_http import release
from s06_restore_target import ALIAS
from test_s06_accounts import json_status
from test_s06_backup import (
    API,
    make_available,
    make_intent,
    prepare,
    proof,
    publish,
    sign_reviewed,
    target_empty,
)
from test_s06_backup import archives as archives
from test_s06_backup import restore_target as restore_target

pytest_plugins = ("test_s06_accounts",)
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not os.environ.get("S06_AUTHORITY_FIXTURE"), reason="use s06_restore_verify.py"
    ),
]


@contextmanager
def reject_manifest_commit():
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION fail_s06_manifest_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 's06_injected_commit_failure';
            END $$;
            CREATE CONSTRAINT TRIGGER fail_s06_manifest_commit
            AFTER UPDATE ON observations_backupsegment DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (OLD.manifest IS NULL AND NEW.manifest IS NOT NULL)
            EXECUTE FUNCTION fail_s06_manifest_commit();
        """)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("""
                DROP TRIGGER fail_s06_manifest_commit ON observations_backupsegment;
                DROP FUNCTION fail_s06_manifest_commit();
            """)


def check_registered(segment_id, product_id):
    with observe_db() as db, db.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.state, s.manifest, s.volume_generation, s.catalog_dependencies, j.due_at
              FROM observations_backupsegment s
              JOIN observations_dispositionjob j ON j.product_id = s.product_id
                       AND j.due_at = least(s.expires_at, s.snapshot_at + interval '5 minutes')
             WHERE s.id = %s AND s.product_id = %s AND j.reason = 'backup_prepare_timeout'
                       AND j.completed_at IS NULL
            """,
            [segment_id, product_id],
        )
        [(state, manifest, generation, dependencies, deadline)] = cursor.fetchall()
    assert state == "preparing" and manifest is None
    assert generation and dependencies and deadline


def test_v13_failed_manifest_commit_retains_durable_cleanup_intent(archives, monkeypatch, settings):
    a, b, custodian, root, authority, key, web, _ = archives
    basis = grant(custodian, a, "backup", fields=backup.BASE_FIELDS)
    grant(custodian, b, "backup", fields=backup.BASE_FIELDS)
    legal = make_available(web, b, key, authority)
    legal_file = backup.paths(root, legal)[1]
    original = backup.write_preparation
    registered = []

    def observe_registration(request, segment_id, *args):
        check_registered(segment_id, a.pk)
        assert not any(path.exists() for path in backup.paths(root, segment_id))
        registered.append(segment_id)
        return original(request, segment_id, *args)

    monkeypatch.setattr(backup, "write_preparation", observe_registration)
    with reject_manifest_commit():
        assert json_status(prepare(web, a), 503) == {"code": "backup_unavailable"}
    [segment_id] = registered
    check_registered(segment_id, a.pk)
    partial, final = backup.paths(root, segment_id)
    assert partial.stat().st_size > 0 and not final.exists()
    assert not GovernanceAction.objects.filter(action="backup_prepare", target=segment_id).exists()
    assert json_status(publish(web, segment_id), 409) == {"code": "precondition_conflict"}
    material = web.get(f"{API}backup-authority/publish/{segment_id}/")
    assert json_status(material, 409) == {"code": "precondition_conflict"}

    revoke_grant(custodian.pk, basis.pk, "F withdrawal after a failed preparation commit")
    wrong = root.parent / "offline-volume"
    wrong.mkdir(mode=0o700)
    register_volume(wrong)
    settings.S06_BACKUP_DIR = str(wrong)
    with pytest.raises(OSError, match="storage_generation_mismatch"):
        reconcile_product(a.pk)
    assert BackupSegment.objects.get(pk=segment_id).state == "blocked"
    jobs = DispositionJob.objects.filter(product=a, reason="revocation")
    assert jobs.get().completed_at is None
    assert partial.is_file()

    settings.S06_BACKUP_DIR = str(root)
    reconcile_product(a.pk)
    receipt = jobs.get().completed_at
    assert receipt is not None
    assert BackupSegment.objects.get(pk=segment_id).state == "removed"
    assert not partial.exists() and not final.exists() and legal_file.is_file()
    reconcile_product(a.pk)
    assert jobs.get().completed_at == receipt
    assert (
        GovernanceAction.objects.filter(action="backup_disposition", target=segment_id).count() == 1
    )


def test_v17_directory_rights_withdrawal_between_preparation_commits_writes_nothing(
    archives,
    monkeypatch,
):
    a, _, custodian, root, _, _, web, _ = archives
    grant(custodian, a, "backup", fields=backup.BASE_FIELDS, indicator="CO")
    catalog_basis = grant(custodian, a, "backup", fields={"catalog"})
    original = backup.write_preparation
    registered = []

    def withdraw_dependency(request, segment_id, *args):
        check_registered(segment_id, a.pk)
        revoke_grant(custodian.pk, catalog_basis.pk, "F catalog withdrawal before file creation")
        assert BackupSegment.objects.get(pk=segment_id).state == "blocked"
        # Regranting in another commit cannot revive the previous preparation.
        grant(custodian, a, "backup", fields={"catalog"})
        registered.append(segment_id)
        return original(request, segment_id, *args)

    monkeypatch.setattr(backup, "write_preparation", withdraw_dependency)
    assert json_status(prepare(web, a), 409) == {"code": "precondition_conflict"}
    [segment_id] = registered
    assert list(root.iterdir()) == []
    assert json_status(publish(web, segment_id), 409) == {"code": "precondition_conflict"}
    reconcile_product(a.pk)
    assert BackupSegment.objects.get(pk=segment_id).state == "removed"


@pytest.mark.parametrize("phase", ["publish", "restore"])
def test_v17_release_receipt_commit_cannot_outlive_external_seal(archives, restore_target, phase):
    _, b, custodian, root, authority, key, web, _ = archives
    target, guard = restore_target
    grant(custodian, b, "backup", fields=backup.BASE_FIELDS)
    if phase == "publish":
        operation = segment = json_status(prepare(web, b), 201)["operation_id"]
    else:
        segment = make_available(web, b, key, authority)
        operation = make_intent(web, segment, target)
    action = "backup_publish" if phase == "publish" else "restore_read"
    material = proof(web, phase, operation)
    cutoff = clock() + timedelta(seconds=2)
    material["expiry"] = cutoff.isoformat()
    sign_reviewed(key, authority, material)
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION s06_delay_release_marker() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(2.1);
                RETURN NULL;
            END $$;
            CREATE CONSTRAINT TRIGGER s06_delay_release_marker
            AFTER UPDATE ON accounts_governanceaction DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (OLD.outcome = 'pending' AND NEW.outcome = 'applied')
            EXECUTE FUNCTION s06_delay_release_marker();
        """)
    try:
        if phase == "publish":
            assert json_status(publish(web, operation), 403) == {"code": "authority_invalid"}
            assert BackupSegment.objects.get(pk=segment).state == "blocked"
            reconcile_product(b.pk)
            assert not any(path.exists() for path in backup.paths(root, segment))
        else:
            with pytest.raises(Denied, match="restore_read_denied"):
                run(operation, target, ALIAS, guard, release)
            target_empty()
        assert clock() >= cutoff
        assert (
            GovernanceAction.objects.get(request_id=operation, action=action).outcome == "withheld"
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER s06_delay_release_marker ON accounts_governanceaction")
            cursor.execute("DROP FUNCTION s06_delay_release_marker()")


def test_v13_unwitnessed_final_ack_is_unreadable_and_reconciled(archives, restore_target):
    a, b, custodian, root, authority, key, web, _ = archives
    target, _ = restore_target
    for product in (a, b):
        grant(custodian, product, "backup", fields=backup.BASE_FIELDS)
    segment, other = [make_available(web, product, key, authority) for product in (a, b)]
    file = backup.paths(root, segment)[1]
    # Disk state left by an interruption just after PG acknowledged publication.
    os.removexattr(file, backup.RELEASE_MARK)
    assert (
        GovernanceAction.objects.get(action="backup_publish", request_id=segment).outcome
        == "applied"
    )
    assert (
        web.post(
            f"{API}backup-segments/{segment}/restore-intents/", json={"target": str(target)}
        ).status_code
        == 409
    )
    backup.sweep_orphans()
    assert not file.exists()
    assert BackupSegment.objects.get(pk=segment).state == "removed"
    assert (
        GovernanceAction.objects.get(action="backup_publish", request_id=segment).outcome
        == "withheld"
    )
    assert make_intent(web, other, target)


@pytest.mark.parametrize("phase", ["publish", "restore"])
def test_v17_withdraw_regrant_after_durable_ack_closes_physical_gate(
    archives, restore_target, monkeypatch, phase
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    a, b, custodian, root, authority, key, web, _ = archives
    target, guard = restore_target
    basis = grant(custodian, a, "backup", fields=backup.BASE_FIELDS)
    grant(custodian, b, "backup", fields=backup.BASE_FIELDS)
    legal = make_available(web, b, key, authority)
    if phase == "publish":
        module = backup
        operation = segment = json_status(prepare(web, a), 201)["operation_id"]
    else:
        module = backup_restore
        segment = make_available(web, a, key, authority)
        operation = make_intent(web, segment, target)
    sign_reviewed(key, authority, proof(web, phase, operation))
    ack, revoked = Event(), Event()
    original = module.confirm_release

    def after_commit(*args):
        original(*args)
        ack.set()
        assert revoked.wait(6)

    def withdraw():
        try:
            assert ack.wait(6)
            revoke_grant(custodian.pk, basis.pk, "F withdrawal after durable ack")
            grant(custodian, a, "backup", fields=backup.BASE_FIELDS)
        finally:
            connections.close_all()
            revoked.set()

    monkeypatch.setattr(module, "confirm_release", after_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(withdraw)
        if phase == "publish":
            assert json_status(publish(web, operation), 409) == {"code": "precondition_conflict"}
        else:
            with pytest.raises(Denied, match="restore_read_denied"):
                run(operation, target, ALIAS, guard, release)
            target_empty()
        worker.result(timeout=6)
    assert ack.is_set()
    action = "backup_publish" if phase == "publish" else "restore_read"
    assert GovernanceAction.objects.get(request_id=operation, action=action).outcome == "withheld"
    assert BackupSegment.objects.get(pk=segment).state == "blocked"
    reconcile_product(a.pk)
    assert not any(file.exists() for file in backup.paths(root, segment))
    assert make_intent(web, legal, target)


def test_v17_publication_marker_finishing_after_seal_withholds_the_copy(archives, monkeypatch):
    a, _, custodian, root, authority, key, web, _ = archives
    grant(custodian, a, "backup", fields=backup.BASE_FIELDS)
    segment = json_status(prepare(web, a), 201)["operation_id"]
    material = proof(web, "publish", segment)
    cutoff = clock() + timedelta(seconds=2)
    material["expiry"] = cutoff.isoformat()
    sign_reviewed(key, authority, material)
    write = backup.record_file_release
    witnessed = []

    def slow_disk(fd, identifier):
        write(fd, identifier)
        with observe_db() as db:
            db.execute(
                "SELECT pg_sleep(greatest(extract(epoch FROM %s - clock_timestamp()),0) + 0.02)",
                [cutoff],
            )
        witnessed.append(identifier)

    monkeypatch.setattr(backup, "record_file_release", slow_disk)
    assert json_status(publish(web, segment), 403) == {"code": "authority_invalid"}
    assert len(witnessed) == 1
    assert backup.RELEASE_MARK not in os.listxattr(backup.paths(root, segment)[1])
    assert (
        GovernanceAction.objects.get(action="backup_publish", request_id=segment).outcome
        == "withheld"
    )
    reconcile_product(a.pk)
    assert not any(file.exists() for file in backup.paths(root, segment))


def test_v13_retry_after_unwitnessed_copy_unlinked_and_cleanup_commit_rejected(
    archives, restore_target, settings
):
    a, b, custodian, root, authority, key, web, _ = archives
    target, _ = restore_target
    for product in (a, b):
        grant(custodian, product, "backup", fields=backup.BASE_FIELDS)
    segment, other = [make_available(web, product, key, authority) for product in (a, b)]
    path = backup.paths(root, segment)[1]
    os.removexattr(path, backup.RELEASE_MARK)
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION s06_reject_unlink_ack() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE check_violation USING MESSAGE='s06 injected unlink commit failure'; END $$;
            CREATE CONSTRAINT TRIGGER s06_reject_unlink_ack
              AFTER UPDATE ON observations_backupsegment DEFERRABLE INITIALLY DEFERRED
              FOR EACH ROW WHEN (NEW.state = 'removed') EXECUTE FUNCTION s06_reject_unlink_ack();
        """)
    try:
        with pytest.raises(DatabaseError) as error:
            backup.sweep_orphans()
        assert error.value.__cause__.sqlstate == "23514"
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER s06_reject_unlink_ack ON observations_backupsegment")
            cursor.execute("DROP FUNCTION s06_reject_unlink_ack()")
    assert not path.exists()
    job = DispositionJob.objects.get(product=a, reason="backup_publication_withheld")
    assert job.completed_at is None and job.due_at <= clock()
    assert BackupSegment.objects.get(pk=segment).state == "blocked"
    assert (
        GovernanceAction.objects.get(action="backup_publish", request_id=segment).outcome
        == "withheld"
    )
    wrong = root.parent / "different-volume"
    wrong.mkdir(mode=0o700)
    register_volume(wrong)
    settings.S06_BACKUP_DIR = str(wrong)
    with pytest.raises(Denied, match="cleanup_failed"):
        dispose_due(a.pk)
    job.refresh_from_db()
    assert job.completed_at is None
    settings.S06_BACKUP_DIR = str(root)
    dispose_due()
    job.refresh_from_db()
    assert job.completed_at and BackupSegment.objects.get(pk=segment).state == "removed"
    dispose_due()
    assert DispositionJob.objects.get(pk=job.pk).completed_at == job.completed_at
    assert GovernanceAction.objects.filter(action="backup_disposition", target=segment).count() == 1
    assert make_intent(web, other, target)
