"""Shared boundary for authenticated, non-cacheable S04 reads and keyset cursors."""

import hashlib
import json
from datetime import datetime

from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response
from samples.normalization import Rejected, parse_time

from .policy import Denied
from .queries import pk

CURSOR_MAX = 2048
CURSOR_AGE = 3600


def params(query, allowed_keys):
    if set(query) - set(allowed_keys):
        raise Denied("invalid_request")
    result = {}
    for key in query:
        if hasattr(query, "getlist") and len(query.getlist(key)) != 1:
            raise Denied("invalid_request")
        value = query[key]
        if (
            not isinstance(value, str)
            or not value
            or not value.isprintable()
            or len(value) > (CURSOR_MAX if key == "cursor" else 200)
        ):
            raise Denied("invalid_cursor" if key == "cursor" else "invalid_request")
        result[key] = value
    return result


def instant(value, hour=False) -> datetime:
    try:
        stamp = parse_time(value)
    except Rejected:
        raise Denied("invalid_request") from None
    # Leave room for UTC offsets, source-local day ends and rolling windows.
    if stamp.year in (1, 9999) or hour and (stamp.minute or stamp.second or stamp.microsecond):
        raise Denied("invalid_request")
    return stamp


def page_size(values):
    size = pk(values.get("page_size", "50"))
    if size > 100:
        raise Denied("query_too_large")
    return size


def fingerprint(value):
    def canonical(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return DjangoJSONEncoder().default(obj)

    data = json.dumps(value, default=canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def encode_cursor(route, user, conditions, payload):
    return signing.dumps([fingerprint([route, user, conditions]), payload], salt="s04.page.v1")


def decode_cursor(token, route, user, conditions):
    if token is None:
        return None
    try:
        if len(token) > CURSOR_MAX:
            raise ValueError
        binding, payload = signing.loads(token, salt="s04.page.v1", max_age=CURSOR_AGE)
        if binding != fingerprint([route, user, conditions]) or not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, TypeError, signing.BadSignature):
        raise Denied("invalid_cursor") from None


def read_view(service):
    @never_cache
    @api_view(["GET"])
    def view(request, **kwargs):
        try:
            return Response(service(request.user.pk, request.query_params, **kwargs))
        except Denied as exc:
            status = {"use_denied": 403, "permission_denied": 403, "page_changed": 409}.get(
                exc.code, 400
            )
            return Response({"code": exc.code}, status=status)

    return view
