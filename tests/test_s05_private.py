"""Private research and export contracts through Session/CSRF and PostgreSQL."""

import csv
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from io import StringIO

import pytest
from accounts.models import ExportTicket, SavedQuery
from accounts.research_exports import export_fields
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.test import Client
from observations.models import CityObservation, CityRevision, Product, Receipt
from observations.policy import clock, revoke_grant
from observations.research_disposition import cleanup_expired
from s05_support import (
    ATTRIBUTION,
    HOUR,
    START,
    authorize,
    candidate,
    city,
    grant,
    ingest,
    product,
    site,
)
from test_s05_selection import DETAIL, parent, post

pytestmark = pytest.mark.django_db(transaction=True)
SAVED = "/api/v1/me/saved-queries/"
EXPORTS = "/api/v1/me/exports/"
AUTH = "/api/v1/auth/"
ANALYSIS = "/api/v1/analysis/statistics/"
SCOPE = "?scope=synthetic"


@pytest.fixture
def users(django_user_model):
    cache.clear()
    operator = django_user_model.objects.create_superuser("curator", password="curator-test-abcdef")
    user = django_user_model.objects.create_user("owner", password="owner-test-abcdef")
    other = django_user_model.objects.create_user("elsewhere", password="elsewhere-test-abcdef")
    yield operator, user, other
    cache.clear()


def refresh_csrf(web):
    web.defaults["HTTP_X_CSRFTOKEN"] = web.get(AUTH + "csrf/").json()["csrf_token"]


def login(user):
    web = Client(enforce_csrf_checks=True)
    refresh_csrf(web)
    response = post(
        web,
        AUTH + "login/",
        {"username": user.username, "password": f"{user.username}-test-abcdef"},
    )
    assert response.status_code == 200, response.content
    refresh_csrf(web)
    return web


def check(response, status):
    assert response.status_code == status, response.content[:2000]
    assert "no-store" in response.headers["Cache-Control"]
    return response.json() if status != 204 else None


def create_plan(web, query):
    return check(post(web, SAVED, query | {"title": "实验窗口"}), 201)


def read_version(web, query):
    endpoint = {"detail": DETAIL, "statistics": ANALYSIS, "compare": "/api/v1/analysis/compare/"}
    result = check(post(web, endpoint[query["method"]], query), 200)
    return result, query | {"input_version": result["input_version"]}


def prepare_csv(web, *groups, kind="detail", fields=()):
    return post(web, EXPORTS, {"kind": kind, "groups": list(groups), "fields": list(fields)})


def csv_response(web, ticket):
    return web.get(EXPORTS + ticket["id"] + "/download/" + SCOPE)


def csv_rows(web, ticket):
    response = csv_response(web, ticket)
    assert response.status_code == 200, response.content
    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert response.headers["Content-Disposition"] == 'attachment; filename="air-quality.csv"'
    return response, list(csv.DictReader(StringIO(response.content.decode())))


def test_v10_saved_crud_csrf_owner_and_target_deletion(users):
    op, owner, other = users
    web = login(owner)
    p = product("v10", "S")
    epoch = site(p)
    authorize(op, p)
    query = parent(p, [epoch], method="statistics", hours=24)
    created = create_plan(web, query)
    item = SAVED + created["id"] + "/"
    run = item + "run/" + SCOPE
    assert check(web.get(item + SCOPE), 200) == created
    assert check(web.get(SAVED + SCOPE), 200)["results"] == [created]
    assert check(post(web, run, {}), 200)["state"] == "current"
    for stranger in (other, op):
        assert check(login(stranger).get(item + SCOPE), 404) == {"code": "not_found"}
    assert check(web.get(item), 404) == {"code": "not_found"}
    stored = SavedQuery.objects.get(pk=created["id"])
    assert stored.baseline and {tuple(row["fields"]) for row in stored.baseline_basis} == {
        ("statistics_baseline",)
    }
    assert stored.intent["selection"]["site_epochs"] == []
    web.defaults.pop("HTTP_X_CSRFTOKEN")
    assert check(web.delete(item + SCOPE), 403) == {"code": "csrf_failed"}
    refresh_csrf(web)
    updated = check(
        web.patch(
            item + SCOPE, {"intent_revision": 1, "title": "新标题"}, content_type="application/json"
        ),
        200,
    )
    assert updated["intent_revision"] == 2 and updated["title"] == "新标题"
    assert check(
        web.patch(
            item + SCOPE, {"intent_revision": 1, "title": "旧版本"}, content_type="application/json"
        ),
        409,
    ) == {"code": "input_changed"}
    epoch.delete()
    assert check(post(web, run, {}), 200) == {"state": "target_unavailable"}
    assert check(web.get(item + SCOPE), 200)["selection"]["site_epochs"] == [None]
    check(web.delete(item + SCOPE), 204)
    assert check(web.get(item + SCOPE), 404) == {"code": "not_found"}


