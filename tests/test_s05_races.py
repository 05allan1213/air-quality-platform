"""V14 interleavings on independent PostgreSQL backends and DB-backed HTTP sessions."""

import csv
import hashlib
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest
from accounts import research_exports as exports
from accounts.models import ExportTicket
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.db import connection, connections, transaction
from observations.catalog import relocate
from observations.policy import clock, revoke_grant
from s05_http import loopback
from s05_support import HOUR, START, authorize, candidate, grant, ingest, product, site
from test_s05_selection import DETAIL, parent, post, session

pytestmark = pytest.mark.django_db(transaction=True)
EXPORT = "/api/v1/me/exports/"


def version(web, query):
    response = post(web, DETAIL, query)
    assert response.status_code == 200, response.content[:150]
    return response.json()["input_version"]


def prepare(web, query, stamp):
    return post(web, EXPORT, {"kind": "detail", "groups": [query | {"input_version": stamp}]})


def location(ticket_id):
    return f"{EXPORT}{ticket_id}/download/?scope=synthetic"


@pytest.fixture
def state(django_user_model):
    op = django_user_model.objects.create_superuser("race-curator")
    reader = django_user_model.objects.create_user("race-user", password="s05-test-only")
    permission = Permission.objects.get(
        content_type__app_label="observations", codename="review_observation"
    )
    reader.user_permissions.add(permission)
    p = product("race")
    target = site(p, "101")
    grants = authorize(op, p, detail_fields=exports.export_fields("detail"))
    ingest(op, p, [candidate(p, "101", token=1)])
    web = session(reader)
    query = parent(p, [target], hours=6)
    stamp = version(web, query)
    return op, reader, web, p, target, grants, query, stamp


