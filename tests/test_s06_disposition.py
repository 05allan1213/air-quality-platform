"""V11/V12/C02: the old rows and bytes exist between loss and reconciliation."""

import fcntl
import os
from datetime import timedelta

import pytest
import test_s06_accounts
from django.db import DatabaseError, transaction
from observations.controlled_storage import register_volume
from observations.disposition import dispose_due
from observations.models import (
    Batch,
    DispositionJob,
    Receipt,
    StagedInput,
    StationObservation,
    StationRevision,
)
from observations.policy import REQUIRED, Denied, applicable, clock, locked_product, revoke_grant
from observations.revisions import key_scope
from s02_support import (
    allow,
    candidate,
    create_product,
    create_site,
    observe_db,
    reject_observation_write,
)
from s06_witness import witness
from test_observation_disposition import ingest

account_fixtures = (
    test_s06_accounts.identities,
    test_s06_accounts.sql_hook,
    test_s06_accounts.web_clients,
)
identities, sql_hook, web_clients = account_fixtures

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def fixture(django_user_model):
    operator = django_user_model.objects.create_user("disposition-F", is_superuser=True)
    p = create_product("disposition-F")
    a = create_site(p, "001", "A")
    b = create_site(p, "002", "B")
    for op in ("process_local", "review", "display"):
        allow(operator, p, op)
    return operator, p, a, b


def test_v11_regrant_before_sweep_keeps_only_continuous_inputs(fixture):
    op, p, a, b = fixture
    grant = allow(op, p, geography="A")
    allow(op, p, geography="A", indicator="CO", fields=sorted(REQUIRED))
    allow(op, p, geography="B")
    initial = ingest(
        op,
        p,
        "mixed",
        candidate(p, 1),
        candidate(p, 1, "20", indicator="PM10", unit="µg/m³"),
        candidate(p, 1, "31", code="002"),
    )
    pm = candidate(p, 1, "23", indicator="PM10", unit="µg/m³")
    ingest(op, p, "conflict", pm)
    removed = StationObservation.objects.get(site_epoch=a, indicator="PM10")
    unchanged = StationObservation.objects.get(site_epoch=b)
    co = StationObservation.objects.get(site_epoch=a, indicator="CO")
    assert removed.disputed and removed.revisions.count() == 2
    revoke_grant(op.pk, grant.pk, "retention_withdrawn")
    job = DispositionJob.objects.get(boundary_grant=grant)
    regrant = allow(op, p, geography="A", valid_from=grant.valid_from)  # backdated terms
    job.refresh_from_db()
    assert job.completed_at is None and regrant.pk not in job.boundary_basis
    with observe_db() as sql:
        assert sql.execute(
            "SELECT count(*) FROM observations_stationrevision WHERE key_id=%s", (removed.pk,)
        ).fetchone() == (2,)
    with transaction.atomic():
        current = locked_product(p.pk)
        with pytest.raises(Denied):
            applicable(current, "retain_normalized", key_scope(removed), REQUIRED, clock())
        applicable(current, "retain_normalized", key_scope(co), REQUIRED, clock())
        with pytest.raises(Denied):
            applicable(
                current, "retain_normalized", key_scope(co), REQUIRED | {"raw_field"}, clock()
            )
        applicable(
            current, "retain_normalized", key_scope(unchanged), REQUIRED | {"raw_field"}, clock()
        )
    with pytest.raises(Denied):
        ingest(op, p, "conflict", pm)
    with reject_observation_write(DispositionJob, "UPDATE", "NEW.completed_at IS NOT NULL"):
        with pytest.raises(DatabaseError):
            dispose_due(p.pk)
    job.refresh_from_db()
    assert job.completed_at is None and removed.revisions.count() == 2
    dispose_due(p.pk)
    job.refresh_from_db()
    assert job.completed_at and not StationRevision.objects.filter(key_id=removed.pk).exists()
    assert not Receipt.objects.filter(station_revision__key_id=removed.pk).exists()
    assert list(co.revisions.values_list("raw_value", flat=True)) == [None]
    assert unchanged.revisions.get().raw_value == "31"
    assert Batch.objects.get(pk=initial.pk).identity is None
    with pytest.raises(Denied):
        ingest(op, p, "conflict", pm)
    ingest(op, p, "independent", candidate(p, 2, "24", indicator="PM10", unit="µg/m³"))
    assert StationObservation.objects.get(site_epoch=a, indicator="PM10").pk != removed.pk


