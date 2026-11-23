"""V02/V03 over live loopback HTTP and committed PostgreSQL transactions."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from queue import Queue
from secrets import token_urlsafe
from socketserver import ThreadingMixIn
from threading import Event, Thread
from time import monotonic, sleep
from uuid import UUID, uuid4
from wsgiref.simple_server import WSGIServer, make_server

import httpx
import pytest
from accounts.models import Favorite, GovernanceAction
from accounts.research_exports import export_fields
from accounts.session_generation import SESSION_GENERATION
from django.contrib.auth.models import Permission
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.wsgi import get_wsgi_application
from django.db import OperationalError, connection, connections
from observations.models import StationRevision
from s02_support import observe_db
from s05_http import QuietRequestHandler
from s05_support import authorize, candidate, ingest, product, site
from test_s04_accounts import AUTH, ME, login
from test_s05_selection import DETAIL, parent

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN_ACCOUNTS = "/api/v1/admin/accounts/"
EVIDENCE = "/api/v1/admin/observation-evidence/"
SAVED = "/api/v1/me/saved-queries/"
EXPORTS = "/api/v1/me/exports/"
FAVORITES = "/api/v1/me/favorites/"
SCOPE = "?scope=synthetic"
REVIEW = "observations.review_observation"


class ConcurrentWSGI(ThreadingMixIn, WSGIServer):
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            connections.close_all()


@pytest.fixture
def sql_hook():
    return {}


@pytest.fixture
def web_clients(sql_hook, request):
    from s07_contract import Witness

    witness = Witness()
    cache.clear()
    application = get_wsgi_application()

    def observed_wsgi(environ, start_response):
        hook = sql_hook.get(environ.get("HTTP_X_S06_TEST_HOOK"))
        if not hook:
            return application(environ, start_response)
        with connection.execute_wrapper(hook):
            return application(environ, start_response)

    with make_server("127.0.0.1", 0, observed_wsgi, ConcurrentWSGI, QuietRequestHandler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with ExitStack() as clients:

                def client():
                    return clients.enter_context(
                        httpx.Client(
                            base_url=f"http://127.0.0.1:{server.server_port}",
                            timeout=8,
                            trust_env=False,
                            event_hooks={"response": [witness.inspect]},
                        )
                    )

                yield client
        finally:
            server.shutdown()
            thread.join()
            cache.clear()
            request.node.user_properties.append(
                ("s07_http_contract", json.dumps(witness.receipt()))
            )


def destination(user):
    return f"{ADMIN_ACCOUNTS}{user.pk}/actions/"


def request_for(action, version, **params):
    return {
        "action": action,
        "reason_code": "account_review",
        "request_id": str(uuid4()),
        "expected_version": version,
        **params,
    }


def permission(name):
    label, code = name.split(".")
    return Permission.objects.get(content_type__app_label=label, codename=code)


def sign_in(client, user, password):
    client.headers.update(login(client, user.username, password))
    return client


def json_status(response, status):
    assert response.status_code == status
    return response.json()


def denied(response, code, status=403):
    assert json_status(response, status) == {"code": code}
    assert "Content-Disposition" not in response.headers


def version(client, user):
    return json_status(client.get(destination(user)), 200)["version"]


def observed(user):
    with observe_db() as pg:
        return pg.execute(
            """SELECT u.is_active, COALESCE(s.version, 0), COALESCE(s.session_generation, 0)
                 FROM auth_user u LEFT JOIN accounts_accountstate s ON s.user_id=u.id
                WHERE u.id=%s""",
            (user.pk,),
        ).fetchone()


@pytest.fixture
def identities(django_user_model):
    users = django_user_model.objects
    password = token_urlsafe(24)
    owner = users.create_user("s06-owner", password=password)
    staff_only = users.create_user("s06-staff", password=password, is_staff=True)
    admin = users.create_user("s06-account-operator", password=password, is_staff=True)
    admin.user_permissions.add(permission("accounts.manage_accounts"), permission(REVIEW))
    root = users.create_superuser("s06-root", password=password)
    return owner, staff_only, admin, root, password


def test_v02_disable_enable_keeps_untouched_old_cookie_invalid(web_clients, identities):
    owner, staff, admin, root, password = identities
    operator = sign_in(web_clients(), admin, password)
    user_a = sign_in(web_clients(), owner, password)
    user_b = sign_in(web_clients(), owner, password)
    staff_web = sign_in(web_clients(), staff, password)
    replay = web_clients()
    assert user_a.cookies["sessionid"] != user_b.cookies["sessionid"]
    old_cookie = user_b.cookies["sessionid"]
    # Exercise a legacy session created before generation binding was installed.
    legacy = Session.objects.get(session_key=old_cookie)
    session_data = legacy.get_decoded()
    session_data.pop(SESSION_GENERATION)
    legacy.session_data = SessionStore().encode(session_data)
    legacy.save(update_fields=["session_data"])
    assert user_b.get(ME).status_code == 200
    route = destination(owner)
    before = observed(owner)
    assert before == (True, version(operator, owner), 0)
    payload = request_for("disable", before[1])

    denied(operator.post(route, json=payload, headers={"X-CSRFToken": ""}), "csrf_failed")
    denied(user_a.post(route, json=payload), "permission_denied")
    denied(staff_web.post(route, json=payload), "permission_denied")
    denied(operator.post(destination(admin), json=payload), "permission_denied")
    denied(operator.post(destination(root), json=payload), "permission_denied")
    assert observed(owner) == before
    assert GovernanceAction.objects.count() == 0

    disabled = json_status(operator.post(route, json=payload), 200)
    assert disabled["active"] is False and disabled["version"] == before[1] + 1
    assert observed(owner) == (False, disabled["version"], before[2] + 1)
    denied(user_a.get(ME), "not_authenticated")
    login(replay, owner.username, password, status=403)

    enabled = json_status(
        operator.post(route, json=request_for("enable", disabled["version"])), 200
    )
    assert enabled["active"] is True and enabled["version"] == disabled["version"] + 1
    assert observed(owner) == (True, enabled["version"], before[2] + 1)
    # No request used user_b's cookie while disabled. A middleware check made
    # during the disabled interval alone cannot satisfy this assertion.
    denied(user_b.get(ME), "not_authenticated")
    denied(replay.get(ME, headers={"Cookie": f"sessionid={old_cookie}"}), "not_authenticated")
    sign_in(user_b, owner, password)
    assert json_status(user_b.get(ME), 200)["username"] == owner.username
    assert GovernanceAction.objects.filter(actor=admin).count() == 2


def test_v02_current_direct_and_group_permissions(web_clients, identities):
    owner, staff, admin, root, password = identities
    operator = sign_in(web_clients(), admin, password)
    privileged = sign_in(web_clients(), root, password)
    user = sign_in(web_clients(), owner, password)
    route = destination(owner)
    p = product("s06-ability")
    evidence = EVIDENCE + f"?product={p.pk}&scope=synthetic"
    state = version(operator, owner)
    denied(user.get(evidence), "permission_denied")
    denied(
        operator.post(
            route,
            json=request_for("grant", state, permission="observations.manage_grants"),
        ),
        "permission_denied",
    )
    granted = json_status(
        operator.post(route, json=request_for("grant", state, permission=REVIEW)), 200
    )
    assert granted["version"] == state + 1
    denied(user.get(evidence), "invalid_request", 400)
    revoked = json_status(
        operator.post(route, json=request_for("revoke", granted["version"], permission=REVIEW)), 200
    )
    assert revoked["version"] == granted["version"] + 1
    denied(user.get(evidence), "permission_denied")
    assert user.get(ME).status_code == 200

    # The same cookie also sees assignments through the named role service.
    in_group = json_status(
        operator.post(
            route, json=request_for("grant_role", revoked["version"], role="quality_reviewer")
        ),
        200,
    )
    assert in_group["version"] == revoked["version"] + 1
    denied(user.get(evidence), "invalid_request", 400)
    json_status(
        operator.post(
            route, json=request_for("revoke_role", in_group["version"], role="quality_reviewer")
        ),
        200,
    )
    denied(user.get(evidence), "permission_denied")
    assert user.get(ME).status_code == 200

    payload = request_for("disable", version(operator, owner))
    first = json_status(operator.post(route, json=payload), 200)
    assert (
        UUID(first["action_id"])
        == GovernanceAction.objects.get(actor=admin, request_id=payload["request_id"]).pk
    )
    admin_state = version(privileged, admin)
    json_status(
        privileged.post(
            destination(admin),
            json=request_for("revoke", admin_state, permission="accounts.manage_accounts"),
        ),
        200,
    )
    denied(operator.post(route, json=payload), "permission_denied")
    assert operator.get(ME).status_code == 200


def test_v02_admin_and_owner_boundaries(web_clients, identities):
    owner, staff, admin, root, password = identities
    operator = sign_in(web_clients(), admin, password)
    privileged = sign_in(web_clients(), root, password)
    user = sign_in(web_clients(), owner, password)
    second = sign_in(web_clients(), owner, password)
    staff_web = sign_in(web_clients(), staff, password)
    p = product("s06-private")
    epoch = site(p)
    authorize(root, p, detail_fields=export_fields("detail"))
    ingest(root, p, [candidate(p)])
    favorite = Favorite.objects.create(
        owner=owner,
        kind=Favorite.Kind.STATION,
        station=epoch.station,
        preferred_product=p,
        synthetic_scope=True,
    )
    saved = json_status(
        user.post(
            SAVED,
            json={
                **parent(p, [epoch], method="statistics"),
                "title": "S06 F private",
            },
        ),
        201,
    )
    detail = parent(p, [epoch])
    stats = json_status(user.post(DETAIL, json=detail), 200)
    ticket = json_status(
        user.post(
            EXPORTS,
            json={
                "kind": "detail",
                "groups": [detail | {"input_version": stats["input_version"]}],
            },
        ),
        201,
    )
    download = EXPORTS + ticket["id"] + "/download/" + SCOPE
    assert user.get(download).headers["Content-Type"].startswith("text/csv")
    for stranger in (operator, privileged, staff_web):
        for route in (
            FAVORITES + f"{favorite.pk}/" + SCOPE,
            SAVED + saved["id"] + "/" + SCOPE,
            download,
        ):
            denied(stranger.get(route), "not_found", 404)

    # Admin is explicitly limited to reviewed account operations. Even the
    # initial superuser has no built-in User/Group or observation edit route.
    assert operator.get("/admin/").status_code == 200
    assert staff_web.get("/admin/").status_code in {302, 403}
    values = list(StationRevision.objects.values_list("pk", "value", "is_current"))
    for route in (
        f"/admin/auth/user/{owner.pk}/change/",
        "/admin/auth/group/",
        "/admin/django_celery_beat/periodictask/",
        f"/admin/observations/stationrevision/{values[0][0]}/change/",
    ):
        assert privileged.post(route, data={"value": "99"}).status_code == 404
    assert operator.post(EVIDENCE, json={"value": 99}).status_code == 405
    assert list(StationRevision.objects.values_list("pk", "value", "is_current")) == values

    new_password = token_urlsafe(24)
    assert (
        user.post(
            AUTH + "password/",
            json={
                "old_password": password,
                "new_password": new_password,
            },
        ).status_code
        == 204
    )
    denied(user.get(ME), "not_authenticated")
    denied(second.get(download), "not_authenticated")
    sign_in(second, owner, new_password)
    assert second.get(download).headers["Content-Type"].startswith("text/csv")


def test_v02_admin_login_and_legacy_control_path_share_generation(web_clients, identities):
    _, _, admin, root, password = identities
    privileged = sign_in(web_clients(), root, password)
    route = destination(admin)
    old = web_clients()
    login_url = "/admin/login/"
    csrf_token = old.get(AUTH + "csrf/").json()["csrf_token"]
    login_fields = {"username": admin.username, "password": password}
    assert old.post(login_url, data=login_fields).status_code == 403
    response = old.post(login_url, data=login_fields | {"csrfmiddlewaretoken": csrf_token})
    assert response.status_code == 302
    assert old.get("/admin/").status_code == 200
    old_cookie = old.cookies["sessionid"]

    disabled = json_status(
        privileged.post(route, json=request_for("disable", version(privileged, admin))), 200
    )
    enabled = json_status(
        privileged.post(route, json=request_for("enable", disabled["version"])), 200
    )
    assert enabled["active"] is True
    assert (
        web_clients().get("/admin/", headers={"Cookie": f"sessionid={old_cookie}"}).status_code
        == 302
    )

    csrf_token = old.get(AUTH + "csrf/").json()["csrf_token"]
    assert (
        old.post(login_url, data=login_fields | {"csrfmiddlewaretoken": csrf_token}).status_code
        == 302
    )
    bound = Session.objects.get(session_key=old.cookies["sessionid"]).get_decoded()
    assert bound[SESSION_GENERATION] == observed(admin)[2] == 1
    assert old.get("/admin/").status_code == 200

    user = identities[0]
    form_route = f"/admin/auth/user/{user.pk}/actions/"
    csrf_token = old.get(AUTH + "csrf/").json()["csrf_token"]
    payload = request_for("disable", version(old, user))
    denied(old.post(form_route, data=payload), "csrf_failed")
    status = json_status(
        old.post(form_route, data=payload | {"csrfmiddlewaretoken": csrf_token}), 200
    )
    assert observed(user) == (False, status["version"], 1)
    assert GovernanceAction.objects.get(actor=admin, request_id=payload["request_id"]).pk == UUID(
        status["action_id"]
    )


def committed_receipt(user, request_id):
    with observe_db() as pg:
        return pg.execute(
            """SELECT actor_id, action, reason_code, target_kind, target, before_state,
                      after_state, before_version, after_version, outcome,
                      created_at <= clock_timestamp()
                 FROM accounts_governanceaction WHERE actor_id=%s AND request_id=%s""",
            (user.pk, UUID(request_id)),
        ).fetchall()


def check_receipt(admin, owner, payload):
    version_before = payload["expected_version"]
    assert committed_receipt(admin, payload["request_id"]) == [
        (
            admin.pk,
            "disable",
            "account_review",
            "account",
            str(owner.pk),
            "active",
            "inactive",
            version_before,
            version_before + 1,
            "applied",
            True,
        )
    ]


def expect_blocking(holder, waiter):
    assert holder != waiter
    with observe_db() as pg:
        until = monotonic() + 3
        while monotonic() < until:
            if pg.execute("SELECT %s = ANY(pg_blocking_pids(%s))", (holder, waiter)).fetchone()[0]:
                return
            sleep(0.01)
    pytest.fail("The competing HTTP transaction did not wait for the first PostgreSQL writer")


@pytest.mark.parametrize("second_case", ["same_request", "same_id_other_parameters", "other_admin"])
def test_v03_two_http_connections_share_one_commit(web_clients, identities, sql_hook, second_case):
    owner, _, admin, root, password = identities
    first = sign_in(web_clients(), admin, password)
    second = sign_in(web_clients(), root if second_case == "other_admin" else admin, password)
    route = destination(owner)
    version_before = version(first, owner)
    payload = request_for("disable", version_before)
    other = {
        "same_request": payload,
        "same_id_other_parameters": payload | {"action": "enable"},
        "other_admin": request_for("disable", version_before),
    }[second_case]
    wrote, waiting, release = Queue(), Queue(), Event()

    def hold_after_audit(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith('INSERT INTO "accounts_governanceaction"'):
            wrote.put(context["connection"].connection.info.backend_pid)
            assert release.wait(5), "Timed out releasing V03 transaction"
        return result

    def watch_lock(execute, sql, params, many, context):
        if 'FROM "auth_user"' in sql and "FOR UPDATE" in sql and waiting.empty():
            waiting.put(context["connection"].connection.info.backend_pid)
        return execute(sql, params, many, context)

    sql_hook.update(first=hold_after_audit, second=watch_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.post, route, json=payload, headers={"X-S06-Test-Hook": "first"})
        try:
            holder = wrote.get(timeout=4)
            assert observed(owner) == (True, version_before, 0)
            assert committed_receipt(admin, payload["request_id"]) == []
            b = pool.submit(second.post, route, json=other, headers={"X-S06-Test-Hook": "second"})
            expect_blocking(holder, waiting.get(timeout=4))
        finally:
            release.set()
        applied = json_status(a.result(timeout=5), 200)
        response = b.result(timeout=5)
    if second_case == "same_request":
        assert json_status(response, 200) == applied
    else:
        denied(
            response,
            {
                "same_id_other_parameters": "request_conflict",
                "other_admin": "precondition_conflict",
            }[second_case],
            409,
        )
    assert observed(owner) == (False, version_before + 1, 1)
    check_receipt(admin, owner, payload)
    assert GovernanceAction.objects.count() == 1


def test_v03_audit_and_mutation_rollback_together(web_clients, identities, sql_hook, caplog):
    owner, _, admin, _, password = identities
    operator = sign_in(web_clients(), admin, password)
    route = destination(owner)
    before = observed(owner)
    payload = request_for("disable", version(operator, owner))
    sensitive_marker = token_urlsafe(36)
    invalid = operator.post(route, json=payload | {"password": sensitive_marker})
    denied(invalid, "invalid_request", 400)
    assert committed_receipt(admin, payload["request_id"]) == []

    audit_written = Event()

    def fail_after_audit(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith('INSERT INTO "accounts_governanceaction"'):
            audit_written.set()
            raise OperationalError("injected account rollback")
        return result

    sql_hook["rollback"] = fail_after_audit
    denied(
        operator.post(route, json=payload, headers={"X-S06-Test-Hook": "rollback"}),
        "database_unavailable",
        503,
    )
    assert audit_written.is_set()
    assert observed(owner) == before
    assert committed_receipt(admin, payload["request_id"]) == []
    response = operator.post(route, json=payload)
    applied = json_status(response, 200)
    assert applied["version"] == before[1] + 1 and applied["active"] is False
    check_receipt(admin, owner, payload)
    assert GovernanceAction.objects.count() == 1
    persisted = repr(list(GovernanceAction.objects.values()))
    for confidential in (sensitive_marker, password, operator.cookies["sessionid"]):
        if any(
            confidential in output
            for output in (invalid.text, response.text, persisted, caplog.text)
        ):
            pytest.fail("Sensitive request or account material was retained or returned")