def csv_matches(response, ticket, expected_version):
    assert response.status_code == 200, response.content[:150]
    assert response.headers["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(StringIO(response.content.decode(), newline="")))
    assert len(rows) == ticket.row_count == 1
    assert Decimal(rows[0]["value"]) == 10
    assert rows[0]["input_version"] == expected_version
    assert hashlib.sha256(response.content).hexdigest() == ticket.data_sha256
    assert len(response.content) == ticket.byte_count


def rejection(response, status, code):
    assert (response.status_code, response.json()) == (status, {"code": code})
    assert "Content-Disposition" not in response.headers


# Fixed detail format and one full-day, unexpiring allow per purpose in the race fixture.
DETAIL_COLUMNS = (
    "group",
    "data_scope",
    "source",
    "product",
    "attribution",
    "generated_at",
    "source_timezone",
    "indicator",
    "unit",
    "window_definition",
    "standard_series",
    "input_version",
    "method",
    "method_version",
    "processing",
    "entity",
    "observed_at",
    "value",
    "quality",
    "censor_direction",
    "censor_limit",
    "key",
    "revision",
    "fetched_at",
)


def capture_export_snapshot(state, web):
    _, _, _, product, target, _, query, _ = state
    selection = query["selection"]
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT k.id, r.id, k.observed_at_utc, r.value, r.quality, r.unit,
                      k.window_definition, k.standard_series, b.fetched_at
                 FROM observations_stationobservation k
                 JOIN observations_stationrevision r ON r.key_id = k.id AND r.is_current
                 JOIN observations_receipt rc ON rc.station_revision_id = r.id
                 JOIN observations_batch b ON b.id = rc.batch_id
                WHERE k.product_id = %s AND k.site_epoch_id = %s AND k.indicator = 'CO'
                  AND k.observed_at_utc >= %s AND k.observed_at_utc < %s
                  AND rc.outcome IN ('first', 'content_changed') AND b.product_id = %s
                  AND NOT b.redacted
                ORDER BY k.observed_at_utc""",
            [product.pk, target.pk, selection["start"], selection["end"], product.pk],
        )
        rows = cursor.fetchall()
        cursor.execute(
            """SELECT operation, id, audience, fields, attribution
                 FROM observations_usegrant WHERE product_id = %s
                  AND operation IN ('export', 'retain_normalized') AND effect = 'allow'
                  AND revoked_at IS NULL AND valid_from <= clock_timestamp()
                  AND valid_until IS NULL AND retain_until IS NULL
                  AND data_from <= %s AND data_until >= %s""",
            [product.pk, START.date(), (START + 24 * HOUR).date()],
        )
        grants = {row[0]: row[1:] for row in cursor.fetchall()}
    details = web.post(DETAIL, json=query)
    assert details.status_code == 200
    detail = details.json()
    assert [(r["key"], r["revision"], Decimal(r["value"])) for r in detail["results"]] == [
        (key, revision, value) for key, revision, _, value, *_ in rows
    ]
    return detail["input_version"], rows, grants


def assert_frozen_snapshot(state, ticket, snapshot):
    _, owner, _, product, target, _, query, _ = state
    stamp, rows, grants = snapshot
    manifest = []
    required = (
        (
            "retain_normalized",
            "platform",
            {
                "catalog",
                "export_manifest",
                "normalized",
                "provenance",
                *("csv_" + c for c in DETAIL_COLUMNS),
            },
        ),
        (
            "export",
            "local_authenticated",
            {"export_manifest", *("csv_" + c for c in DETAIL_COLUMNS)},
        ),
    )
    for operation, audience, fields in required:
        grant_id, registered_audience, granted_fields, credit = grants[operation]
        assert registered_audience == audience and fields.issubset(json.loads(granted_fields))
        manifest.append(
            {
                "operation": operation,
                "audience": audience,
                "fields": sorted(fields),
                "product": product.pk,
                "ids": [grant_id],
                "grant_until": None,
                "until": None,
                "attributions": [credit],
                "scope": {
                    "geography": target.city.geography,
                    "object_code": "101",
                    "indicator": "CO",
                    "start": START.date().isoformat(),
                    "end": (START + 24 * HOUR).date().isoformat(),
                },
            }
        )
    attribution = "\n".join(sorted({product.attribution, *(r[3] for r in grants.values())}))
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(DETAIL_COLUMNS)
    for key, revision, observed_at, value, quality, unit, window, standard, fetched_at in rows:
        writer.writerow(
            (
                1,
                "synthetic",
                product.source.namespace,
                product.pk,
                attribution,
                ticket.created_at.isoformat(),
                product.source_timezone,
                "CO",
                unit,
                window,
                standard,
                stamp,
                query["method"],
                query["method_version"],
                "normalized observation; quality and censor fields retain source semantics",
                target.pk,
                observed_at.isoformat(),
                value,
                quality,
                "",
                "",
                key,
                revision,
                fetched_at.isoformat(),
            )
        )
    body = output.getvalue().encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()
    signature_input = [
        "s05-export-projection",
        [
            owner.pk,
            "detail",
            "s05.csv.v2",
            list(DETAIL_COLUMNS),
            [{k: v for k, v in row.items() if k != "until"} for row in manifest],
            checksum,
        ],
    ]
    canonical = json.dumps(
        signature_input, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    signature = hmac.digest(settings.SECRET_KEY.encode(), canonical.encode(), "sha256").hex()
    assert ticket.input_versions == [stamp]
    assert ticket.basis == manifest
    assert ticket.fields == list(DETAIL_COLUMNS)
    assert ticket.row_count == len(rows)
    assert ticket.byte_count == len(body)
    assert ticket.data_sha256 == checksum
    assert ticket.projection_signature == signature
    return body


def blocked_by(waiter, holder):
    deadline = monotonic() + 0.85
    while monotonic() < deadline:
        with connection.cursor() as cur:
            cur.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder, waiter])
            if cur.fetchone()[0]:
                return
        sleep(0.008)
    raise AssertionError("Expected wait was not visible in pg_blocking_pids")


def pid():
    with connection.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        return cur.fetchone()[0]


def in_thread(func):
    try:
        return func()
    finally:
        connections.close_all()


def record(case, **data):
    root = os.environ.get("S05_EVIDENCE_DIR")
    if root:
        path = Path(root) / "V14"
        path.mkdir(exist_ok=True)
        (path / f"{case}.json").write_text(json.dumps(data, sort_keys=True) + "\n")


def apply_change(state, change, ticket_id, *, session_key=None):
    op, user, web, p, target, grants, _, _ = state
    if change == "revision":
        ingest(op, p, [candidate(p, "101", value="30", token=4)])
    elif change == "fill":
        ingest(op, p, [candidate(p, "101", at=START + HOUR, value="30", token=1)])
    elif change == "relocate":
        relocate(
            target.station,
            START + 3 * HOUR,
            city=target.city,
            name="new F site",
            certainty="confirmed",
            location_evidence="S05 F",
        )
    elif change == "withdraw":
        revoke_grant(op.pk, grants["export"].pk, "V14 F withdrawal")
    elif change == "deny":
        grant(op, p, "export", object_code="101", fields=["*"], effect="deny")
    elif change == "logout":
        Session.objects.filter(
            session_key=session_key or web.cookies[settings.SESSION_COOKIE_NAME].value
        ).delete()
    elif change == "password":
        user.set_unusable_password()
        user.save(update_fields=["password"])
    elif change == "disable":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif change == "remove_review":
        # S05 ordinary-user CSV has no dependency on review_observation.
        user.user_permissions.clear()
    elif change == "cancel":
        ExportTicket.objects.filter(pk=ticket_id).delete()
    else:
        raise AssertionError(change)


CHANGES = (
    "revision",
    "fill",
    "relocate",
    "withdraw",
    "deny",
    "logout",
    "password",
    "disable",
    "remove_review",
)
CASES = [(action, change) for action in ("prepare", "download") for change in CHANGES] + [
    ("download", "cancel")
]


@pytest.fixture
def live_race(state):
    _, reader, *_ = state
    watching, started = Event(), Queue()

    def before_sql(sql):
        if watching.is_set() and "FOR UPDATE" in sql:
            started.put(connection.connection.info.backend_pid)
            watching.clear()

    with loopback(reader, before_sql=before_sql) as (client, _):
        yield client, watching, started


@pytest.mark.parametrize(("action", "change"), CASES)
@pytest.mark.parametrize("first", ["reader", "writer"])
def test_v14_serializes_real_writes(state, live_race, monkeypatch, action, change, first):
    *_, query, stamp = state
    web, watching, started = live_race
    session_key = web.cookies.get(settings.SESSION_COOKIE_NAME)
    payload = {"kind": "detail", "groups": [query | {"input_version": stamp}]}
    old_ticket = None
    if action == "download":
        prepared = web.post(EXPORT, json=payload)
        assert prepared.status_code == 201, prepared.content
        old_ticket = ExportTicket.objects.get(pk=prepared.json()["id"])
    before = ExportTicket.objects.count()
    check_snapshot = action == "prepare" and change in {"revision", "fill"}
    before_snapshot = capture_export_snapshot(state, web) if check_snapshot else None
    snapshot_evidence = {}

    def fn():
        return (
            web.post(EXPORT, json=payload)
            if action == "prepare"
            else web.get(location(old_ticket.pk))
        )

    resource = old_ticket.pk if old_ticket else None
    # Only the write of an irrelevant management permission bypasses the ordinary-user locks.
    independent = change == "remove_review"
    with ThreadPoolExecutor(max_workers=2) as pool:
        if first == "reader":
            ready, release, writer_pid = Queue(), Event(), Queue()
            original = exports.encode_csv

            def hold_encoding(*args, **kwargs):
                ready.put(pid())
                assert release.wait(4)
                return original(*args, **kwargs)

            monkeypatch.setattr(exports, "encode_csv", hold_encoding)
            read = pool.submit(in_thread, fn)
            holder = ready.get(timeout=5)

            def write():
                with transaction.atomic():
                    writer_pid.put(pid())
                    apply_change(state, change, resource, session_key=session_key)

            writing = pool.submit(in_thread, write)
            waiter = writer_pid.get(timeout=5)
            try:
                if independent:
                    writing.result(timeout=2)
                else:
                    blocked_by(waiter, holder)
            finally:
                release.set()
            response = read.result(timeout=6)
            writing.result(timeout=5)
        else:
            with transaction.atomic():
                holder = pid()
                apply_change(state, change, resource, session_key=session_key)
                watching.set()
                read = pool.submit(fn)
                waiter = started.get(timeout=5)
                if not independent:
                    blocked_by(waiter, holder)
            response = read.result(timeout=6)
    expected = {
        "revision": (409, "input_changed"),
        "fill": (409, "input_changed"),
        "relocate": (409, "input_changed"),
        "withdraw": (403, "use_denied"),
        "deny": (403, "use_denied"),
        "logout": (403, "not_authenticated"),
        "password": (403, "not_authenticated"),
        "disable": (403, "not_authenticated"),
        "cancel": (404, "not_found"),
    }
    if first == "writer" and not independent:
        rejection(response, *expected[change])
        if action == "prepare":
            assert ExportTicket.objects.count() == before
        if check_snapshot:
            after_snapshot = capture_export_snapshot(state, web)
            assert after_snapshot[0] != before_snapshot[0]
            assert [r[3] for r in after_snapshot[1]] == ([30] if change == "revision" else [10, 30])
            after_payload = {
                "kind": "detail",
                "groups": [query | {"input_version": after_snapshot[0]}],
            }
            signed = web.post(EXPORT, json=after_payload)
            assert signed.status_code == 201
            assert ExportTicket.objects.count() == before + 1
            ticket = ExportTicket.objects.get(pk=signed.json()["id"])
            expected_csv = assert_frozen_snapshot(state, ticket, after_snapshot)
            downloaded = web.get(location(ticket.pk))
            assert downloaded.status_code == 200 and downloaded.content == expected_csv
            snapshot_evidence = {"snapshot": "post-write", "fresh_http": signed.status_code}
    elif action == "download":
        csv_matches(response, old_ticket, stamp)
    else:
        assert response.status_code == 201, response.content[:120]
        frozen = ExportTicket.objects.get(pk=response.json()["id"])
        assert frozen.input_versions == [stamp] and frozen.row_count == 1 and frozen.byte_count > 0
        if check_snapshot:
            assert before_snapshot[0] == stamp and [r[3] for r in before_snapshot[1]] == [10]
            assert_frozen_snapshot(state, frozen, before_snapshot)
            assert web.post(DETAIL, json=query).json()["input_version"] != stamp
            snapshot_evidence = {"snapshot": "pre-write"}
    if snapshot_evidence:
        snapshot_evidence.update(
            resource="product row",
            commit_order=[first, "writer" if first == "reader" else "fresh prepare"],
            checks=[
                "input_version",
                "grants",
                "fields",
                "lineage",
                "rows",
                "bytes",
                "sha256",
                "hmac",
            ],
        )
    record(
        f"{action}-{first}-{change}",
        operation=action,
        transport="loopback HTTP, database Session and CSRF",
        first=first,
        mutation=change,
        pg_blocking_pids=not independent,
        distinct_pids=waiter != holder,
        http=response.status_code,
        tickets_before=before,
        tickets_after=ExportTicket.objects.count(),
        **snapshot_evidence,
    )


@pytest.mark.parametrize("action", ["prepare", "download"])
@pytest.mark.parametrize("timeout", ["lock", "statement", "encode"])
def test_v14_deadlines_rollback_before_any_bytes(state, monkeypatch, action, timeout):
    _, _, web, p, _, _, query, stamp = state
    old = prepare(web, query, stamp)
    assert old.status_code == 201

    def fn():
        return (
            prepare(web, query, stamp)
            if action == "prepare"
            else web.get(location(old.json()["id"]))
        )

    count = ExportTicket.objects.count()
    start = monotonic()
    if timeout == "lock":
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                type(p).objects.select_for_update().get(pk=p.pk)
                response = pool.submit(in_thread, fn).result(timeout=4)
    else:
        original = exports.encode_csv

        def slow(*args, **kwargs):
            if timeout == "statement":
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_sleep(8)")
            else:
                # The encoder checks the real monotonic deadline between bounded chunks.
                sleep(10.02)
            return original(*args, **kwargs)

        monkeypatch.setattr(exports, "encode_csv", slow)
        response = fn()
    elapsed = monotonic() - start
    rejection(response, 503, "database_unavailable")
    upper = {"lock": 2.5, "statement": 6.5, "encode": 11.3}[timeout]
    assert elapsed < upper and ExportTicket.objects.count() == count
    record(f"{action}-{timeout}-timeout", http=503, seconds=round(elapsed, 3), tickets=count)


@pytest.mark.parametrize("action", ["prepare", "download"])
@pytest.mark.parametrize("boundary", ["grant", "future_deny", "ttl"])
def test_v14_last_pg_clock_after_encoding(state, monkeypatch, action, boundary):
    op, _, web, p, _, grants, query, stamp = state
    from datetime import timedelta

    # Install actual grant boundaries through the controlled registration service.
    deadline = clock() + timedelta(seconds=1.0)
    if boundary == "grant":
        revoke_grant(op.pk, grants["export"].pk, "time-limited fixture")
        grant(op, p, "export", fields=exports.export_fields("detail"), valid_until=deadline)
    elif boundary == "future_deny":
        grant(op, p, "export", object_code="101", fields=["*"], effect="deny", valid_from=deadline)
    else:
        monkeypatch.setattr(exports, "TICKET_TTL", timedelta(seconds=1))
    old = None
    if action == "download":
        old = prepare(web, query, stamp)
        assert old.status_code == 201, old.content
        deadline = ExportTicket.objects.get(pk=old.json()["id"]).expires_at
    before = ExportTicket.objects.count()
    original = exports.encode_csv

    def cross(*args, **kwargs):
        body = original(*args, **kwargs)
        # Sleep until the actual PG clock passed the planned boundary, while locks stay held.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_sleep(GREATEST(0,extract(epoch FROM (%s-clock_timestamp())))+0.22)",
                [deadline],
            )
        return body

    monkeypatch.setattr(exports, "encode_csv", cross)
    response = prepare(web, query, stamp) if old is None else web.get(location(old.json()["id"]))
    rejection(
        response,
        410 if boundary == "ttl" else 403,
        "expired" if boundary == "ttl" else "use_denied",
    )
    assert before == ExportTicket.objects.count()
    record(f"{action}-{boundary}-boundary", http=response.status_code, tickets=before)


def test_v14_transient_deny_never_resurrects_ticket(state):
    op, _, web, p, _, _, query, stamp = state
    first = prepare(web, query, stamp).json()["id"]
    foreign = grant(op, p, "export", effect="deny", fields=["*"], object_code="somewhere-else")
    assert web.get(location(first)).status_code == 200
    revoke_grant(op.pk, foreign.pk, "unrelated")
    stop = grant(op, p, "export", effect="deny", fields=["*"], object_code="101")
    revoke_grant(op.pk, stop.pk, "restored")
    rejection(web.get(location(first)), 403, "use_denied")
    assert prepare(web, query, stamp).status_code == 201


def test_v14_writer_first_fresh_version_prepares_one_new_snapshot(state):
    op, _, web, p, _, _, query, stamp = state
    ingest(op, p, [candidate(p, "101", value="30", token=4)])
    new_stamp = version(web, query)
    assert new_stamp != stamp
    result = prepare(web, query, new_stamp)
    assert result.status_code == 201
    new_ticket = ExportTicket.objects.get(pk=result.json()["id"])
    response = web.get(location(new_ticket.pk))
    assert response.status_code == 200
    (row,) = csv.DictReader(StringIO(response.content.decode(), newline=""))
    assert Decimal(row["value"]) == 30 and row["input_version"] == new_stamp
    assert hashlib.sha256(response.content).hexdigest() == new_ticket.data_sha256


def test_v18_dispose_scoped_manifest_and_saved_baseline(state):
    from accounts.models import SavedQuery
    from observations.disposition import reconcile_product

    op, user, web, p, target, grants, query, stamp = state

    def saved(p, target):
        body = parent(p, [target], method="statistics", hours=6) | {"title": "F intent"}
        response = post(web, "/api/v1/me/saved-queries/", body)
        assert response.status_code == 201, response.content
        return SavedQuery.objects.get(pk=response.json()["id"])

    first = saved(p, target)
    first_ticket = prepare(web, query, stamp).json()["id"]
    q = product("v18-unaffected", "S")
    b = site(q, "201")
    authorize(op, q, detail_fields=exports.export_fields("detail"))
    ingest(op, q, [candidate(q, "201")])
    second = saved(q, b)
    other = parent(q, [b], hours=6)
    second_ticket = prepare(web, other, version(web, other)).json()["id"]
    assert first.baseline and first.baseline_basis
    revoke_grant(op.pk, grants["display"].pk, "display only")
    first.refresh_from_db()
    assert first.baseline
    assert web.get(location(first_ticket)).status_code == 200
    revoke_grant(op.pk, grants["retain_normalized"].pk, "retention withdrawal")
    reconcile_product(p.pk)
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.baseline, first.baseline_basis, first.baseline_until) == ("", [], None)
    assert first.title == "F intent" and first.owner == user
    assert second.baseline and web.get(location(second_ticket)).status_code == 200
    rejection(web.get(location(first_ticket)), 404, "not_found")


def test_v14_two_sessions_recheck_password_and_logout_via_http(state):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import make_password

    _, owner, _, _, _, _, query, _ = state
    initial = "test-only-F-initial2024"
    new = "test-only-F-updated2024"
    get_user_model().objects.filter(pk=owner.pk).update(password=make_password(initial))
    owner.refresh_from_db()
    first, second = session(owner), session(owner)
    ticket = prepare(first, query, version(first, query)).json()["id"]
    assert post(first, "/api/v1/auth/logout/", {}).status_code == 204
    rejection(first.get(location(ticket)), 403, "not_authenticated")
    assert second.get(location(ticket)).status_code == 200
    third = session(owner)
    r = post(second, "/api/v1/auth/password/", {"old_password": initial, "new_password": new})
    assert r.status_code == 204, r.content[:220]
    for web in (second, third):
        rejection(web.get(location(ticket)), 403, "not_authenticated")


@pytest.mark.parametrize("first", ["run", "mutation"])
@pytest.mark.parametrize("mutation", ["patch", "delete"])
def test_v14_owned_intent_is_one_locked_edition(state, monkeypatch, first, mutation):

    _, _, web, _, target, _, detail, stamp = state
    regular = detail | {"method": "statistics"}
    base = "/api/v1/me/saved-queries/"
    created = post(web, base, regular | {"title": "original"})
    assert created.status_code == 201, created.content
    item = base + created.json()["id"] + "/"
    snap = prepare(web, detail, stamp).json()["id"]

    def run():
        return post(web, item + "run/?scope=synthetic", {})

    def mutate():
        if mutation == "delete":
            return web.delete(item + "?scope=synthetic")
        change = parent(
            target.station.source.product_set.get(code="race"),
            [target],
            method="statistics",
            hours=3,
        )
        return web.patch(
            item + "?scope=synthetic",
            change | {"title": "shorter", "intent_revision": 1},
            content_type="application/json",
        )

    # Pause the lock owner inside its HTTP transaction. Both threads still execute their full requests.
    ready, release, waiting = Queue(), Event(), Queue()
    owner_first = run if first == "run" else mutate
    second = mutate if first == "run" else run

    def hold():
        def wrap(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'accounts_savedquery"' in sql and "FOR UPDATE" in sql and not release.is_set():
                ready.put(connection.connection.info.backend_pid)
                assert release.wait(4)
            return result

        with connection.execute_wrapper(wrap):
            return owner_first()

    def observe():
        def wrap(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and waiting.empty():
                waiting.put(connection.connection.info.backend_pid)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrap):
            return second()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(in_thread, hold)
        holder = ready.get(timeout=5)
        b = pool.submit(in_thread, observe)
        waiter = waiting.get(timeout=5)
        try:
            blocked_by(waiter, holder)
        finally:
            release.set()
        first_response, second_response = a.result(timeout=6), b.result(timeout=6)
    result = first_response if first == "run" else second_response
    edited = second_response if first == "run" else first_response
    assert edited.status_code == (204 if mutation == "delete" else 200), edited.content
    if first == "run":
        assert result.status_code == 200 and result.json()["intent_revision"] == 1
    elif mutation == "delete":
        rejection(result, 404, "not_found")
    else:
        rejection(result, 409, "input_changed")
    assert web.get(location(snap)).status_code == 200
    record(
        f"saved-{first}-{mutation}",
        pg_blocking_pids=True,
        first=first,
        writer_http=edited.status_code,
        run_http=result.status_code,
    )


def test_v14_reverse_product_order_completes_without_deadlock(state):
    op, reader, web, p, target, _, _, _ = state
    second_product = product("race-other")
    other = site(second_product, "201")
    authorize(op, second_product, detail_fields=exports.export_fields("detail"))
    ingest(op, second_product, [candidate(second_product, "201")])
    groups = []
    for prod, entity in ((p, target), (second_product, other)):
        body = parent(prod, [entity])
        groups.append(body | {"input_version": version(web, body)})
    clients = [web, session(reader)]
    from threading import Barrier

    start = Barrier(2)

    def request(i):
        start.wait(timeout=5)
        return post(
            clients[i], EXPORT, {"kind": "detail", "groups": groups if i == 0 else groups[::-1]}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(in_thread, lambda i=i: request(i)) for i in (0, 1)]
        replies = [f.result(timeout=8) for f in futures]
    for i, reply in enumerate(replies):
        assert reply.status_code == 201, reply.content
        rows = list(
            csv.DictReader(
                StringIO(web.get(location(reply.json()["id"])).content.decode(), newline="")
            )
        )
        assert [int(row["product"]) for row in rows] == (
            [p.pk, second_product.pk] if i == 0 else [second_product.pk, p.pk]
        )
    record(
        "reverse-order", http=[r.status_code for r in replies], tickets=ExportTicket.objects.count()
    )