def test_v12_expiry_open_reader_and_idempotent_cleanup(fixture, tmp_path, settings):
    op, p, a, b = fixture
    from django.db import connection
    from observations.disposition import PACKAGE_SCOPE
    from observations.models import Product

    settings.S02_STAGE_DIR = str(tmp_path)
    tmp_path.chmod(0o700)
    terms = dict(
        geography="*",
        data_from=PACKAGE_SCOPE.start,
        data_until=PACKAGE_SCOPE.end,
        fields=["archive"],
    )
    cutoff = clock() + timedelta(seconds=1)
    grant = allow(op, p, "retain_raw", valid_until=cutoff, expiry_mode="recoverable", **terms)
    stage = StagedInput.objects.create(
        product=p,
        owner=op,
        state="ready",
        expires_at=clock() + timedelta(minutes=15),
        volume_generation=register_volume(tmp_path),
    )
    path = tmp_path / str(stage.pk)
    path.write_bytes(b"private F test content")
    path.chmod(0o600)
    keep = tmp_path / "independent-F"
    keep.write_bytes(b"F control")
    # Wait on the PG clock. The scheduled job and grant keep their real deadlines.
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_sleep(greatest(0, extract(epoch from %s-clock_timestamp()))+0.02)", [cutoff]
        )
    allow(op, p, "retain_raw", valid_from=grant.valid_from, **terms)
    job = DispositionJob.objects.get(boundary_grant=grant)
    with pytest.raises(Denied):
        applicable(Product.objects.get(pk=p.pk), "retain_raw", PACKAGE_SCOPE, {"archive"}, clock())
    with path.open("rb") as reader:
        fcntl.flock(reader.fileno(), fcntl.LOCK_SH)
        with pytest.raises(Denied, match="cleanup_failed"):
            dispose_due(p.pk)
        job.refresh_from_db()
        assert (
            job.completed_at is None and path.exists() and os.fstat(reader.fileno()).st_nlink == 1
        )
    dispose_due(p.pk)
    job.refresh_from_db()
    end = job.completed_at
    assert end and not os.path.lexists(path) and keep.read_bytes() == b"F control"
    dispose_due(p.pk)
    job.refresh_from_db()
    assert job.completed_at == end


