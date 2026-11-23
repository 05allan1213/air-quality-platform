"""Account generations are serialized by the existing auth_user row lock."""

from config.errors import DATABASE_CONNECTION_ERRORS, database_unavailable
from django.contrib.auth import get_user_model, logout

from .models import AccountState

SESSION_GENERATION = "account_generation"


def generation(user_id):
    return (
        AccountState.objects.filter(pk=user_id).values_list("session_generation", flat=True).first()
        or 0
    )


def matches_generation(data, expected):
    # Pre-S06 sessions have an implicit generation of zero.
    value = data.get(SESSION_GENERATION, 0)
    return type(value) is int and value == expected


def lock_login_name(name):
    # Lock *before* authentication, covering password verification and session binding.
    if not isinstance(name, str) or len(name) > 150 or "\x00" in name:
        return None
    return get_user_model().objects.select_for_update().filter(username=name).first()


def bind_generation(request, user_id):
    request.session[SESSION_GENERATION] = generation(user_id)


class GenerationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            if request.user.is_authenticated and not matches_generation(
                request.session, generation(request.user.pk)
            ):
                logout(request)
        except DATABASE_CONNECTION_ERRORS:
            return database_unavailable()
        return self.get_response(request)
