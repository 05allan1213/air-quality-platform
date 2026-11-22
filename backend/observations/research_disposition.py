"""Dispose only frozen dependencies intersecting the currently locked product."""

from django.db import transaction

from .policy import Denied, Scope, applicable, clock
from .research_access import affects_basis, overlap


def retained(product, deps, now, grants, field):
    checks = [
        d
        for d in deps
        if d["product"] == product.pk
        and d["operation"] == "retain_normalized"
        and field in d["fields"]
    ]
    if not checks:
        return False
    try:
        for d in checks:
            applicable(
                product,
                "retain_normalized",
                Scope.from_issue(d["scope"]),
                d["fields"],
                now,
                grants,
                audience="platform",
            )
    except Denied:
        return False
    return True


def affected(product, dep, grant, now):
    scope = Scope.from_issue(dep["scope"])
    if (
        dep["product"] != grant.product_id
        or dep["operation"] != grant.operation
        or dep["audience"] != grant.audience
        or not overlap(grant, scope, dep["fields"])
    ):
        return False
    if grant.revoked_at:
        return grant.pk in dep["ids"]
    return affects_basis(product, scope, dep["fields"], grant, max(now, grant.valid_from))


def reconcile_private(*products, now, change=None, lost_cities=(), lost_epochs=()):
    from accounts.models import ExportTicket, SavedQuery

    # Callers hold the upstream locks. Lock each private table's whole affected set
    # together so a shared ticket cannot reverse row order across siblings or sources.
    by_id = {product.pk: product for product in products}
    grants = {
        product.pk: list(product.usegrant_set.filter(revoked_at__isnull=True).order_by("pk"))
        for product in products
    }
    saved = list(
        SavedQuery.objects.filter(product_id__in=by_id)
        .exclude(baseline="")
        .select_for_update()
        .order_by("pk")
    )
    affected_tickets = ExportTicket.products.through.objects.filter(product_id__in=by_id)
    tickets = list(
        ExportTicket.objects.filter(pk__in=affected_tickets.values("exportticket_id"))
        .select_for_update()
        .order_by("pk")
    )
    for row in saved:
        product = by_id[row.product_id]
        lost_target = bool(lost_cities or lost_epochs) and any(
            t.city_id in lost_cities or t.site_epoch_id in lost_epochs for t in row.targets.all()
        )
        if lost_target or not retained(
            product, row.baseline_basis, now, grants[product.pk], "statistics_baseline"
        ):
            row.baseline, row.baseline_basis, row.baseline_until = "", [], None
            row.save(update_fields=["baseline", "baseline_basis", "baseline_until"])
    for ticket in tickets:
        deps = {
            group["selection"]["product"]: []
            for group in ticket.groups
            if group["selection"]["product"] in by_id
        }
        for dep in ticket.basis:
            if dep["product"] in deps:
                deps[dep["product"]].append(dep)
        deleted_identity = any(
            g["selection"]["product"] in by_id
            and (
                set(g["selection"].get("cities", [])).intersection(lost_cities)
                or set(g["selection"].get("site_epochs", [])).intersection(lost_epochs)
            )
            for g in ticket.groups
        )
        if deleted_identity or any(
            not retained(by_id[pk], entries, now, grants[pk], "export_manifest")
            for pk, entries in deps.items()
        ):
            ticket.delete()
        elif (
            change
            and not ticket.invalidated_at
            and ticket.expires_at > now
            and any(
                affected(by_id[change.product_id], dep, change, now)
                for dep in deps.get(change.product_id, ())
            )
        ):
            if change.valid_from > now and not change.revoked_at:
                if change.valid_from < ticket.expires_at:
                    ticket.expires_at = change.valid_from
                    ticket.save(update_fields=["expires_at"])
            else:
                ticket.invalidated_at = now
                ticket.save(update_fields=["invalidated_at"])


@transaction.atomic
def cleanup_expired(limit=200):
    from accounts.models import ExportTicket, SavedQuery

    now = clock()
    expired = list(
        SavedQuery.objects.select_for_update(skip_locked=True)
        .filter(baseline_until__lte=now)
        .exclude(baseline="")
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    SavedQuery.objects.filter(pk__in=expired).update(
        baseline="", baseline_basis=[], baseline_until=None
    )
    ids = list(
        ExportTicket.objects.select_for_update(skip_locked=True)
        .filter(expires_at__lte=now)
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    ExportTicket.objects.filter(pk__in=ids).delete()
    return len(ids), len(expired)