def test_v11_optional_fields_binding_preview_and_physical_removal(fixture, identities, web_clients):
    from dataclasses import replace
    from uuid import uuid4

    from accounts.models import GovernanceAction
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from test_s06_accounts import json_status, permission, sign_in

    op, p, a, _ = fixture
    _, _, admin, _, password = identities
    admin.user_permissions.add(permission("observations.manage_grants"))
    web = sign_in(web_clients(), admin, password)
    allow(op, p, geography="A")
    allow(op, p, "review", fields=["*"])
    optional = allow(op, p, geography="A", fields=["source_description", "source_time"])
    keep = allow(op, p, geography="A", fields=["source_time"])
    reading = candidate(p, 1)
    ingest(
        op,
        p,
        "optional-source",
        replace(reading, data=replace(reading.data, source_description="F-optional")),
    )
    key = StationObservation.objects.get(site_epoch=a)
    url = f"/api/v1/admin/grants/{optional.pk}/actions/"
    evidence = "/api/v1/admin/observation-evidence/"
    params = {"product": p.pk, "station_key": key.pk, "scope": "synthetic"}

    def exported():
        return json_status(web.get(evidence, params=params), 200)["revisions"][0]

    assert exported()["source_description"] == "F-optional" and exported()["source_time"]
    version = json_status(web.get(url), 200)["version"]
    cmd = dict(
        action="withdraw",
        reason_code="rights_review",
        request_id=str(uuid4()),
        expected_version=version,
    )
    result = json_status(web.post(url, json=cmd), 200)
    assert json_status(web.post(url, json=cmd), 200) == result
    allow(op, p, geography="A", fields=["source_description"], valid_from=optional.valid_from)
    assert key.revisions.get().source_description == "F-optional"
    assert "source_description" not in exported() and exported()["source_time"]
    control = f"/api/v1/admin/disposition-products/{p.pk}/"
    plan = json_status(web.get(control), 200)
    preview = dict(
        action="prepare_disposal",
        reason_code="due_reconciliation",
        request_id=str(uuid4()),
        expected_version=plan["version"],
        basis=plan["basis"],
    )
    assert web.post(control, json=preview | {"product": p.pk}).status_code == 400
    token = json_status(web.post(control, json=preview), 200)["operation_id"]
    assert json_status(web.post(control, json=preview), 200)["operation_id"] == token
    admin.user_permissions.remove(permission("observations.manage_grants"))
    with pytest.raises(CommandError, match="permission_denied"):
        call_command("s06_dispose", "--operation", token)
    assert DispositionJob.objects.get(boundary_grant=optional).completed_at is None
    admin.user_permissions.add(permission("observations.manage_grants"))
    call_command("s06_dispose", "--operation", token)
    with pytest.raises(CommandError, match="precondition_conflict"):
        call_command("s06_dispose", "--operation", token)
    with observe_db() as sql:
        assert sql.execute(
            "SELECT source_description, provenance->>'source_time', raw_value FROM observations_stationrevision WHERE key_id=%s",
            (key.pk,),
        ).fetchone() == ("", reading.data.time.source_time, "10")
    assert GovernanceAction.objects.filter(action="execute_disposal", origin=token).count() == 1
    revoke_grant(op.pk, keep.pk, "rights_review")
    dispose_due(p.pk)
    assert not key.revisions.get().provenance.get("source_time")


@pytest.mark.parametrize("first", ["regrant", "sweep"])
def test_v17_c02_regrant_and_sweep_commit_orders(
    fixture, identities, web_clients, first, record_property
):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue

    from django.db import close_old_connections, connection, connections
    from s02_support import selector
    from test_s05_races import blocked_by, pid
    from test_s06_accounts import sign_in

    op, p, a, b = fixture
    owner, _, _, _, password = identities
    web = sign_in(web_clients(), owner, password)
    lost = allow(op, p, geography="A")
    allow(op, p, geography="B")
    ingest(op, p, "old-AB", candidate(p, 1), candidate(p, 1, code="002"))
    old = StationObservation.objects.get(site_epoch=a).pk
    revoke_grant(op.pk, lost.pk, "interruption")

    def replacement():
        return allow(op, p, geography="A", valid_from=lost.valid_from)

    def sweep():
        dispose_due(p.pk)

    def current(epoch):
        return web.get("/api/v1/observations/current/", params=selector(p, epoch))

    pending, sibling = current(a), current(b)
    assert pending.status_code == 403 and sibling.json()["results"][0]["value"] is not None
    queued = Queue()

    def wait_for_product(execute, sql, params, many, context):
        if 'FROM "observations_product"' in sql and "FOR UPDATE" in sql:
            queued.put(pid())
        return execute(sql, params, many, context)

    def competing():
        close_old_connections()
        try:
            with connection.execute_wrapper(wait_for_product):
                (sweep if first == "regrant" else replacement)()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            locked_product(p.pk)
            (replacement if first == "regrant" else sweep)()
            task = pool.submit(competing)
            blocked_by(queued.get(timeout=4), pid())
            wait_at = clock()
        task.result(timeout=5)
    with observe_db() as db:
        (old_after, sibling_after) = db.execute(
            """SELECT count(*) FILTER (WHERE k.id=%s), count(*) FILTER (WHERE k.site_epoch_id=%s)
                 FROM observations_stationrevision r
                 JOIN observations_stationobservation k ON k.id=r.key_id
                WHERE k.product_id=%s""",
            (old, b.pk, p.pk),
        ).fetchone()
    assert old_after == 0 and sibling_after == 1
    after = current(a)
    assert after.json()["results"] == []
    assert current(b).json()["results"][0]["value"] is not None
    with pytest.raises(Denied):
        ingest(op, p, "old-AB", candidate(p, 1))
    p.refresh_from_db(fields=["rights_version"])
    witness(
        record_property,
        pg_at=wait_at,
        condition="C02",
        order=first,
        lock_target="product",
        pg_lock_wait=True,
        http=pending.status_code,
        sibling_http=sibling.status_code,
        replay_http=after.status_code,
        old_revisions_after=old_after,
        sibling_rows_after=sibling_after,
        rights_version=p.rights_version,
    )


