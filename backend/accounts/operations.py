from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.views.decorators.cache import never_cache
from observations.policy import Denied, clock
from redis import Redis, RedisError
from redis.backoff import NoBackoff
from redis.retry import Retry
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .management_api import rejection
from .models import ServicePulse
from .operations_first_write import FirstWrite, Unproved
from .research_session import SessionCheck, lock_users_and_groups, reject, request_transaction


def proof(state, at, kind, *, reason="", ttl=None):
    return {
        "state": state,
        "observed_at": at,
        "evidence_kind": kind,
        "valid_until": at + timedelta(seconds=ttl or settings.OPERATIONS_TTL_SECONDS)
        if at
        else None,
        "reason_code": reason,
    }


def broker_probe():
    uri = settings.CELERY_BROKER_URL.replace("redis+socket://", "unix://", 1)
    try:
        with Redis.from_url(
            uri, socket_connect_timeout=0.5, socket_timeout=0.5, retry=Retry(NoBackoff(), 0)
        ) as broker:
            pong = broker.ping()
    except (RedisError, OSError, ValueError):
        pong = False
    return proof(
        "ok" if pong else "unavailable",
        clock(),
        "redis_ping",
        reason="" if pong else "probe_failed",
    )


def pulse(component, row, now):
    kind = "scheduler_tick" if component == "beat" else "worker_heartbeat"
    if row is None or (component == "worker" and row.instance != settings.OPERATIONS_WORKER):
        row = None
        result = proof("unknown", None, kind, reason="no_evidence")
    else:
        result = proof("ok", row.observed_at, kind)
        if row.observed_at > now or result["valid_until"] <= now:
            result.update(state="unknown", reason_code="stale_evidence")
    if component != "worker":
        return result
    queue = settings.CELERY_TASK_ROUTES["collection.execute_run"]["queue"]
    if row is None or row.subscribed_queues is None:
        readiness = proof("unknown", None, "worker_subscription", reason="no_evidence")
    else:
        readiness = proof(
            result["state"], row.observed_at, "worker_subscription", reason=result["reason_code"]
        )
        if readiness["state"] == "ok" and queue not in row.subscribed_queues:
            readiness.update(state="unavailable", reason_code="queue_not_subscribed")
    result.update(
        queue_readiness=readiness | {"queue": queue},
        last_consumption=(
            {"observed_at": row.consumed_at, "queue": row.queue}
            if row is not None and row.consumed_at and row.consumed_at <= now
            else None
        ),
    )
    return result


@never_cache
@api_view(["GET"])
def health(request):
    from collection.queries import read_window

    try:
        params = request.query_params
        if (
            set(params) - {"window", "object_code", "indicator", "include"}
            or any(len(params.getlist(k)) != 1 for k in params)
            or (params and "window" not in params)
            or ("include" in params and params["include"] != "first_valid_write")
        ):
            raise Denied("invalid_request")
        if not request.user.has_perm("accounts.diagnose_operations"):
            raise Denied("permission_denied")
        broker = broker_probe()
        enhanced = "include" in params
        with request_transaction() if enhanced else transaction.atomic():
            projected = evidence = latest = None
            first_write_state = "unavailable"
            window_rechecks = []
            if "window" in params:
                pk = params["window"]
                if not pk.isascii() or not pk.isdigit() or len(pk) > 18:
                    raise Denied("invalid_request")
                selectors = {k: params[k] for k in ("object_code", "indicator") if k in params}
                projected = read_window(
                    request.user.pk, int(pk), selectors, rechecks=window_rechecks
                )
            if enhanced:
                lock_users_and_groups([request.user.pk])
            check = SessionCheck(request)
            first_write_page_truncated = bool(projected and projected.get("next_after"))
            if (
                enhanced
                and not first_write_page_truncated
                and projected["state"] not in {"disposed", "not_applicable"}
            ):
                evidence = FirstWrite(
                    projected["window_id"],
                    selectors,
                    check.now,
                    [cell["item_key"] for cell in projected.get("cells", ())],
                )
                try:
                    latest = evidence.latest()
                except Unproved:
                    pass
                else:
                    first_write_state = "none" if latest is None else "known"
            rows = ServicePulse.objects.in_bulk(["worker", "beat"])
            user = get_user_model().objects.get(pk=request.user.pk)
            if not user.has_perm("accounts.diagnose_operations") or (
                enhanced and not user.has_perm("observations.review_observation")
            ):
                raise Denied("permission_denied")
            now = check.finish(())
            for recheck in window_rechecks:
                recheck(now)
            if evidence is not None:
                evidence.finish(now)
            components = {
                "web": proof("ok", now, "http_request"),
                "pg": proof("ok", now, "postgres_clock"),
                "redis": broker,
                **{k: pulse(k, rows.get(k), now) for k in ("worker", "beat")},
            }
            window = window_state(projected, now) if projected else None
            if enhanced:
                window.update(
                    window_evidence(
                        projected, first_write_state, latest, first_write_page_truncated
                    )
                )
        return Response(
            {
                "components": components,
                "window": window,
            }
        )
    except Denied as exc:
        if exc.code == "database_unavailable":
            return reject(exc)
        return rejection(exc)


def window_state(projected, now):
    # Only the already authorized page contributes, never infer a hidden total.
    runs = projected.get("runs", ())
    pending = [r for r in runs if r["status"] == "pending"]
    budget = [r for r in pending if r["error_code"] == "budget_exhausted"]
    dispatch = [r for r in pending if r["error_code"] != "budget_exhausted"]
    sent = [r for r in dispatch if r["dispatch"] == "sent" and r["started_at"] is None]
    return {
        "window_id": projected["window_id"],
        "state": projected["state"],
        "result": projected.get("result"),
        "coverage": projected["coverage"],
        "truncated": projected.get("next_run_after") is not None,
        "visible_pending": len(pending),
        "visible_sent_not_started": len(sent),
        "visible_quota_wait": len(budget),
        "visible_unconfirmed_dispatch": sum(r["dispatch"] == "unconfirmed" for r in dispatch),
        "oldest_visible_pending_age_seconds": (
            max(0, int((now - min(r["created_at"] for r in pending)).total_seconds()))
            if pending
            else None
        ),
        "next_visible_quota_check_at": min(
            (r["next_retry_at"] for r in budget if r["next_retry_at"] is not None), default=None
        ),
        "visible_retry": sum(r.get("next_retry_at") is not None for r in runs),
        "visible_running": sum(r.get("status") == "running" for r in runs),
    }


def window_evidence(projected, state, latest, page_truncated):
    # No error text or counters from outside the authorized Run page.
    runs = projected.get("runs", ())
    return {
        "first_valid_write_state": state,
        "first_valid_write_page_truncated": page_truncated,
        "latest_first_valid_write_at": latest,
        "oldest_visible_pending_created_at": min(
            (r["created_at"] for r in runs if r["status"] == "pending"), default=None
        ),
        "latest_visible_fetched_at": max(
            (r["fetched_at"] for r in runs if r["fetched_at"] is not None), default=None
        ),
        "visible_errors": [
            {k: r[k] for k in ("error_code", "result", "request_state")}
            for r in runs
            if r["error_code"]
        ],
    }
