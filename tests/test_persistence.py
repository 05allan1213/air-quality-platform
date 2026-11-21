from dataclasses import asdict
from datetime import timedelta

import psycopg
import pytest
from django.contrib.auth.models import Permission, User
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone
from samples.models import FixtureGrant, FixtureObservation, FixtureRun
from samples.normalization import KEY, Rejected, load_fixture, normalize
from samples.policy import require_uses
from samples.services import REASON, consume, store_observation

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def actor():
    # transaction=True flushes the test DB between cases, including migration data.
    for op in ["process_local", "retain", "display"]:
        FixtureGrant.objects.update_or_create(
            product="F",
            source="local.fixture.v1",
            geography="synthetic",
            audience="local_authenticated",
            operation=op,
            defaults={
                "valid_from": timezone.now(),
                "enabled": True,
                "basis": "self-authored F test",
            },
        )
    user = User.objects.create_user("operator")
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="samples", codename="run_fixture")
    )
    return user


def run_for(user):
    return FixtureRun.objects.create(actor=user, reason=REASON, fixture_key=KEY)


@pytest.mark.parametrize("invalid", [{"product": "C"}, {"synthetic": False}, {"value": "-1.000"}])
def test_pg_rejects_wrong_observation(invalid):
    fields = asdict(normalize(load_fixture())) | invalid
    with pytest.raises(IntegrityError), transaction.atomic():
        FixtureObservation.objects.create(**fields, fetched_at=timezone.now())
    assert FixtureObservation.objects.count() == 0


def test_pg_rejects_duplicate_key():
    fields = asdict(normalize(load_fixture()))
    FixtureObservation.objects.create(**fields, fetched_at=timezone.now())
    with pytest.raises(IntegrityError), transaction.atomic():
        FixtureObservation.objects.create(**fields, fetched_at=timezone.now())
    assert FixtureObservation.objects.count() == 1


@pytest.mark.parametrize(
    "change",
    [
        {"source": "other"},
        {"audience": "public"},
        {"geography": "real"},
        {"valid_from": timezone.now() + timedelta(days=7)},
        {"valid_until": timezone.now() - timedelta(days=7)},
        {"enabled": False},
    ],
)
def test_missing_scope_is_denied(actor, change):
    FixtureGrant.objects.filter(operation="display").update(**change)
    with transaction.atomic(), pytest.raises(Rejected, match="display_denied"):
        require_uses("F", "display")


def test_conflict_never_replaces_first_value(actor):
    original = store_observation(normalize(load_fixture() | {"raw_value": "2000"}), timezone.now())
    run = run_for(actor)
    assert consume(run.pk) == "fixture_conflict"
    run.refresh_from_db()
    assert (run.state, run.error_code, run.observation_id) == ("rejected", "fixture_conflict", None)
    original.refresh_from_db()
    assert FixtureObservation.objects.count() == 1 and str(original.value) == "2.000"


def test_pg_failure_after_first_insert_rolls_back_observation_and_link(actor):
    run = run_for(actor)
    assert FixtureObservation.objects.count() == 0
    with connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION s01_after_insert() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.state='succeeded' AND EXISTS (SELECT 1 FROM samples_fixtureobservation WHERE id=NEW.observation_id) THEN
              RAISE EXCEPTION 's01_injected_after_verified_insert';
            END IF;
            RETURN NEW;
          END $$;
          CREATE TRIGGER inject_failure BEFORE UPDATE OF state ON samples_fixturerun
          FOR EACH ROW EXECUTE FUNCTION s01_after_insert();""")
    try:
        with pytest.raises(DatabaseError, match="s01_injected_after_verified_insert"):
            consume(run.pk)
        with psycopg.connect(**connection.get_connection_params()) as observer:
            assert observer.execute(
                "SELECT count(*) FROM samples_fixtureobservation"
            ).fetchone() == (0,)
            assert observer.execute(
                "SELECT state, error_code, observation_id FROM samples_fixturerun WHERE id=%s",
                (run.pk,),
            ).fetchone() == ("failed", "processing_failed", None)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER inject_failure ON samples_fixturerun; DROP FUNCTION s01_after_insert()"
            )


def test_operator_revoked_while_consumer_waits_for_grants(actor):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue

    from django.db import connections

    run = run_for(actor)
    worker_pid = Queue()

    def consume_in_another_session():
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                worker_pid.put(cur.fetchone()[0])
            return consume(run.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(1) as pool:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SELECT id FROM samples_fixturegrant WHERE operation='retain' FOR UPDATE")
            future = pool.submit(consume_in_another_session)
            pid = worker_pid.get(timeout=5)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                cur.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", (pid,))
                if cur.fetchone()[0]:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("consumer did not reach the locked grant")
            actor.user_permissions.clear()
        assert future.result(timeout=5) == "permission_denied"
        assert FixtureObservation.objects.count() == 0
