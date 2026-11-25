"""Account mutations and committed governance receipts. The User row fences ACL writes."""

from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from observations.policy import Denied, clock

from .assigned_permissions import CONFIGURE_SCHEDULE, MAINTAIN_CITY_ALIAS
from .models import AccountState, GovernanceAction
from .research_session import SessionCheck

MANAGE = "accounts.manage_accounts"
PERMISSIONS = frozenset(
    {
        MANAGE,
        "accounts.adjudicate_quality",
        "accounts.manage_catalog",
        MAINTAIN_CITY_ALIAS,
        "accounts.diagnose_operations",
        "accounts.manage_backups",
        "accounts.restore_backup",
        "observations.manage_collection",
        CONFIGURE_SCHEDULE,
        "observations.run_collection",
        "observations.import_history",
        "observations.review_observation",
        "observations.manage_grants",
    }
)
ROLES = {
    "collection_operator": {"observations.run_collection"},
    "quality_reviewer": {"observations.review_observation"},
}


def command(data, actions, reasons, *, extra=None):
    """Validate the common command envelope before using it in an ORM predicate."""
    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
        raise Denied("invalid_request")
    action = data["action"]
    if (
        action not in actions
        or set(data)
        != {"action", "request_id", "reason_code", "expected_version"}
        | (extra or {}).get(action, set())
        or not isinstance(data["reason_code"], str)
        or data["reason_code"] not in reasons
        or type(data["expected_version"]) is not int
        or not 0 <= data["expected_version"] < 2**63
        or not isinstance(data["request_id"], str)
    ):
        raise Denied("invalid_request")
    try:
        identifier = UUID(data["request_id"])
    except ValueError:
        raise Denied("invalid_request") from None
    if str(identifier) != data["request_id"]:
        raise Denied("invalid_request")
    return data | {"request_id": identifier}


def replay(actor_id, data, kind, target, *, parameter="", origin=""):
    previous = GovernanceAction.objects.filter(
        actor_id=actor_id, request_id=data["request_id"]
    ).first()
    if previous and (
        previous.target_kind,
        previous.target,
        previous.action,
        previous.reason_code,
        previous.before_version,
        previous.parameter,
        previous.origin,
    ) != (
        kind,
        str(target),
        data["action"],
        data["reason_code"],
        data["expected_version"],
        parameter,
        origin,
    ):
        raise Denied("request_conflict")
    return previous


def receipt(
    actor_id,
    data,
    kind,
    target,
    before,
    after,
    before_version,
    after_version,
    *,
    parameter="",
    origin="",
):
    return GovernanceAction.objects.create(
        actor_id=actor_id,
        request_id=data["request_id"],
        action=data["action"],
        reason_code=data["reason_code"],
        target_kind=kind,
        target=str(target),
        before_state=before,
        after_state=after,
        before_version=before_version,
        after_version=after_version,
        parameter=parameter,
        origin=origin,
        created_at=clock(),
    )


def permission_names(user):
    # Include privileges of disabled targets too. Django's has_perm omits these.
    direct = user.user_permissions.values_list("content_type__app_label", "codename")
    indirect = Permission.objects.filter(group__user=user).values_list(
        "content_type__app_label", "codename"
    )
    return {f"{app}.{code}" for app, code in (*direct, *indirect)}


def subordinate(operator, subject):
    if (
        not operator.is_active
        or not operator.has_perm(MANAGE)
        or operator.pk == subject.pk
        or subject.is_superuser
    ):
        raise Denied("permission_denied")
    grants = permission_names(subject)
    if not operator.is_superuser and (
        MANAGE in grants or not grants <= operator.get_all_permissions()
    ):
        raise Denied("permission_denied")


def as_result(record):
    return {
        "action_id": str(record.pk),
        "active": record.after_state == "active",
        "version": record.after_version,
    }


@transaction.atomic
def account_action(request, pk, data=None):
    # The same order is used even if two operators target each other.
    users = {
        u.pk: u
        for u in get_user_model()
        .objects.select_for_update()
        .filter(pk__in=[pk, request.user.pk])
        .order_by("pk")
    }
    operator = users.get(request.user.pk)
    if operator is None or not operator.is_active or not operator.has_perm(MANAGE):
        raise Denied("permission_denied")
    subject = users.get(pk)
    if subject is None:
        raise Denied("not_found")
    # All membership writers hold the member's user lock. Lock group definitions too.
    group_ids = set(operator.groups.values_list("pk", flat=True)) | set(
        subject.groups.values_list("pk", flat=True)
    )
    role = ""
    if data is not None:
        data = command(
            data,
            {"enable", "disable", "grant", "revoke", "grant_role", "revoke_role"},
            {"account_review"},
            extra={
                "grant": {"permission"},
                "revoke": {"permission"},
                "grant_role": {"role"},
                "revoke_role": {"role"},
            },
        )
        role = data.get("role", "")
        if "role" in data:
            if not isinstance(role, str) or role not in ROLES:
                raise Denied("invalid_request")
            group_ids.update(Group.objects.filter(name=f"aq:{role}").values_list("pk", flat=True))
    list(Group.objects.select_for_update().filter(pk__in=group_ids).order_by("pk"))
    operator = get_user_model().objects.get(pk=operator.pk)  # No pre-lock permission caches.
    subordinate(operator, subject)
    check = SessionCheck(request)
    state = AccountState.objects.filter(pk=pk).first()
    version = state.version if state else 0
    if data is None:
        check.finish(())
        return {"account": pk, "active": subject.is_active, "version": version}
    action = data["action"]
    parameter = data.get("permission", role)
    if "permission" in data and (not isinstance(parameter, str) or parameter not in PERMISSIONS):
        raise Denied("invalid_request")
    required = ROLES[role] if role else ({parameter} if parameter else set())
    if required and (
        not operator.has_perms(required) or (MANAGE in required and not operator.is_superuser)
    ):
        raise Denied("permission_denied")
    previous = replay(operator.pk, data, "account", pk, parameter=parameter)
    if previous:
        check.finish(())
        return as_result(previous)
    if data["expected_version"] != version:
        raise Denied("precondition_conflict")
    if state is None:
        state = AccountState(user=subject)
    before = "active" if subject.is_active else "inactive"
    if action in {"enable", "disable"}:
        active = action == "enable"
        if subject.is_active == active:
            raise Denied("precondition_conflict")
        subject.is_active = active
        subject.save(update_fields=["is_active"])
        if not active:
            state.session_generation += 1
    elif role:
        group, created = Group.objects.get_or_create(name=f"aq:{role}")
        if created:
            for name in required:
                app, code = name.split(".")
                group.permissions.add(
                    Permission.objects.get(content_type__app_label=app, codename=code)
                )
        present = {
            f"{app}.{code}"
            for app, code in group.permissions.values_list("content_type__app_label", "codename")
        }
        if present != required:
            raise Denied("precondition_conflict")
        modify(subject.groups, group, action == "grant_role")
    else:
        app, code = parameter.split(".")
        permission = Permission.objects.get(content_type__app_label=app, codename=code)
        modify(subject.user_permissions, permission, action == "grant")
    state.version += 1
    state.save()
    record = receipt(
        operator.pk,
        data,
        "account",
        pk,
        before,
        "active" if subject.is_active else "inactive",
        version,
        state.version,
        parameter=parameter,
    )
    check.finish(())
    return as_result(record)


def modify(members, item, add):
    if members.filter(pk=item.pk).exists() == add:
        raise Denied("precondition_conflict")
    if add:
        members.add(item)
    else:
        members.remove(item)