def test_v10_sessions_scope_and_method(users):
    op, owner, _ = users
    web, second = login(owner), login(owner)
    p = product("v10-method", "C")
    a = city(p)
    grants = authorize(op, p)
    item = create_plan(web, parent(p, [a], method="statistics"))["id"]
    url = SAVED + item + "/run/" + SCOPE
    assert post(web, AUTH + "logout/", {}).status_code == 204
    assert check(post(web, url, {}), 403) == {"code": "not_authenticated"}
    assert check(post(second, url, {}), 200)["state"] == "current"
    p.collection_pause = "F source pause"
    p.save(update_fields=["collection_pause"])
    assert check(post(second, url, {}), 200)["state"] == "current"
    row = SavedQuery.objects.get(pk=item)
    row.intent["method_version"] = "old-test-method"
    row.save(update_fields=["intent"])
    assert check(post(second, url, {}), 200)["state"] == "method_changed"
    revoke_grant(op.pk, grants["analyze"].pk, "research intentionally disabled")
    assert check(post(second, url, {}), 403) == {"code": "use_denied"}
    check(second.delete(SAVED + item + "/" + SCOPE), 204)


def test_v11_csv_roundtrip_and_distinct_grants(users):
    op, owner, other = users
    web, outsider = login(owner), login(other)
    detail_opts = ("qualification_version",)
    aggregate_opts = ("median", "minimum", "maximum")
    p = product("v11-a", "C")
    p.attribution = ' \ufeff\t＝SUM(2,3), "引号"\n第二行'
    p.save(update_fields=["attribution"])
    a = city(p)
    authorize(op, p, detail_fields=export_fields("detail", detail_opts))
    ingest(
        op, p, [candidate(p, at=START + i * HOUR, value=v) for i, v in enumerate([0, "<3", "NA"])]
    )
    result, group = read_version(web, parent(p, [a], hours=3))
    ticket = check(prepare_csv(web, group, fields=detail_opts), 201)
    csv_file, rows = csv_rows(web, ticket)
    assert len(rows) == 3
    assert [row["quality"] for row in rows] == ["valid", "censored", "missing"]
    assert Decimal(rows[0]["value"]) == 0 and rows[1]["value"] == ""
    assert Decimal(rows[1]["censor_limit"]) == 3 and rows[1]["censor_direction"] == "<"
    assert all(
        "'" == row["attribution"][0] and '"引号"\n第二行' in row["attribution"] for row in rows
    )
    assert [int(row["revision"]) for row in rows] == [row["revision"] for row in result["results"]]
    assert all(row["input_version"] == result["input_version"] for row in rows)
    meta = ExportTicket.objects.get(pk=ticket["id"])
    assert (meta.row_count, meta.byte_count, meta.data_sha256) == (
        3,
        len(csv_file.content),
        sha256(csv_file.content).hexdigest(),
    )
    assert "Content-Disposition" not in csv_response(outsider, ticket).headers
    assert check(csv_response(outsider, ticket), 404) == {"code": "not_found"}
    assert check(prepare_csv(web, group, fields=["source_description"]), 400) == {
        "code": "invalid_request"
    }
    assert check(prepare_csv(web, group, fields=["report_kind"]), 403) == {"code": "use_denied"}
    stats, group_stats = read_version(web, parent(p, [a], method="statistics", hours=3))
    assert check(prepare_csv(web, group_stats, kind="aggregate"), 403) == {"code": "use_denied"}

    q = product("v11-b", "C")
    q.attribution = "F-B 独立来源"
    q.save(update_fields=["attribution"])
    b = city(q)
    authorize(op, q, aggregate_fields=export_fields("aggregate", aggregate_opts))
    ingest(op, q, [candidate(q, at=START + i * HOUR, value=i) for i in range(3)])
    stats, q_statistics = read_version(web, parent(q, [b], method="statistics", hours=3))
    aggregate = check(prepare_csv(web, q_statistics, kind="aggregate", fields=aggregate_opts), 201)
    _, summarized = csv_rows(web, aggregate)
    assert len(summarized) == 1 and "value" not in summarized[0] and "key" not in summarized[0]
    assert [int(summarized[0][name]) for name in ("E", "O", "V")] == [3, 3, 3]
    assert all(
        Decimal(summarized[0][field]) == Decimal(stats["buckets"][0][field])
        for field in ("mean", *aggregate_opts)
    )
    assert ATTRIBUTION in summarized[0]["attribution"]
    _, q_detail = read_version(web, parent(q, [b], hours=3))
    assert check(prepare_csv(web, group, q_detail), 403) == {"code": "use_denied"}
    q_export = grant(op, q, "export", fields=export_fields("detail"))
    combined = check(prepare_csv(web, group, q_detail), 201)
    _, data = csv_rows(web, combined)
    assert [r["group"] for r in data] == ["1", "2"] * 3
    assert {r["source"] for r in data} == {p.source.namespace, q.source.namespace}
    assert all(
        (p.attribution in r["attribution"], q.attribution in r["attribution"])
        == (r["group"] == "1", r["group"] == "2")
        for r in data
    )
    revoke_grant(op.pk, q_export.pk, "withdraw second group")
    rejected = csv_response(web, combined)
    assert check(rejected, 403) == {"code": "use_denied"}
    assert "Content-Disposition" not in rejected.headers


