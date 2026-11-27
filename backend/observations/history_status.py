"""A minimal import-attempt status, fenced by current account and source-use rights."""

from accounts.research_session import SessionCheck, lock_users_and_groups, request_transaction
from django.db.models import Q

from .disposition import PACKAGE_SCOPE
from .history import source_product
from .import_uci import PROFILE
from .models import ImportAttempt
from .normalization import CONCENTRATIONS
from .policy import REQUIRED, Denied, Scope, actor, applicable
from .s04_http import decode_cursor, encode_cursor, instant, page_size, params
from .uci_catalog import STATIONS, WINDOWS

ROUTE = "history-imports"


def can_review(user):
    return user.has_perm("observations.review_observation")


def may_read(user):
    if not (can_review(user) or user.has_perm("observations.import_history")):
        raise Denied("permission_denied")
    return user


def permit(product, now):
    grants = list(
        product.usegrant_set.filter(revoked_at=None, operation__in=("retain_normalized", "review"))
    )
    applicable(product, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"}, now, grants)
    # One status describes the complete fixed historical selection.
    for station in STATIONS:
        for start, end in WINDOWS:
            for indicator in CONCENTRATIONS:
                scope = Scope("beijing", station, indicator, start, end)
                for operation in ("retain_normalized", "review"):
                    applicable(product, operation, scope, REQUIRED, now, grants)


def project(attempt):
    return {
        "id": attempt.pk,
        "state": attempt.state,
        "dispatch": attempt.dispatch,
        "created_at": attempt.created_at,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "links": {"detail": f"/api/v1/admin/{ROUTE}/{attempt.pk}/"},
    }


def read(request, attempt_id=None):
    values = params(request.query_params, {"page_size", "cursor"} if attempt_id is None else set())
    user_id = request.user.pk
    with request_transaction():
        may_read(actor(user_id))
        product = source_product()
        lock_users_and_groups({user_id})
        auth = SessionCheck(request)
        user = may_read(actor(user_id))
        rows = ImportAttempt.objects.filter(product=product)
        if not can_review(user):
            rows = rows.filter(actor_id=user_id)
        permit(product, auth.now)
        if attempt_id is not None:
            attempt = rows.filter(pk=attempt_id).first()
            if attempt is None:
                raise Denied()
            result = project(attempt)
        else:
            size = page_size(values)
            cursor = decode_cursor(values.get("cursor"), ROUTE, user_id, size)
            if cursor:
                at = instant(cursor["created"])
                rows = rows.filter(Q(created_at__lt=at) | Q(created_at=at, pk__lt=cursor["id"]))
            page = list(rows.order_by("-created_at", "-pk")[: size + 1])
            continuation = None
            if len(page) > size:
                last = page[size - 1]
                position = {"created": last.created_at.isoformat(), "id": str(last.pk)}
                continuation = encode_cursor(ROUTE, user_id, size, position)
            result = {
                "results": [project(row) for row in page[:size]],
                "page": {"page_size": size, "next_cursor": continuation},
            }
        permit(product, auth.finish(()))
        return result


def lookup_submission(request, target, command):
    if command != {
        "profile": PROFILE,
        "stage_id": str(target),
        "reason": "historical_verification",
    }:
        raise Denied("not_found")
    user_id = request.user.pk
    product = source_product()
    lock_users_and_groups({user_id})
    session = SessionCheck(request)
    may_read(actor(user_id))
    attempt = (
        ImportAttempt.objects.filter(
            stage_id=target,
            stage__owner_id=user_id,
            stage__product=product,
            actor_id=user_id,
            product=product,
            reason=command["reason"],
        )
        .values_list("pk", flat=True)
        .first()
    )
    if attempt is None:
        raise Denied("not_found")
    permit(product, session.finish(()))
    return {"attempt_id": str(attempt)}
