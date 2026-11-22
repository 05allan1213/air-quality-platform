"""Forward, backwards and pending S03 delivery in a test-owned PostgreSQL database."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_pending_s03_run_and_grants_survive_upgrade_and_disposable_rollback():
    old = [
        ("collection", "0006_reconcile_legacy_quota"),
        ("observations", "0009_receipt_collection_metadata"),
    ]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps

        def model(name):
            return apps.get_model(name)

        actor = model("auth.User").objects.create(username="F-upgrade")
        source = model("observations.Source").objects.create(namespace="s03.fixture", publisher="F")
        product = model("observations.Product").objects.create(
            source=source,
            code="L",
            family="F",
            shape="L",
            level="city",
            synthetic=True,
            contract_version="s03.f.v1",
        )
        grant = model("observations.UseGrant").objects.create(
            product=product,
            operation="acquire_online",
            audience="platform",
            geography="*",
            data_from=date.min,
            data_until=date.max,
            fields=["*"],
            terms="F legacy",
            terms_version="1",
            confirmed_by=actor,
        )
        job = model("collection.Job").objects.create(
            product=product, executor=actor, profile="f-exact-v1", expected_codes=None
        )
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        window = model("collection.WorkWindow").objects.create(
            product=product,
            scope_key="national",
            target_start=now,
            target_end=now + timedelta(hours=1),
            profile=job.profile,
        )
        uid = uuid4()
        run = model("collection.Run").objects.create(
            job=job,
            window=window,
            actor=actor,
            executor=actor,
            reason="manual",
            request_id=uid,
            dispatch_due=now,
        )
        MigrationExecutor(connection).migrate(leaves)
        from collection.models import Run
        from observations.models import UseGrant

        recovered = Run.objects.select_related("job", "window").get(pk=run.pk)
        assert (
            recovered.status,
            recovered.request_id,
            recovered.endpoint,
            recovered.validation_grant_id,
            recovered.reserved_at,
        ) == ("pending", uid, "", None, None)
        assert (
            recovered.window.selection == recovered.job.selection == {}
            and not recovered.job.disposed
        )
        old_grant = UseGrant.objects.get(pk=grant.pk)
        assert (
            old_grant.max_total_requests is None
            and old_grant.endpoints == []
            and old_grant.terms_version == "1"
        )
        MigrationExecutor(connection).migrate(old)
        assert model("collection.Run").objects.get(pk=run.pk).request_id == uid
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef('s03_freeze_job'::regproc)")
            assert "NEW.disposed" not in cursor.fetchone()[0]
    finally:
        MigrationExecutor(connection).migrate(leaves)