def test_v11_aggregate_csv_round_trips_negative_difference_as_a_number(users):
    op, owner, _ = users
    web = login(owner)
    p = product("signed-difference", "C")
    a, b = city(p, "001"), city(p, "002")
    fields = ["mean_difference"]
    authorize(op, p, aggregate_fields=export_fields("aggregate", fields))
    ingest(op, p, [candidate(p, "001", value=0), candidate(p, "002", value=9)])
    report, group = read_version(web, parent(p, [a, b], method="compare", hours=1))
    assert report["mean_difference"] == -9
    ticket = check(prepare_csv(web, group, kind="aggregate", fields=fields), 201)
    _, rows = csv_rows(web, ticket)
    paired = [r for r in rows if r["P"] == "1"]
    assert [(Decimal(r["mean"]), Decimal(r["mean_difference"])) for r in paired] == [
        (0, -9),
        (9, -9),
    ]


def test_v13_same_content_watermark_and_later_correction(users):
    op, owner, _ = users
    web = login(owner)
    p = product("v13", "C")
    a, b = city(p, "001"), city(p, "002")
    authorize(op, p, detail_fields=export_fields("detail"))
    ingest(op, p, [candidate(p, "001", value=10, token=1), candidate(p, "002", value=20, token=1)])
    query = parent(p, [a, b], method="compare", hours=1)
    plan = create_plan(web, query)
    version1, group1 = read_version(web, query)
    run = SAVED + plan["id"] + "/run/" + SCOPE
    ticket1 = check(prepare_csv(web, group1), 201)

    def reconcile(report, value, watermark):
        assert [(r["E"], r["O"], r["V"]) for r in report["columns"]] == [(1, 1, 1)] * 2
        assert report["E_common"] == report["P"] == 1
        assert {r["entity"]: r["mean"] for r in report["ranking"]} == {a.pk: value, b.pk: 20}
        page = check(
            post(
                web, DETAIL, query | {"input_version": report["input_version"], "support": "paired"}
            ),
            200,
        )
        assert [(r["entity"], Decimal(r["value"])) for r in page["results"]] == [
            (a.pk, value),
            (b.pk, 20),
        ]
        key = CityObservation.objects.get(product=p, city=a)
        current = CityRevision.objects.get(key=key, is_current=True)
        first = page["results"][0]
        assert (first["key"], first["revision"], first["qualification_version"]) == (
            key.pk,
            current.pk,
            key.qualification_version,
        )
        assert int(key.watermark) == watermark and current.value == value
        return key.pk, current.pk, key.qualification_version

    original = reconcile(version1, 10, 1)
    ingest(op, p, [candidate(p, "001", value=10, token=3)])
    version3, group3 = read_version(web, query)
    assert version3["input_version"] != version1["input_version"]
    assert reconcile(version3, 10, 3) == original
    assert check(csv_response(web, ticket1), 409) == {"code": "input_changed"}
    revisited = check(post(web, run, {}), 200)
    assert (revisited["state"], revisited["result"]["input_version"]) == (
        "current",
        version3["input_version"],
    )
    ticket3 = check(prepare_csv(web, group3), 201)
    ingest(op, p, [candidate(p, "001", value=20, token=2)])
    assert (
        Receipt.objects.filter(city_revision_id=original[1], source_token=2, outcome="late").count()
        == 1
    )
    late = check(post(web, run, {}), 200)
    assert (late["state"], late["result"]["input_version"]) == (
        "current",
        version3["input_version"],
    )
    assert reconcile(late["result"], 10, 3) == original
    _, rows = csv_rows(web, ticket3)
    assert [Decimal(r["value"]) for r in rows] == [Decimal(10), Decimal(20)]
    assert all(row["input_version"] == version3["input_version"] for row in rows)
    ingest(op, p, [candidate(p, "001", value=30, token=4)])
    changed = check(post(web, run, {}), 200)
    assert (
        changed["state"] == "data_changed"
        and changed["result"]["input_version"] != version3["input_version"]
    )
    assert reconcile(changed["result"], 30, 4)[1] != original[1]
    assert check(csv_response(web, ticket3), 409) == {"code": "input_changed"}


