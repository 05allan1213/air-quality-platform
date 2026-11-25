"""S08 workspace capabilities over real session-authenticated HTTP."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from accounts.models import AccountState
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from s05_http import loopback

CAPABILITIES = "/api/v1/me/capabilities/"

pytestmark = pytest.mark.django_db(transaction=True)


def add_permission(user, app_label, codename):
    permission = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    user.user_permissions.add(permission)


def test_capabilities_reflect_current_permissions_and_session(django_user_model):
    reviewer = django_user_model.objects.create_user(
        "s08-capability-reviewer", password="s05-test-only"
    )
    AccountState.objects.create(user=reviewer)
    add_permission(reviewer, "observations", "review_observation")
    add_permission(reviewer, "accounts", "adjudicate_quality")

    with loopback(reviewer) as (web, _):
        response = web.get(CAPABILITIES)
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert response.json() == {
            "workspaces": ["user", "manage"],
            "actions": ["review_observation", "adjudicate_quality"],
        }

        invalid = web.get(CAPABILITIES, params={"debug": "1"})
        assert invalid.status_code == 400
        assert invalid.json() == {"code": "invalid_request"}

        state = AccountState.objects.get(user=reviewer)
        state.session_generation += 1
        state.save(update_fields=["session_generation"])
        expired = web.get(CAPABILITIES)
        assert expired.status_code == 403
        assert expired.json() == {"code": "not_authenticated"}


def test_staff_without_named_permission_stays_in_user_workspace(django_user_model):
    staff = django_user_model.objects.create_user(
        "s08-capability-staff", password="s05-test-only", is_staff=True
    )

    with loopback(staff) as (web, _):
        response = web.get(CAPABILITIES)

    assert response.status_code == 200
    assert response.json() == {"workspaces": ["user"], "actions": []}


def test_superuser_without_explicit_permission_stays_in_user_workspace(django_user_model):
    superuser = django_user_model.objects.create_superuser(
        "s08-capability-superuser", password="s05-test-only"
    )

    with loopback(superuser) as (web, _):
        response = web.get(CAPABILITIES)

    assert response.status_code == 200
    assert response.json() == {"workspaces": ["user"], "actions": []}


def test_group_permission_projects_only_its_named_capability(django_user_model):
    user = django_user_model.objects.create_user("s08-capability-group", password="s05-test-only")
    group = Group.objects.create(name="s08-reviewers")
    permission = Permission.objects.get(
        content_type__app_label="observations",
        codename="review_observation",
    )
    group.permissions.add(permission)
    user.groups.add(group)

    with loopback(user) as (web, _):
        response = web.get(CAPABILITIES)

    assert response.status_code == 200
    assert response.json() == {
        "workspaces": ["user", "manage"],
        "actions": ["review_observation"],
    }


def test_session_revoked_during_capability_read(django_user_model):
    user = django_user_model.objects.create_user("s08-reader", password="s05-test-only")
    add_permission(user, "observations", "review_observation")
    armed, entered, resume = Event(), Event(), Event()

    def before_sql(sql):
        if armed.is_set() and 'FROM "auth_user"' in sql and "FOR UPDATE" in sql:
            armed.clear()
            entered.set()
            assert resume.wait(5)

    with loopback(user, before_sql=before_sql) as (web, _), ThreadPoolExecutor(1) as client:
        key = web.cookies["sessionid"]
        armed.set()
        result = client.submit(web.get, CAPABILITIES)
        try:
            assert entered.wait(5)
            assert Session.objects.filter(session_key=key).delete()[0] == 1
        finally:
            resume.set()
        response = result.result(timeout=10)
        assert response.status_code == 403
        assert response.json() == {"code": "not_authenticated"}


@pytest.mark.parametrize("is_staff", [False, True])
def test_account_management_capability_requires_staff(django_user_model, is_staff):
    user = django_user_model.objects.create_user(
        f"s08-capability-account-{is_staff}",
        password="s05-test-only",
        is_staff=is_staff,
    )
    add_permission(user, "accounts", "manage_accounts")

    with loopback(user) as (web, _):
        response = web.get(CAPABILITIES)

    assert response.status_code == 200
    if is_staff:
        assert response.json() == {
            "workspaces": ["user", "manage"],
            "actions": ["manage_accounts"],
        }
    else:
        assert response.json() == {"workspaces": ["user"], "actions": []}
