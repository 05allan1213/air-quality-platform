"""Evaluate independent complete grants under the same product lock as data changes."""

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from .models import Change, Product, UseGrant

OPERATIONS = {
    "acquire_file": "platform",
    "process_local": "platform",
    "retain_raw": "platform",
    "retain_normalized": "platform",
    "display": "local_authenticated",
    "review": "operator",
    "analyze": "operator",
    "export": "operator",
    "backup": "platform",
    "restore": "platform",
    "redistribute": "operator",
    "public_display": "public",
    "acquire_online": "platform",
    "screenshot": "operator",
    "export_aggregate": "operator",
}
REQUIRED = {"normalized", "provenance"}
RAW = REQUIRED | {"raw_field"}


class Denied(ValueError):
    def __init__(self, code="use_denied"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Scope:
    geography: str
    object_code: str
    indicator: str
    start: date
    end: date

    def serialize(self):
        return {
            "geography": self.geography,
            "object_code": self.object_code,
            "indicator": self.indicator,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }

    @classmethod
    def from_issue(cls, fields):
        if not fields:
            return cls("*", "*", "*", date.min, date.max)
        return cls(
            fields["geography"],
            fields["object_code"],
            fields["indicator"],
            date.fromisoformat(fields["start"]),
            date.fromisoformat(fields["end"]),
        )


@dataclass(frozen=True)
class Basis:
    ids: tuple
    until: object
    attributions: tuple


def clock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def locked_product(product_id):
    try:
        return (
            Product.objects.select_for_update(of=("self",))
            .select_related("source")
            .get(pk=product_id)
        )
    except Product.DoesNotExist:
        raise Denied() from None


def actor(actor_id, permission=None):
    user = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if user is None or (permission and not user.has_perm("observations." + permission)):
        raise Denied("permission_denied")
    return user


def applicable(product, operation, scope, fields, now, rows=None):
    """One allow must cover the whole request. A partial intersecting deny vetoes it."""
    wanted = set(fields)
    if not wanted or scope.start >= scope.end or operation not in OPERATIONS:
        raise Denied()
    if rows is None:
        rows = UseGrant.objects.filter(
            product=product, operation=operation, revoked_at__isnull=True
        )
    allows = []
    for grant in rows:
        if (
            grant.operation != operation
            or grant.revoked_at
            or grant.valid_from > now
            or grant.audience != OPERATIONS[operation]
            or (grant.valid_until and now >= grant.valid_until)
            or (grant.retain_until and now >= grant.retain_until)
        ):
            continue
        dimensions = (
            (grant.geography, scope.geography),
            (grant.object_code, scope.object_code),
            (grant.indicator, scope.indicator),
        )
        if grant.effect == "deny" or grant.restrictions:
            if (
                all(a == "*" or b == "*" or a == b for a, b in dimensions)
                and grant.data_from < scope.end
                and scope.start < grant.data_until
                and ("*" in grant.fields or wanted.intersection(grant.fields))
            ):
                raise Denied()
        elif (
            all(a == "*" or a == b for a, b in dimensions)
            and grant.data_from <= scope.start
            and grant.data_until >= scope.end
            and ("*" in grant.fields or wanted <= set(grant.fields))
            and not grant.restrictions
        ):
            # A stopped local machine cannot enforce physical erasure at a hard deadline.
            if operation.startswith("retain_") and (
                grant.retain_until or (grant.valid_until and grant.expiry_mode != "recoverable")
            ):
                continue
            allows.append(grant)
    if not allows:
        raise Denied()
    return Basis(
        tuple(g.pk for g in allows),
        min((g.valid_until for g in allows if g.valid_until), default=None),
        tuple(dict.fromkeys(g.attribution for g in allows if g.attribution)),
    )


def require_use(actor_id, product, operation, scope, fields):
    product = locked_product(product.pk)
    actor(
        actor_id,
        {
            "review": "review_observation",
            "analyze": "review_observation",
            "acquire_file": "import_history",
            "process_local": "import_history",
        }.get(operation),
    )
    return applicable(product, operation, scope, fields, clock())


def bump(product, kind):
    product.rights_version += 1
    product.save(update_fields=["rights_version"])
    Change.objects.create(product=product, kind=kind, rights_version=product.rights_version)


@transaction.atomic
def register_grant(actor_id, product_id, **terms):
    from .disposition import schedule

    product = locked_product(product_id)
    user = actor(actor_id, "manage_grants")
    if (
        terms.get("operation") not in OPERATIONS
        or terms.get("audience") != OPERATIONS[terms["operation"]]
        or not terms.get("terms")
        or not terms.get("terms_version")
        or not isinstance(terms.get("fields"), list)
        or not terms["fields"]
        or not all(isinstance(v, str) and v for v in terms["fields"])
    ):
        raise Denied("invalid_grant")
    grant = UseGrant(product=product, confirmed_by=user, **terms)
    grant.full_clean(
        exclude=[
            k
            for k in ("valid_until", "retain_until", "revoked_at", "revoked_by")
            if getattr(grant, k) is None
        ]
        + (["restrictions"] if not grant.restrictions else [])
    )
    grant.save()
    bump(product, "grant_registered")
    now = clock()
    for cutoff in {
        grant.valid_until,
        grant.retain_until,
        grant.valid_from if grant.effect == "deny" or grant.restrictions else None,
    } - {None}:
        schedule(product, max(cutoff, now), "rights_boundary")
    return grant


@transaction.atomic
def revoke_grant(actor_id, grant_id, reason):
    from .disposition import schedule

    product = locked_product(UseGrant.objects.values_list("product_id", flat=True).get(pk=grant_id))
    user = actor(actor_id, "manage_grants")
    grant = UseGrant.objects.get(pk=grant_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 100:
        raise Denied("invalid_reason")
    if not grant.revoked_at:
        grant.revoked_at, grant.revoked_by, grant.revoke_reason = clock(), user, reason
        grant.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
        bump(product, "grant_revoked")
        schedule(product, grant.revoked_at, "revocation")
    return product.pk
