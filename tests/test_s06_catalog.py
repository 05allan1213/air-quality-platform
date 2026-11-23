"""Directory governance keeps original identities and effective intervals intact."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from observations.catalog import resolve
from observations.disposition import reconcile_product
from observations.models import CatalogChange, QualityIssue, StationIdentifier, StationObservation
from observations.policy import RAW, REQUIRED, Denied, Scope, revoke_grant
from observations.queries import changes_since
from s05_support import HOUR, START, authorize, candidate, city, grant, ingest, product, site
from test_s05_races import blocked_by, pid
from test_s06_accounts import sign_in
from test_s06_accounts import sql_hook as sql_hook
from test_s06_accounts import web_clients as web_clients
from test_s06_quality import PASSWORD, QUALITY, ask, check, command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def directory(django_user_model, web_clients):
    op = django_user_model.objects.create_superuser("catalog-op", password=PASSWORD)
    admin = sign_in(web_clients(), op, PASSWORD)
    p = product("catalog")
    a = site(p, "101")
    ident = StationIdentifier.objects.get(station=a.station)
    b = city(p, "102")
    s = product("catalog-sibling", source=p.source)
    other = product("catalog-independent")
    separate = site(other, "101", name=a.station.name)
    for item in (p, s):
        authorize(op, item)
        grant(op, item, "review", fields=REQUIRED | {"catalog"})
    return op, p, a, ident, b, s, other, separate, admin


def preview(admin, p, ident, action, at, target, status=200):
    return check(
        admin.get(
            f"/api/v1/admin/stations/{ident.station_id}/changes/",
            params={
                "product": p.pk,
                "action": action,
                "identifier": ident.pk,
                "at": at.isoformat(),
                "target": target,
            },
        ),
        status,
    )


def edit(admin, ident, proof, action, status=200, **terms):
    return check(
        admin.post(
            f"/api/v1/admin/stations/{ident.station_id}/changes/",
            json={
                "action": action,
                "request_id": str(uuid4()),
                "reason_code": {
                    "rename": "documented_rename",
                    "relocate": "documented_relocation",
                    "reuse_code": "documented_code_reuse",
                }[action],
                "expected_version": proof["version"],
                "evidence_ref": proof["evidence_ref"],
            }
            | terms,
        ),
        status,
    )


def test_v08_timebound_rename_relocation_reuse_and_independent_source(directory):
    op, p, old, ident, b, sibling, other, separate, admin = directory
    at = START + HOUR
    ingest(op, p, [candidate(p, "101", at=START, token=1)])
    first = preview(admin, p, ident, "rename", at, "renamed F site")
    relocate_before_rename = preview(admin, p, ident, "relocate", at + HOUR, b.pk)
    renamed = edit(admin, ident, first, "rename")
    assert renamed["status"] == "applied"
    alias = StationIdentifier.objects.get(station=old.station, kind="alias")
    assert alias.code == "renamed F site" and alias.active_period.lower == at
    assert resolve(p, "101", at)[0].pk == old.pk
    assert (
        edit(admin, ident, relocate_before_rename, "relocate", 409)["code"]
        == "precondition_conflict"
    )
    move_at = at + HOUR
    relocated = edit(admin, ident, preview(admin, p, ident, "relocate", move_at, b.pk), "relocate")
    old.refresh_from_db()
    assert old.active_period.upper == move_at
    moved = resolve(p, "101", move_at)[0]
    assert moved.station_id == old.station_id and moved.city_id == b.pk
    assert StationObservation.objects.get(product=p).site_epoch_id == old.pk
    assert set(
        CatalogChange.objects.filter(action_id=relocated["action_id"]).values_list(
            "change__product_id",
            "epoch_id",
        )
    ) == {(i.pk, o.pk) for i in (p, sibling) for o in (old, moved)}
    next_station = site(p, "201", parent=b, start=move_at)
    reused_at = move_at + HOUR
    assert (
        edit(
            admin,
            ident,
            preview(admin, p, ident, "reuse_code", reused_at, next_station.pk),
            "reuse_code",
        )["status"]
        == "applied"
    )
    assert resolve(p, "101", move_at)[0] == moved
    assert resolve(p, "101", reused_at)[0] == next_station
    assert resolve(other, "101", reused_at)[0] == separate
    assert StationObservation.objects.get(product=p).site_epoch_id == old.pk


def test_v08_block_crossing_window_without_rebinding(directory):
    op, p, old, ident, b, _, _, _, admin = directory
    at = START + HOUR
    ingest(op, p, [candidate(p, "101", at=at, token=1, window="hourly_right_label")])
    crossing = START + HOUR / 2
    blocked = edit(admin, ident, preview(admin, p, ident, "relocate", crossing, b.pk), "relocate")
    assert blocked["status"] == "mapping_review_required"
    key = StationObservation.objects.get(product=p)
    issue = QualityIssue.objects.get(station_key=key, code="mapping_review_required")
    assert str(issue.last_action_id) == blocked["action_id"]
    assert key.site_epoch == old and not CatalogChange.objects.exists()
    assert resolve(p, "101", at)[0] == old
    with pytest.raises(Denied, match="identity_window_crossing"):
        resolve(p, "101", at, START - HOUR, at)


def test_v08_directory_disclosure_and_independent_disposition(directory):
    op, p, old, ident, _, sibling, other, foreign_site, admin = directory
    at = START + HOUR
    assert (
        preview(admin, p, ident, "relocate", at, foreign_site.city_id, 409)["code"]
        == "precondition_conflict"
    )
    assert (
        preview(admin, p, ident, "reuse_code", at, foreign_site.pk, 409)["code"]
        == "precondition_conflict"
    )
    assert preview(admin, other, ident, "rename", at, "F foreign", 403)["code"] == "use_denied"
    alias_name = "F controlled name"
    change = edit(admin, ident, preview(admin, p, ident, "rename", at, alias_name), "rename")
    assert alias_name not in str(change)
    span = change["affected"]["scope"][0]
    detail = [
        row
        for row in changes_since(0, p.pk, Scope.from_issue(span), op.pk)
        if row["kind"] == "catalog_changed"
    ]
    assert detail[0]["directory"]["site_epoch"] == old.pk and alias_name not in str(detail)
    assert not any(
        row["kind"] == "catalog_changed"
        for row in changes_since(
            0,
            p.pk,
            Scope.from_issue(span | {"object_code": "999"}),
            op.pk,
        )
    )

    def withdraw(item):
        grant_row = item.usegrant_set.get(operation="retain_normalized", revoked_at=None)
        revoke_grant(op.pk, grant_row.pk, "F catalog disposal")
        assert not any(
            row["kind"] == "catalog_changed"
            for row in changes_since(0, item.pk, Scope.from_issue(span), op.pk)
        )
        reconcile_product(item.pk)
        assert not CatalogChange.objects.filter(change__product=item).exists()

    withdraw(p)
    assert StationIdentifier.objects.filter(station=old.station, kind="alias").exists()
    assert CatalogChange.objects.filter(change__product=sibling).exists()
    withdraw(sibling)
    assert not StationIdentifier.objects.filter(station=old.station, kind="alias").exists()
    assert StationIdentifier.objects.filter(pk=ident.pk, kind="code").exists()


@pytest.mark.parametrize("first", ["mapping", "directory"])
def test_v17_mapping_and_epoch_commit_order(directory, web_clients, sql_hook, first):
    op, p, source, _, city_b, _, _, _, mapping_web = directory
    directory_web = sign_in(web_clients(), op, PASSWORD)
    target = site(p, "201", parent=city_b)
    new_city = city(p, "103")
    ident = StationIdentifier.objects.get(station=target.station)
    at = START + HOUR
    grant(op, p, "review", fields=RAW)
    grant(op, p, "retain_normalized", fields=RAW)
    ingest(op, p, [candidate(p, "101", at=at, token=1)])
    key = StationObservation.objects.get(product=p, site_epoch=source)
    issue = QualityIssue.objects.create(product=p, station_key=key, code="mapping_review_required")
    mapping = ask(mapping_web, issue, target="201")
    mapping_payload = command(mapping, "correct_mapping")
    relocation = preview(directory_web, p, ident, "relocate", at, new_city.pk)
    waiting, held = Queue(), Queue()
    release = Event()

    def monitor(execute, sql, params, many, context):
        if " FOR UPDATE" in sql and '"observations_source"' in sql:
            waiting.put(pid())
        return execute(sql, params, many, context)

    def hold(execute, sql, params, many, context):
        if sql.startswith('INSERT INTO "accounts_governanceaction"'):
            held.put(pid())
            assert release.wait(4)
        return execute(sql, params, many, context)

    sql_hook.update({"directory-holding": hold, "directory-waiting": monitor})

    def map_post(header):
        return mapping_web.post(
            f"{QUALITY}{issue.pk}/actions/",
            json=mapping_payload,
            headers={"X-S06-Test-Hook": header},
        )

    def move_post(header):
        return directory_web.post(
            f"/api/v1/admin/stations/{ident.station_id}/changes/",
            json={
                "action": "relocate",
                "request_id": str(uuid4()),
                "reason_code": "documented_relocation",
                "expected_version": relocation["version"],
                "evidence_ref": relocation["evidence_ref"],
            },
            headers={"X-S06-Test-Hook": header},
        )

    winner, loser = (map_post, move_post) if first == "mapping" else (move_post, map_post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winning = pool.submit(winner, "directory-holding")
        holder = held.get(timeout=5)
        losing = pool.submit(loser, "directory-waiting")
        try:
            blocked_by(waiting.get(timeout=5), holder)
        finally:
            release.set()
        won = check(winning.result(timeout=5), 200)
        lost = check(losing.result(timeout=5), 200 if first == "mapping" else 409)
    key.refresh_from_db()
    if first == "mapping":
        assert won["outcome"] == "local_mapping_fix" and lost["status"] == "mapping_review_required"
        assert key.retired and resolve(p, "201", at)[0] == target
        assert StationObservation.objects.filter(product=p, site_epoch=target).count() == 1
    else:
        assert won["status"] == "applied" and lost["code"] == "precondition_conflict"
        assert not key.retired and resolve(p, "201", at)[0].city_id == new_city.pk
        assert StationObservation.objects.filter(product=p).count() == 1