def test_v18_cleanup_expired_metadata(users):
    op, owner, _ = users
    web = login(owner)
    p = product("v18", "C")
    a = city(p)
    authorize(op, p, detail_fields=export_fields("detail"))
    ingest(op, p, [candidate(p)])
    _, group = read_version(web, parent(p, [a]))
    ticket = check(prepare_csv(web, group), 201)
    now = clock()
    ExportTicket.objects.filter(pk=ticket["id"]).update(
        created_at=now - timedelta(seconds=2), expires_at=now - timedelta(seconds=1)
    )
    assert cleanup_expired() == (1, 0)
    assert check(csv_response(web, ticket), 404) == {"code": "not_found"}
    assert not ExportTicket.objects.exists()


def reject(response, status=403, code="use_denied"):
    assert check(response, status) == {"code": code}
    assert "Content-Disposition" not in response


def test_export_prepare_cannot_probe_restricted_product_lock(users):
    op, owner, _ = users
    web = login(owner)
    p = product("preflight-restricted", "C")
    target = city(p)
    authorize(op, p)
    _, group = read_version(web, parent(p, [target]))
    missing = deepcopy(group)
    missing["selection"]["product"] += 10_000

    def responses():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                reader_pid = cursor.fetchone()[0]
            return reader_pid, prepare_csv(web, group), prepare_csv(web, missing)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool, transaction.atomic():
        Product.objects.select_for_update().get(pk=p.pk)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            writer_pid = cursor.fetchone()[0]
        reader_pid, restricted, absent = pool.submit(responses).result(timeout=5)
        assert reader_pid != writer_pid
        reject(restricted)
        reject(absent)


def test_export_authorizes_every_group_before_compatibility_and_versions(users):
    op, owner, _ = users
    web = login(owner)
    allowed, restricted = product("preflight-allowed", "C"), product("preflight-denied", "C")
    a, b = city(allowed), city(restricted)
    authorize(op, allowed, detail_fields=export_fields("detail"))
    authorize(op, restricted)
    _, visible = read_version(web, parent(allowed, [a]))
    _, hidden = read_version(web, parent(restricted, [b]))
    incompatible = deepcopy(visible)
    incompatible["selection"]["units"] = {"CO": "µg/m³"}
    reject(prepare_csv(web, incompatible), 422, "incompatible_series")
    for changed in (incompatible, visible | {"method_version": "old-method"}):
        reject(prepare_csv(web, changed, hidden))
        reject(prepare_csv(web, hidden, changed))


@pytest.mark.parametrize("field", ["format", "method", "compatibility"])
def test_export_download_prioritizes_current_use_over_frozen_versions(users, field):
    op, owner, other = users
    web, outsider = login(owner), login(other)
    allowed, expiring = product("download-allowed", "C"), product("download-denied", "C")
    a, b = city(allowed), city(expiring)
    authorize(op, allowed, detail_fields=export_fields("detail"))
    rights = authorize(op, expiring, detail_fields=export_fields("detail"))
    ingest(op, allowed, [candidate(allowed, token=1)])
    ingest(op, expiring, [candidate(expiring, token=1)])
    _, first = read_version(web, parent(allowed, [a]))
    _, second = read_version(web, parent(expiring, [b]))
    tickets = [
        check(prepare_csv(web, *groups), 201) for groups in ((first, second), (second, first))
    ]
    for info in tickets:
        ticket = ExportTicket.objects.get(pk=info["id"])
        group = next(g for g in ticket.groups if g["selection"]["product"] == allowed.pk)
        if field == "format":
            ticket.format_version = "old-format"
        elif field == "method":
            group["method_version"] = "old-method"
        else:
            group["selection"]["units"] = {"CO": "µg/m³"}
        ticket.save(update_fields=["format_version", "groups"])
        if field == "compatibility":
            reject(csv_response(web, info), 422, "incompatible_series")
        else:
            reject(csv_response(web, info), 409, "method_changed")
        reject(csv_response(outsider, info), 404, "not_found")

    # Simulate time advancing past a required grant's boundary without eager invalidation.
    # The existing ticket metadata still contains its previous boundary and must be rechecked.
    rights["export"].valid_until = clock()
    rights["export"].save(update_fields=["valid_until"])
    for info in tickets:
        assert ExportTicket.objects.get(pk=info["id"]).invalidated_at is None
        reject(csv_response(web, info))
