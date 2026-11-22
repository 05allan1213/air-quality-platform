from django.core.exceptions import RequestDataTooBig
from django.db import InterfaceError, OperationalError
from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers
from django.utils.deprecation import MiddlewareMixin


class BoundaryErrors(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, (OperationalError, InterfaceError)):
            response = JsonResponse({"code": "database_unavailable"}, status=503)
            add_never_cache_headers(response)
            return response
        if isinstance(exception, RequestDataTooBig):
            return JsonResponse({"code": "invalid_request"}, status=400)
        return None
