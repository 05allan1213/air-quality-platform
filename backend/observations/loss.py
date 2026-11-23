"""Enforce historical interruptions of retention until physical reconciliation commits.

An affected range accepts no new lineage until swept. Thus the locked cutoff and the
product rights version delimit every old input without copying an observation value.
"""

from django.db.models import Min, Q

from .models import DispositionJob, UseGrant
from .policy import Denied, Scope, _grant_basis, clock, covers_fields

CONTROLLED = {"retain_normalized", "retain_raw", "backup", "restore"}


def historical_rows(job):
    rows = UseGrant.objects.filter(
        product_id=job.product_id, operation=job.boundary_grant.operation
    )
    # A backdated grant in a still-open transaction cannot supply evidence at a
    # previous cutoff. The boundary itself matters for an immediately effective deny.
    rows = rows.filter(Q(registered_at__lte=job.due_at) | Q(pk=job.boundary_grant_id))
    if job.boundary_basis is not None:
        rows = rows.filter(pk__in=job.boundary_basis)
    return list(rows.filter(Q(revoked_at=None) | Q(revoked_at__gt=job.due_at)).order_by("pk"))


def track(job, grant, product):
    job.boundary_grant = grant
    job.boundary_version = product.rights_version
    job.save(update_fields=["boundary_grant", "boundary_version"])
    if product.next_loss_at is None or job.due_at < product.next_loss_at:
        product.next_loss_at = job.due_at
        product.save(update_fields=["next_loss_at"])
    product.__dict__.pop("_loss_checks", None)


def due(product):
    return DispositionJob.objects.filter(
        product=product, boundary_grant__isnull=False, completed_at=None
    )


def settle(product, now):
    # Called under product lock *before* registering any grant, including a backdated allow.
    if product.next_loss_at is None or product.next_loss_at > now:
        return
    for job in (
        due(product)
        .filter(boundary_basis__isnull=True, due_at__lte=now)
        .select_related("boundary_grant")
    ):
        job.boundary_basis = [g.pk for g in historical_rows(job)]
        job.boundary_version = product.rights_version
        job.save(update_fields=["boundary_basis", "boundary_version"])
    product.__dict__.pop("_loss_checks", None)


def refresh(product):
    product.next_loss_at = (
        DispositionJob.objects.filter(product=product, completed_at=None)
        .filter(Q(boundary_grant__isnull=False) | Q(reason="legacy_boundary_review"))
        .aggregate(earliest=Min("due_at"))["earliest"]
    )
    product.last_disposed_at = clock()
    product.save(update_fields=["next_loss_at", "last_disposed_at"])
    product.__dict__.pop("_loss_checks", None)


def intersection(grant, scope):
    coords = []
    for left, right in (
        (grant.geography, scope.geography),
        (grant.object_code, scope.object_code),
        (grant.indicator, scope.indicator),
    ):
        if left != right and "*" not in (left, right):
            return None
        coords.append(right if left == "*" else left)
    start, end = max(scope.start, grant.data_from), min(scope.end, grant.data_until)
    return Scope(*coords, start, end) if start < end else None


def check_intersection(product, job, grants, operation, scope, fields):
    lost = job.boundary_grant
    if operation != lost.operation or (
        lost.effect == "deny" and lost.revoked_at and lost.revoked_at <= job.due_at
    ):
        return
    if not any(
        covers_fields(lost.fields, {f})
        or f == "*"
        or (lost.effect == "deny" and "*" in lost.fields)
        for f in fields
    ):
        return
    span = intersection(lost, scope)
    if span is not None:
        # Allow one complete independent basis on each covered sub-interval. A later
        # grant may have an earlier declared start, but is absent from the frozen set.
        cuts = sorted(
            {span.start, span.end}
            | {d for g in grants for d in (g.data_from, g.data_until) if span.start < d < span.end}
        )
        for left, right in zip(cuts, cuts[1:], strict=False):
            part = Scope(span.geography, span.object_code, span.indicator, left, right)
            _grant_basis(product, operation, part, fields, job.due_at, grants)


def require_continuous(product, operation, scope, fields, now, *, since=None):
    if operation not in CONTROLLED:
        return
    if since is None:
        if getattr(product, "next_loss_at", None) is None or product.next_loss_at > now:
            return
        # These instances are request/transaction local. Include future jobs in this
        # list so a cutoff crossed between a request and its final PG-clock check counts.
        if not hasattr(product, "_loss_checks"):
            if DispositionJob.objects.filter(
                product=product, reason="legacy_boundary_review", completed_at=None, due_at__lte=now
            ).exists():
                raise Denied()
            product._loss_checks = [
                (j, historical_rows(j)) for j in due(product).select_related("boundary_grant")
            ]
        entries = product._loss_checks
    else:
        if (product.last_disposed_at is None or since >= product.last_disposed_at) and (
            product.next_loss_at is None or product.next_loss_at > now
        ):
            return
        entries = (
            (j, historical_rows(j))
            for j in DispositionJob.objects.filter(
                product=product,
                boundary_grant__operation=operation,
                due_at__lte=now,
            )
            .filter(Q(completed_at__isnull=True) | Q(completed_at__gt=since) | Q(due_at__gt=since))
            .select_related("boundary_grant")
        )
    for job, grants in entries:
        if job.due_at <= now:
            check_intersection(product, job, grants, operation, scope, fields)
