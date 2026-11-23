"""Same-origin backup and restore intent endpoints; never a file download."""

from accounts.api import error
from accounts.management_api import rejection
from accounts.research_session import SessionCheck
from django.db import DatabaseError, transaction
from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .backup import (
    phase_timeout,
    prepare,
    publish,
    runtime_actor,
    segment_lock,
    verify_rights,
)
from .backup_authority import configured_custodian, proof_observation, wire
from .backup_restore import lock_intent, prepare_restore
from .policy import Denied


@never_cache
@api_view(["POST"])
def prepare_segment(request, product_id):
    try:
        data = request.data
        if (
            request.query_params
            or not isinstance(data, dict)
            or set(data) not in ({"scope"}, {"scope", "raw"})
        ):
            raise Denied("invalid_request")
        segment = prepare(request, product_id, data["scope"], raw=data.get("raw", False))
        return Response({"operation_id": segment.pk, "state": segment.state}, status=201)
    except Denied as exc:
        return rejection(exc)
    except (DatabaseError, OSError):
        return error("backup_unavailable", 503)


@never_cache
@api_view(["POST"])
def publish_segment(request, segment_id):
    try:
        if request.query_params or request.data != {}:
            raise Denied("invalid_request")
        segment = publish(request, segment_id)
        return Response({"operation_id": segment.pk, "state": segment.state})
    except Denied as exc:
        return rejection(exc)
    except (DatabaseError, OSError):
        return error("backup_unavailable", 503)


@never_cache
@api_view(["POST"])
def create_restore(request, segment_id):
    try:
        if (
            request.query_params
            or not isinstance(request.data, dict)
            or set(request.data) != {"target"}
        ):
            raise Denied("invalid_request")
        intent = prepare_restore(request, segment_id, request.data["target"])
        return Response({"operation_id": intent.pk, "expires_at": intent.expires_at}, status=201)
    except Denied as exc:
        return rejection(exc)
    except (DatabaseError, OSError):
        return error("restore_unavailable", 503)


@never_cache
@api_view(["GET"])
def authority_material(request, action, operation_id):
    """A custodian reviews this package against independently held original terms."""
    try:
        if request.query_params or action not in {"publish", "restore"}:
            raise Denied("invalid_request")
        with transaction.atomic(durable=True):
            phase_timeout()
            if action == "restore":
                product, segment, intent, check, _ = lock_intent(
                    operation_id, material_request=request
                )
                if (
                    intent.consumed_at
                    or intent.expires_at <= check.finish(())
                    or segment.state != "available"
                ):
                    raise Denied("precondition_conflict")
                target, expiry = intent.target, intent.expires_at
            else:
                product, segment = segment_lock(operation_id)
                if segment.state != "preparing" or not segment.manifest:
                    raise Denied("precondition_conflict")
                target, expiry = None, segment.expires_at
                if request.user.pk != configured_custodian():
                    raise Denied("permission_denied")
                runtime_actor(request.user.pk, "observations.manage_grants")
            check = SessionCheck(request)
            now = check.finish(())
            expiry = min(expiry, verify_rights(product, segment, now, restore=action == "restore"))
            return Response(
                wire(
                    {
                        "current": proof_observation(product),
                        "manifest": segment.manifest,
                        "action": action,
                        "operation": operation_id,
                        "target": target,
                        "expiry": expiry,
                        "observed_at": now,
                    }
                )
            )
    except Denied as exc:
        return rejection(exc)
    except (DatabaseError, OSError):
        return error("authority_unavailable", 503)


urlpatterns = [
    path("admin/backup-products/<int:product_id>/segments/", prepare_segment),
    path("admin/backup-segments/<uuid:segment_id>/publish/", publish_segment),
    path("admin/backup-segments/<uuid:segment_id>/restore-intents/", create_restore),
    path("admin/backup-authority/<str:action>/<uuid:operation_id>/", authority_material),
]
