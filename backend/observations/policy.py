"""Evaluate independent complete grants under the same product lock as data changes."""

from dataclasses import dataclass
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from .models import Change, Product, Source, UseGrant

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
LOCAL_AUTHENTICATED = "local_authenticated"
OPERATION_AUDIENCES = frozenset(OPERATIONS.items()) | {
    (operation, LOCAL_AUTHENTICATED) for operation in ("analyze", "export", "export_aggregate")
}
REQUIRED = {"normalized", "provenance"}
RAW = REQUIRED | {"raw_field"}
EXPLICIT_FIELDS = frozenset(
    {
        "overview_summary",
        "research_coverage",
        "research_summary",
        "export_manifest",
        "statistics_baseline",
    }
)


def accepts_audience(operation, audience):
    return (
        isinstance(operation, str)
        and isinstance(audience, str)
        and (operation, audience) in OPERATION_AUDIENCES
    )


def covers_fields(granted, wanted):
    granted = set(granted)
    return all(
        field in granted
        or ("*" in granted and field not in EXPLICIT_FIELDS and not field.startswith("csv_"))
        for field in wanted
    )


def batch_issue_fields(product):
    if product.source.namespace in ("cnemc", "cnemc.fixture"):
        return REQUIRED | {"collection_ledger"}
    return REQUIRED


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
    until: datetime | None
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


def locked_catalog_product(product_id):
    """Serialize directory retention and amendments with all jobs of a CNEMC source."""
    source_id = (
        Product.objects.filter(pk=product_id, source__namespace__in=("cnemc", "cnemc.fixture"))
        .values_list("source_id", flat=True)
        .first()
    )
    if source_id is None:
        return locked_product(product_id), ()
    source = Source.objects.select_for_update().get(pk=source_id)
    siblings = list(Product.objects.select_for_update().filter(source=source).order_by("pk"))
    for product in siblings:
        product.source = source
        if product.pk == product_id:
            target = product
    return target, siblings


def actor(actor_id, permission=None):
    user = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if user is None or (permission and not user.has_perm("observations." + permission)):
        raise Denied("permission_denied")
    return user


def applicable(product, operation, scope, fields, now, rows=None, *, audience=None):
    """One allow must cover the whole request. A partial intersecting deny vetoes it."""
    wanted = set(fields)
    if audience is None and isinstance(operation, str):
        audience = OPERATIONS.get(operation)
    if not wanted or scope.start >= scope.end or not accepts_audience(operation, audience):
        raise Denied()
    if rows is None:
        rows = UseGrant.objects.filter(
            product=product, operation=operation, audience=audience, revoked_at__isnull=True
        ).order_by("pk")
    allows = []
    for grant in rows:
        if (
            grant.product_id != product.pk
            or grant.operation != operation
            or grant.revoked_at
            or grant.valid_from > now
            or grant.audience != audience
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
                and ("*" in grant.fields or "*" in wanted or wanted.intersection(grant.fields))
            ):
                raise Denied()
        elif (
            all(a == "*" or a == b for a, b in dimensions)
            and grant.data_from <= scope.start
            and grant.data_until >= scope.end
            and covers_fields(grant.fields, wanted)
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
        min((t for g in allows for t in (g.valid_until, g.retain_until) if t), default=None),
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
            "acquire_online": "run_collection",
            "process_local": "import_history",
        }.get(operation),
    )
    return applicable(product, operation, scope, fields, clock())


def bump(product, kind):
    product.rights_version += 1
    product.save(update_fields=["rights_version"])
    Change.objects.create(product=product, kind=kind, rights_version=product.rights_version)


def check_allocation(product, terms):
    """The caller holds the source lock. Previously assigned shares never replenish."""
    share = terms["max_total_requests"]
    allocation = terms.setdefault(
        "validation_allocation", {"basis": terms["terms_version"], "total": share}
    )
    if not (
        isinstance(allocation, dict)
        and set(allocation) == {"basis", "total"}
        and isinstance(allocation["basis"], str)
        and 0 < len(allocation["basis"].strip()) <= 240
        and allocation["basis"].isprintable()
        and type(allocation["total"]) is int
        and share <= allocation["total"] <= 2**31 - 1
    ):
        raise Denied("invalid_validation_allocation")
    used = 0
    for version, previous, size in UseGrant.objects.filter(
        product__source_id=product.source_id, max_total_requests__isnull=False
    ).values_list("terms_version", "validation_allocation", "max_total_requests"):
        if version == terms["terms_version"] or previous.get("basis") == allocation["basis"]:
            if previous != allocation:
                raise Denied("invalid_validation_allocation")
            used += size
    if used + share > allocation["total"]:
        raise Denied("invalid_validation_allocation")


@transaction.atomic
def register_grant(actor_id, product_id, **terms):
    from .disposition import schedule

    product, _ = locked_catalog_product(product_id)
    user = actor(actor_id, "manage_grants")
    if (
        not accepts_audience(terms.get("operation"), terms.get("audience"))
        or not terms.get("terms")
        or not terms.get("terms_version")
        or not isinstance(terms.get("fields"), list)
        or not terms["fields"]
        or not all(isinstance(v, str) and v for v in terms["fields"])
    ):
        raise Denied("invalid_grant")
    if terms.get("collection_limits") is not None:
        from collection.admission import validate_limits

        if terms["operation"] != "acquire_online":
            raise Denied("invalid_grant")
        validate_limits(terms["collection_limits"])
    endpoints = terms.get("endpoints", [])
    maximum = terms.get("max_total_requests")
    if (
        not isinstance(endpoints, list)
        or "validation_allocation" in terms
        and maximum is None
        or (endpoints or maximum is not None)
        and (
            terms["operation"] != "acquire_online"
            or product.source.namespace not in ("cnemc", "cnemc.fixture")
        )
    ):
        raise Denied("invalid_grant")
    if terms["operation"] == "acquire_online" and product.source.namespace in (
        "cnemc",
        "cnemc.fixture",
    ):
        from collection.adapters.cnemc import PRODUCTS

        allowed = {PRODUCTS.get(product.code)} - {None}
        if product.code == "station-national-hour":
            allowed.add("A10")
        effect = terms.get("effect", "allow")
        if (
            any(not isinstance(endpoint, str) or endpoint not in allowed for endpoint in endpoints)
            or effect == "allow"
            and (not endpoints or terms.get("collection_limits") is None)
            or maximum is not None
            and (
                type(maximum) is not int
                or not 1 <= maximum <= 2**31 - 1
                or effect != "allow"
                or terms.get("valid_until") is None
            )
        ):
            raise Denied("invalid_grant")
        terms["endpoints"] = sorted(set(endpoints))
        if maximum is not None:
            check_allocation(product, terms)
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

    product, _ = locked_catalog_product(
        UseGrant.objects.values_list("product_id", flat=True).get(pk=grant_id)
    )
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