@pytest.mark.parametrize(
    "commit_order", ["before_expiry", "during_insert", "after_registration", "constraints_checked"]
)
def test_v17_c02_registered_grant_cannot_backdate_continuity(fixture, commit_order):
    from django.db import connection
    from observations.models import Product, UseGrant

    op, p, a, b = fixture
    cutoff = clock() + timedelta(seconds=1.5)
    old = allow(op, p, geography="A", valid_until=cutoff, expiry_mode="recoverable")
    keep = allow(op, p, geography="B")
    ingest(op, p, "before-expiry", candidate(p, 1), candidate(p, 1, code="002"))
    key = StationObservation.objects.get(site_epoch=a)
    control = StationObservation.objects.get(site_epoch=b)

    def cross_cutoff():
        with observe_db() as db:
            db.execute(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM %s-clock_timestamp())))", (cutoff,)
            )
            assert db.execute("SELECT clock_timestamp() >= %s", (cutoff,)).fetchone() == (True,)

    def pause_in_insert(execute, sql, params, many, context):
        if commit_order == "during_insert" and sql.startswith(
            'INSERT INTO "observations_usegrant"'
        ):
            assert clock() < cutoff
            cross_cutoff()
        return execute(sql, params, many, context)

    with transaction.atomic(), connection.execute_wrapper(pause_in_insert):
        replacement = allow(op, p, geography="A", valid_from=old.valid_from)
        if commit_order == "constraints_checked":
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            assert UseGrant.objects.get(pk=replacement.pk).registered_at is None
        if commit_order in {"after_registration", "constraints_checked"}:
            assert clock() < cutoff
            cross_cutoff()
            with observe_db() as db:
                assert db.execute(
                    "SELECT count(*) FROM observations_usegrant WHERE id=%s", (replacement.pk,)
                ).fetchone() == (0,)
    if commit_order == "before_expiry":
        cross_cutoff()
    replacement.refresh_from_db()
    assert (replacement.registered_at <= cutoff) == (commit_order == "before_expiry")
    assert UseGrant.objects.get(pk=keep.pk).registered_at < cutoff
    product = Product.objects.get(pk=p.pk)
    if commit_order == "before_expiry":
        applicable(product, "retain_normalized", key_scope(key), REQUIRED, clock())
    else:
        with pytest.raises(Denied):
            applicable(product, "retain_normalized", key_scope(key), REQUIRED, clock())
    dispose_due(p.pk)
    job = DispositionJob.objects.get(boundary_grant=old)
    assert (replacement.pk in job.boundary_basis) == (commit_order == "before_expiry")
    assert job.completed_at and StationRevision.objects.filter(key=control).exists()
    assert StationRevision.objects.filter(key=key).exists() == (commit_order == "before_expiry")
