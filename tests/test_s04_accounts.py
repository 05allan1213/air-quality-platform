"""Account API against PostgreSQL-backed sessions and live loopback HTTP."""

import secrets
from threading import Thread
from wsgiref.simple_server import make_server

import httpx
import pytest
from accounts.limits import LIMITS
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.wsgi import get_wsgi_application
from django.db import connections
from s02_support import observe_db

AUTH = "/api/v1/auth/"
ME = "/api/v1/me/"
ADMIN = "/api/v1/admin/observation-evidence/"
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def account_env():
    cache.clear()
    yield
    cache.clear()


def csrf(client):
    return {"X-CSRFToken": client.get(AUTH + "csrf/").json()["csrf_token"]}


def login(client, username, password, status=200):
    response = client.post(
        AUTH + "login/", json={"username": username, "password": password}, headers=csrf(client)
    )
    assert response.status_code == status
    return csrf(client) if status == 200 else None


@pytest.fixture
def loopback():
    with make_server("127.0.0.1", 0, get_wsgi_application()) as server:

        def serve():
            try:
                server.serve_forever()
            finally:
                connections.close_all()

        thread = Thread(target=serve)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def test_account_sessions_csrf_password_and_permissions(loopback):
    old_password, new_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    owner = get_user_model().objects.create_user(username="owner", password=old_password)
    other = get_user_model().objects.create_user(username="other", password=old_password)
    operator = get_user_model().objects.create_user(username="operator", password=old_password)
    permission = Permission.objects.get(
        codename="review_observation", content_type__app_label="observations"
    )
    operator.user_permissions.add(permission)

    with (
        httpx.Client(base_url=loopback, trust_env=False) as a,
        httpx.Client(base_url=loopback, trust_env=False) as a2,
        httpx.Client(base_url=loopback, trust_env=False) as b,
        httpx.Client(base_url=loopback, trust_env=False) as op,
        httpx.Client(base_url=loopback, trust_env=False) as replay,
    ):
        denied = a.get(ME)
        assert denied.status_code == 403 and denied.json() == {"code": "not_authenticated"}
        assert "no-store" in denied.headers["Cache-Control"]
        a_token = login(a, owner.username, old_password)
        login(a2, owner.username, old_password)
        login(b, other.username, old_password)
        login(op, operator.username, old_password)
        assert a.get(ME).json() == {"username": "owner", "first_name": "", "last_name": ""}
        assert a.patch(ME, json={"first_name": "Updated"}).json() == {"code": "csrf_failed"}
        for invalid in (
            {"owner": other.pk},
            {"username": "other"},
            {"is_superuser": True},
            {"first_name": "X" * 151},
            {"last_name": "x\0y"},
        ):
            response = a.patch(ME, json=invalid, headers=a_token)
            assert response.status_code == 400 and response.json() == {"code": "invalid_request"}
        assert a.get(f"{ME}{other.pk}/").status_code == 404
        assert a.delete(ME, headers=a_token).status_code == 405
        assert a.patch(ME, json={"first_name": "Updated"}, headers=a_token).status_code == 200
        assert b.get(ME).json()["first_name"] == ""
        with observe_db() as pg:
            assert pg.execute(
                "SELECT username, first_name, is_staff, is_superuser FROM auth_user "
                "WHERE id IN (%s,%s) ORDER BY id",
                (owner.pk, other.pk),
            ).fetchall() == [("owner", "Updated", False, False), ("other", "", False, False)]

        password = AUTH + "password/"
        change = {"old_password": old_password, "new_password": new_password}
        assert a.post(password, json=change).json() == {"code": "csrf_failed"}
        assert a.post(
            password, json=change | {"old_password": "wrong"}, headers=a_token
        ).json() == {"code": "invalid_credentials"}
        assert a.post(password, json=change | {"new_password": "123"}, headers=a_token).json() == {
            "code": "invalid_request"
        }
        for _ in range(LIMITS["password"] - 2):
            assert a.post(password, json={}, headers=a_token).status_code == 400
        assert a.post(password, json=change, headers=a_token).status_code == 429
        assert a.get(ME).status_code == a2.get(ME).status_code == 200
        assert get_user_model().objects.get(pk=owner.pk).password == owner.password
        cache.delete(f"s01:password:{owner.pk}")
        session_ids = [client.cookies["sessionid"] for client in (a, a2)]
        assert a.post(password, json=change, headers=a_token).status_code == 204
        assert "sessionid" not in a.cookies
        for session_id in session_ids:
            response = replay.get(ME, headers={"Cookie": f"sessionid={session_id}"})
            assert response.status_code == 403 and response.json() == {"code": "not_authenticated"}
        with observe_db() as pg:
            assert pg.execute(
                "SELECT count(*) FROM django_session WHERE session_key = ANY(%s)", (session_ids,)
            ).fetchone() == (0,)
        login(a, owner.username, old_password, status=403)
        a_token = login(a, owner.username, new_password)
        login(a2, owner.username, new_password)
        single = a.cookies["sessionid"]
        assert a.post(AUTH + "logout/", headers=a_token).status_code == 204
        assert replay.get(ME, headers={"Cookie": f"sessionid={single}"}).status_code == 403
        assert a2.get(ME).status_code == 200
        get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
        assert a2.get(ME).status_code == 403

        assert op.get(ADMIN + "?product=999").json() == {"code": "use_denied"}
        # Reach the role check for a real existing product after sign-in.
        from s02_support import create_product

        product = create_product()
        request = ADMIN + f"?product={product.pk}&scope=synthetic"
        assert op.get(request).json() == {"code": "invalid_request"}
        operator.user_permissions.remove(permission)
        assert op.get(request).json() == {"code": "permission_denied"}
        assert op.get(ME).status_code == 200
