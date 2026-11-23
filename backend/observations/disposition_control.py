"""Session-issued, expiring authorization to reconcile one product's due work."""

from datetime import timedelta

from accounts.api import error
from accounts.governance import command, receipt, replay
from accounts.management_api import rejection
from accounts.models import GovernanceAction
from accounts.research_session import SessionCheck
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .disposition import reconcile_product
from .models import DispositionJob, UseGrant
from .policy import Denied, actor, clock, locked_catalog_product, revoke_grant
from .s04_http import fingerprint

TTL = timedelta(minutes=3)
KIND = "disposition_product"


def pending(product, at):
    rows = list(
        DispositionJob.objects.filter(product=product, completed_at=None, due_at__lte=at)
        .order_by("pk")
        .values("id", "reason", "error_code")[:257]
    )
    if len(rows) > 256:
        raise Denied("precondition_conflict")
    return rows


def basis(rows):
    return fingerprint([[row["id"], row["reason"]] for row in rows])


@transaction.atomic
def preview(request, product_id):
    actor(request.user.pk, "manage_grants")
    product, _ = locked_catalog_product(product_id)
    session = SessionCheck(request)
    actor(request.user.pk, "manage_grants")
    rows = pending(product, session.finish(()))
    if request.method == "GET":
        return {
            "product": product.pk,
            "version": product.rights_version,
            "jobs": rows,
            "basis": basis(rows),
        }
    data = command(
        request.data,
        {"prepare_disposal"},
        {"due_reconciliation"},
        extra={"prepare_disposal": {"basis"}},
    )
    if (
        data["basis"] != basis(rows)
        or data["expected_version"] != product.rights_version
        or not rows
        or any(row["reason"] == "legacy_boundary_review" for row in rows)
    ):
        raise Denied("precondition_conflict")
    record = replay(request.user.pk, data, KIND, product.pk, parameter=data["basis"])
    if record is None:
        record = receipt(
            request.user.pk,
            data,
            KIND,
            product.pk,
            "due",
            "prepared",
            product.rights_version,
            product.rights_version,
            parameter=data["basis"],
        )
    if session.finish(()) >= record.created_at + TTL:
        raise Denied("precondition_conflict")
    return {"operation_id": str(record.pk), "expires_at": record.created_at + TTL}


@transaction.atomic(durable=True)
def execute(operation):
    issued = GovernanceAction.objects.filter(
        pk=operation, action="prepare_disposal", target_kind=KIND
    ).first()
    if issued is None:
        raise Denied("not_found")
    product, _ = locked_catalog_product(int(issued.target))
    # All account actions fence the same user row. No CLI-supplied username, path or DSN.
    if not get_user_model().objects.select_for_update().filter(pk=issued.actor_id).first():
        raise Denied("permission_denied")
    actor(issued.actor_id, "manage_grants")
    now = clock()
    rows = pending(product, now)
    if (
        not rows
        or basis(rows) != issued.parameter
        or product.rights_version != issued.before_version
        or now >= issued.created_at + TTL
        or GovernanceAction.objects.filter(
            action="execute_disposal", origin=str(operation)
        ).exists()
    ):
        raise Denied("precondition_conflict")
    reconcile_product(product.pk, expected_jobs={row["id"] for row in rows})
    if DispositionJob.objects.filter(
        pk__in=[row["id"] for row in rows], completed_at=None
    ).exists():
        raise Denied("cleanup_failed")
    product.refresh_from_db(fields=["rights_version"])
    record = receipt(
        issued.actor_id,
        {"action": "execute_disposal", "reason_code": "due_reconciliation", "request_id": None},
        KIND,
        product.pk,
        "prepared",
        "completed",
        issued.before_version,
        product.rights_version,
        origin=str(operation),
    )
    return str(record.pk)


@transaction.atomic
def withdraw(request, grant_id):
    actor(request.user.pk, "manage_grants")
    product_id = UseGrant.objects.filter(pk=grant_id).values_list("product_id", flat=True).first()
    if product_id is None:
        raise Denied("not_found")
    product, _ = locked_catalog_product(product_id)
    session = SessionCheck(request)
    actor(request.user.pk, "manage_grants")
    if request.method == "GET":
        return {
            "grant": grant_id,
            "product": product.pk,
            "version": product.rights_version,
            "state": "withdrawn" if UseGrant.objects.get(pk=grant_id).revoked_at else "registered",
        }
    data = command(
        request.data, {"withdraw"}, {"terms_ended", "retention_withdrawn", "rights_review"}
    )
    previous = replay(request.user.pk, data, "use_grant", grant_id)
    if previous is None:
        if (
            data["expected_version"] != product.rights_version
            or UseGrant.objects.get(pk=grant_id).revoked_at
        ):
            raise Denied("precondition_conflict")
        revoke_grant(request.user.pk, grant_id, data["reason_code"])
        product.refresh_from_db(fields=["rights_version"])
        previous = receipt(
            request.user.pk,
            data,
            "use_grant",
            grant_id,
            "registered",
            "withdrawn",
            data["expected_version"],
            product.rights_version,
        )
    session.finish(())
    return {"action_id": str(previous.pk), "version": previous.after_version, "state": "withdrawn"}


@never_cache
@api_view(["GET", "POST"])
def disposals(request, product_id):
    if request.query_params or not 0 < product_id < 2**63:
        return error("invalid_request", 400)
    try:
        return Response(preview(request, product_id))
    except Denied as exc:
        return rejection(exc)


@never_cache
@api_view(["GET", "POST"])
def grants(request, grant_id):
    if request.query_params or not 0 < grant_id < 2**63:
        return error("invalid_request", 400)
    try:
        return Response(withdraw(request, grant_id))
    except Denied as exc:
        return rejection(exc)


urlpatterns = [
    path("admin/disposition-products/<int:product_id>/", disposals),
    path("admin/grants/<int:grant_id>/actions/", grants),
]
