import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .limits import limited


def csrf_failure(request, reason=""):
    return JsonResponse({"code": "csrf_failed"}, status=403)


@require_GET
@never_cache
def csrf(request):
    return JsonResponse({"csrf_token": get_token(request)})


def credentials(request):
    if request.content_type != "application/json":
        return None
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(data, dict) or set(data) != {"username", "password"}:
        return None
    if not all(isinstance(v, str) and v and len(v) <= 1024 for v in data.values()):
        return None
    if any("\0" in v for v in data.values()):
        return None
    try:
        for v in data.values():
            v.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return data


def throttled(request, endpoint):
    return limited(endpoint, request.META["REMOTE_ADDR"])


@require_POST
@csrf_protect
def register(request):
    if throttled(request, "register"):
        return JsonResponse({"code": "rate_limited"}, status=429)
    data = credentials(request)
    if data:
        form = UserCreationForm(
            data={
                "username": data["username"],
                "password1": data["password"],
                "password2": data["password"],
            }
        )
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
            except IntegrityError:
                pass  # Concurrent registration of the same name.
            else:
                return JsonResponse({"username": user.username, "role": "USER"}, status=201)
    return JsonResponse({"code": "invalid_request"}, status=400)


@require_POST
@csrf_protect
def sign_in(request):
    if throttled(request, "login"):
        return JsonResponse({"code": "rate_limited"}, status=429)
    data = credentials(request)
    if data is None:
        return JsonResponse({"code": "invalid_request"}, status=400)
    user = authenticate(request, **data)
    if user is None:
        return JsonResponse({"code": "invalid_credentials"}, status=403)
    login(request, user)
    return JsonResponse({"username": user.username})


@require_POST
@csrf_protect
def sign_out(request):
    logout(request)
    return HttpResponse(status=204)
