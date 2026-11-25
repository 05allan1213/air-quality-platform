"""Named permissions recorded in Django's direct and group assignments."""

from django.contrib.auth.models import Permission
from django.db.models import Q

CONFIGURE_SCHEDULE = "observations.configure_collection_schedule"
MAINTAIN_CITY_ALIAS = "accounts.maintain_city_search_alias"


def _assignments(user):
    if not user.is_active or user.pk is None:
        return Permission.objects.none()
    return Permission.objects.filter(Q(user=user) | Q(group__user=user)).order_by()


def assigned_permissions(user):
    return {
        f"{label}.{code}"
        for label, code in _assignments(user)
        .values_list("content_type__app_label", "codename")
        .distinct()
    }


def has_assigned_permission(user, permission):
    label, code = permission.split(".")
    return _assignments(user).filter(content_type__app_label=label, codename=code).exists()
