from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response


class CsrfFailure(PermissionDenied):
    default_code = "csrf_failed"


class CsrfSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except PermissionDenied as exc:
            raise CsrfFailure() from exc


def handle_exception(exc, context):
    from rest_framework.views import exception_handler

    response = exception_handler(exc, context)
    if response is None:
        return None
    code = exc.get_codes() if isinstance(exc, APIException) else "permission_denied"
    response.data = {"code": code if isinstance(code, str) else "invalid_request"}
    return response


def error(code, status):
    return Response({"code": code}, status=status)
