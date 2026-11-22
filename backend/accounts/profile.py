"""Session-authenticated account operations bound to the current Django user."""

from django.contrib.auth import get_user_model, logout
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as RequestError
from rest_framework.response import Response

from .api import error
from .limits import limited

PROFILE_FIELDS = ("first_name", "last_name")


def valid_text(value, max_length):
    if not isinstance(value, str) or len(value) > max_length or "\0" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def current_user(request, *, lock=False):
    users = get_user_model().objects.filter(pk=request.user.pk, is_active=True)
    if lock:
        users = users.select_for_update()
    user = users.first()
    if user is None or not constant_time_compare(
        user.get_session_auth_hash(), request.user.get_session_auth_hash()
    ):
        raise PermissionDenied()
    return user


@never_cache
@api_view(["GET", "PATCH"])
def profile(request):
    if request.query_params:
        return error("invalid_request", 400)
    if request.method == "GET":
        user = current_user(request)
    else:
        fields = request.data
        model = get_user_model()
        if not (
            isinstance(fields, dict)
            and fields
            and set(fields) <= set(PROFILE_FIELDS)
            and all(
                valid_text(value, model._meta.get_field(field).max_length)
                for field, value in fields.items()
            )
        ):
            return error("invalid_request", 400)
        with transaction.atomic():
            user = current_user(request, lock=True)
            for field, value in fields.items():
                setattr(user, field, value)
            user.save(update_fields=fields.keys())
    return Response({field: getattr(user, field) for field in ("username", *PROFILE_FIELDS)})


@never_cache
@api_view(["POST"])
def change_password(request):
    if limited("password", request.user.pk):
        return error("rate_limited", 429)
    fields = request.data
    if not (
        not request.query_params
        and isinstance(fields, dict)
        and set(fields) == {"old_password", "new_password"}
        and all(valid_text(value, 1024) and value for value in fields.values())
    ):
        return error("invalid_request", 400)
    with transaction.atomic():
        user = current_user(request, lock=True)
        # Avoid the automatic hash upgrade on a rejected change request.
        if not check_password(fields["old_password"], user.password):
            return error("invalid_credentials", 403)
        try:
            validate_password(fields["new_password"], user)
        except ValidationError:
            raise RequestError(code="invalid_request") from None
        user.set_password(fields["new_password"])
        user.save(update_fields=["password"])
    logout(request)
    return Response(status=204)
