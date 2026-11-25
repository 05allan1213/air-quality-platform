"""Private, current-authority audit projection for one explicitly identified object."""

from datetime import timedelta
from uuid import UUID

from django.db import DatabaseError
from django.db.models import Q
from django.urls import path
from django.views.decorators.cache import never_cache
from observations.policy import Denied
from observations.queries import pk
from observations.s04_http import decode_cursor, encode_cursor, instant, params
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .api import error
from .limits import limited
from .research_session import SessionCheck, lock_users_and_groups, reject, request_transaction
from .s08_object_audit_scope import authorize, load_target

ROUTE = "object-audit"
PARAMS = {"kind", "id", "start", "end", "page_size", "cursor"}
KINDS = {"job", "run", "quality_issue", "station", "grant"}


def uuid_id(value):
    try:
        result = UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise Denied("invalid_request") from None
    if str(result) != value:
        raise Denied("invalid_request")
    return result


def selection(query):
    values = params(query, PARAMS)
    if not {"kind", "id", "start", "end"} <= values.keys():
        raise Denied("invalid_request")
    kind = values["kind"]
    if kind not in KINDS:
        raise Denied("invalid_request")
    target = uuid_id(values["id"]) if kind == "run" else pk(values["id"])
    start, end = (instant(values[key]) for key in ("start", "end"))
    size = pk(values.get("page_size", "25"))
    if not start < end <= start + timedelta(days=30) or size > 50:
        raise Denied("invalid_request")
    return kind, target, start, end, size, values.get("cursor")


def position(value, collection, start, end):
    try:
        at = instant(value["at"])
        key = pk(value["id"]) if collection else uuid_id(value["id"])
        if not start <= at < end:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise Denied("invalid_cursor") from None
    return at, key


def project(record, user_id):
    return {
        "at": record.created_at,
        "action": record.action,
        "outcome": record.outcome,
        "actor_relation": "system"
        if record.actor_id is None
        else "self"
        if record.actor_id == user_id
        else "other",
        "before_version": record.before_version,
        "after_version": record.after_version,
    }


def read(request, query):
    kind, target_id, start, end, size, token = selection(query)
    with request_transaction():
        user_id = request.user.pk
        authorize(user_id, kind)
        target = load_target(kind, target_id)
        lock_users_and_groups({user_id})
        session = SessionCheck(request)
        permissions = authorize(user_id, kind)
        target.check(session.now, permissions)
        binding = [session.key, kind, str(target_id), start, end, size]
        cursor = decode_cursor(token, ROUTE, user_id, binding)
        stamp = target.timestamp
        rows = target.rows().filter(**{f"{stamp}__gte": start, f"{stamp}__lt": end})
        if cursor is not None:
            at, key = position(cursor, target.collection, start, end)
            rows = rows.filter(Q(**{f"{stamp}__lt": at}) | Q(**{stamp: at, "pk__lt": key}))
        visible = []
        for row in rows.order_by(f"-{stamp}", "-pk").iterator(chunk_size=50):
            record = target.permit(row, session.now, permissions)
            if record is not None:
                visible.append((row, record))
                if len(visible) > size:
                    break

        finished = session.finish(())
        current = authorize(user_id, kind)
        target.check(finished, current)
        # The extra row's authority also determines what may be said about the page tail.
        for row, record in visible:
            if target.permit(row, finished, current) != record:
                raise Denied()
        continuation = None
        if len(visible) > size:
            last = visible[size - 1][0]
            continuation = encode_cursor(
                ROUTE,
                user_id,
                binding,
                {"at": getattr(last, stamp).isoformat(), "id": str(last.pk)},
            )
        return {
            "results": [project(record, user_id) for _, record in visible[:size]],
            "page": {"page_size": size, "next_cursor": continuation},
        }


@never_cache
@api_view(["GET"])
def object_audit(request):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        return Response(read(request, request.query_params))
    except Denied as exc:
        return reject(exc)
    except DatabaseError:
        return error("database_unavailable", 503)


urlpatterns = [path("admin/object-audit/", object_audit)]
