"""V12: verify file disposition against the recorded private storage generation."""

import errno
import os
from datetime import timedelta

import pytest
from observations import controlled_storage, history
from observations.disposition import dispose_due, schedule
from observations.models import StagedInput
from observations.policy import Denied, clock
from s02_support import create_product, observe_db

pytestmark = pytest.mark.django_db(transaction=True)
PAYLOAD = b"self-authored F file for disposition"


@pytest.fixture
def staged_file(django_user_model, tmp_path, settings):
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    settings.S02_STAGE_DIR = str(root)
    user = django_user_model.objects.create_user("F-file-owner")
    product = create_product("F-controlled-file")
    now = clock()
    stage = StagedInput.objects.create(
        owner=user,
        product=product,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
        state="claimed",
        volume_generation=controlled_storage.register_volume(root),
    )
    path = root / str(stage.pk)
    path.write_bytes(PAYLOAD)
    path.chmod(0o600)
    job = schedule(product, now, "stage_cleanup")
    return stage, path, job


def job_state(job):
    with observe_db() as db:
        return db.execute(
            "SELECT completed_at, error_code FROM observations_dispositionjob WHERE id=%s",
            [job.pk],
        ).fetchone()


def expire(stage):
    StagedInput.objects.filter(pk=stage.pk).update(expires_at=clock())


def test_swapped_private_root_never_proves_absence(staged_file, tmp_path):
    stage, path, job = staged_file
    root = path.parent
    detached = root.rename(tmp_path / "detached")
    root.mkdir(mode=0o700)
    assert controlled_storage.register_volume(root) != stage.volume_generation
    # A valid-looking file with the same name on the replacement root is not the input.
    path.write_bytes(b"independent F fixture")
    path.chmod(0o600)
    with pytest.raises(OSError, match="storage_generation_mismatch"):
        history.inspect_input(stage)
    path.unlink()
    expire(stage)
    with pytest.raises(Denied, match="cleanup_failed"):
        dispose_due(stage.product_id)
    assert job_state(job) == (None, "cleanup_failed")
    assert (detached / path.name).read_bytes() == PAYLOAD
    root.rmdir()
    detached.rename(root)
    dispose_due(stage.product_id)
    assert job_state(job)[0] is not None
    assert not path.exists()


def test_unlink_sync_failure_requires_directory_sync_on_missing_retry(staged_file, monkeypatch):
    stage, path, job = staged_file
    expire(stage)
    sync = os.fsync
    synced_absence = []

    def fail_sync(fd):
        assert os.path.samestat(os.fstat(fd), path.parent.stat())
        assert not path.exists()
        assert job_state(job)[0] is None
        raise OSError(errno.EIO, "injected directory fsync failure")

    monkeypatch.setattr(controlled_storage.os, "fsync", fail_sync)
    for _ in range(2):
        with pytest.raises(Denied, match="cleanup_failed"):
            dispose_due(stage.product_id)
        assert job_state(job) == (None, "cleanup_failed")
        assert not path.exists()

    def audit_sync(fd):
        assert os.path.samestat(os.fstat(fd), path.parent.stat())
        synced_absence.append(not path.exists())
        sync(fd)

    monkeypatch.setattr(controlled_storage.os, "fsync", audit_sync)
    dispose_due(stage.product_id)
    completed = job_state(job)
    assert synced_absence == [True]
    assert completed[0] is not None and completed[1] == ""
    dispose_due(stage.product_id)
    assert job_state(job) == completed
    assert synced_absence == [True]


def test_legacy_unbound_file_and_missing_root_stay_pending(staged_file, settings, tmp_path):
    stage, path, job = staged_file
    settings.S06_BACKUP_DIR = str(tmp_path / "offline-backup")
    expire(stage)
    StagedInput.objects.filter(pk=stage.pk).update(volume_generation=None)
    with pytest.raises(Denied, match="cleanup_failed"):
        dispose_due()
    assert job_state(job) == (None, "cleanup_failed")
    assert path.read_bytes() == PAYLOAD


def test_orphan_failure_does_not_hide_registered_work(staged_file, settings, tmp_path):
    stage, path, job = staged_file
    expire(stage)
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o700)
    (backup / "invalid.partial").mkdir(mode=0o700)
    clearable = backup / "1bd6ae14-7f70-409c-a69a-aef793661b73.partial"
    clearable.write_bytes(PAYLOAD)
    clearable.chmod(0o600)
    settings.S06_BACKUP_DIR = str(backup)
    with pytest.raises(Denied, match="cleanup_failed"):
        dispose_due()
    assert job_state(job)[0] is not None
    assert not path.exists() and not clearable.exists()
    assert (backup / "invalid.partial").is_dir()


def test_orphan_catalog_lock_timeout_leaves_registered_cleanup_committed(
    staged_file, settings, tmp_path
):
    stage, path, job = staged_file
    expire(stage)
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o700)
    settings.S06_BACKUP_DIR = str(backup)
    with observe_db() as blocker:
        blocker.execute("LOCK TABLE observations_backupsegment IN ROW EXCLUSIVE MODE")
        with pytest.raises(Denied, match="cleanup_failed"):
            dispose_due()
        assert job_state(job)[0] is not None
        assert not path.exists()
    dispose_due()
