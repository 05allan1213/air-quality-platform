"""Locally maintained, product-scoped city search aliases."""

import unicodedata
from itertools import groupby
from unicodedata import category

from accounts.assigned_permissions import MAINTAIN_CITY_ALIAS, has_assigned_permission
from accounts.governance import command, receipt, replay
from accounts.models import GovernanceAction
from accounts.research_session import SessionCheck, request_transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Max, Q
from django.utils.crypto import salted_hmac

from .catalog import directory_span
from .models import City, CityIdentifier, CitySearchAlias, Product, Source
from .policy import Denied, applicable, locked_catalog_product

KIND = "city_search_alias"


def _canonical_alias(value):
    if not isinstance(value, str):
        raise Denied("invalid_request")
    if any(category(char)[0] == "C" for char in value):
        raise Denied("invalid_request")
    value = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(value) <= 32:
        raise Denied("invalid_request")
    if any(category(char)[0] not in "LN" and char not in " -" for char in value):
        raise Denied("invalid_request")
    return value


def _command(data):
    value = command(
        data,
        {"set", "clear"},
        {"search_correction"},
        extra={"set": {"search_alias"}, "clear": {"search_alias"}},
    )
    if value["action"] == "set":
        value["search_alias"] = _canonical_alias(value["search_alias"])
    elif value["search_alias"] != "":
        raise Denied("invalid_request")
    return value


def _principal(request):
    auth = SessionCheck(request)
    group_ids = Group.objects.filter(user__pk=auth.user_id).values("pk")
    list(Group.objects.select_for_update().filter(pk__in=group_ids).order_by("pk"))
    _require_operator(auth.user_id)
    return auth


def _require_operator(user_id):
    user = get_user_model().objects.get(pk=user_id)
    if not user.is_staff or not has_assigned_permission(user, MAINTAIN_CITY_ALIAS):
        raise Denied("permission_denied")


def _finish(auth):
    now = auth.finish(())
    _require_operator(auth.user_id)
    return now


def _scopes(product, city, identifiers):
    return [
        directory_span(product, identifier.code, city.geography, identifier.active_period)
        for identifier in identifiers
    ]


def _allowed(product, scopes, now, grants=None):
    return bool(scopes) and all(
        _can_use(product, operation, scope, now, grants)
        for scope in scopes
        for operation in ("review", "retain_normalized")
    )


def _require_scope(product, scopes, now):
    if not _allowed(product, scopes, now):
        raise Denied("not_found")


def _authorized_target(request, product_id, city_id):
    if not (0 < product_id < 2**63 and 0 < city_id < 2**63):
        raise Denied("not_found")
    try:
        product, _ = locked_catalog_product(product_id)
    except Denied:
        raise Denied("not_found") from None
    if product.shape != "L" or product.level != "city":
        raise Denied("not_found")
    city = City.objects.select_for_update().filter(pk=city_id, source_id=product.source_id).first()
    if city is None:
        raise Denied("not_found")
    identifiers = list(
        CityIdentifier.objects.select_for_update()
        .filter(source_id=product.source_id, city=city, kind="code")
        .order_by("pk")
    )
    auth = _principal(request)
    scopes = _scopes(product, city, identifiers)
    _require_scope(product, scopes, auth.now)
    return product, city, scopes, auth


def _target_projection(product, city):
    return {
        "product_id": product.pk,
        "city_id": city.pk,
        "city_name": city.name,
        "geography": city.geography,
    }


def list_targets(request, after_product, after_city, limit):
    with request_transaction() as budget:
        # Lock products before SessionCheck, as on the single-target route. The
        # shared product lock protects grants and registered directory changes.
        product_ids = list(
            Product.objects.filter(shape="L", level="city", pk__gte=after_product)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        source_ids = Product.objects.filter(pk__in=product_ids).values("source_id")
        sources = {
            source.pk: source
            for source in Source.objects.select_for_update()
            .filter(pk__in=source_ids)
            .order_by("pk")
        }
        locked_products = list(
            Product.objects.select_for_update(of=("self",))
            .filter(source_id__in=sources)
            .select_related("source")
            .order_by("pk")
        )
        for product in locked_products:
            product.source = sources[product.source_id]
        products = {product.pk: product for product in locked_products if product.pk in product_ids}
        auth = _principal(request)
        candidates = []
        for product_id in product_ids:
            budget.remaining_ms()
            product = products.get(product_id)
            if product is None:
                continue
            grants = list(
                product.usegrant_set.filter(
                    operation__in=("review", "retain_normalized"), revoked_at__isnull=True
                ).order_by("pk")
            )
            if {grant.operation for grant in grants if grant.effect == "allow"} != {
                "review",
                "retain_normalized",
            }:
                continue
            identifiers = (
                CityIdentifier.objects.filter(
                    source_id=product.source_id,
                    city__source_id=product.source_id,
                    kind="code",
                )
                .select_related("city")
                .order_by("city_id", "pk")
            )
            if product.pk == after_product:
                identifiers = identifiers.filter(city_id__gt=after_city)
            for _, group in groupby(identifiers.iterator(chunk_size=100), lambda row: row.city_id):
                budget.remaining_ms()
                codes = list(group)
                city = codes[0].city
                scopes = _scopes(product, city, codes)
                if _allowed(product, scopes, auth.now, grants):
                    candidates.append((product, city, scopes, grants))
                    if len(candidates) == limit + 1:
                        break
            if len(candidates) > limit:
                break

        now = _finish(auth)
        if not all(
            _allowed(product, scopes, now, grants) for product, _, scopes, grants in candidates
        ):
            raise Denied("page_changed")
        return [_target_projection(product, city) for product, city, _, _ in candidates]


def _version(alias, target):
    if alias is not None:
        return alias.version
    # Retention disposal removes the alias, not the independent command version.
    return (
        GovernanceAction.objects.filter(target_kind=KIND, target=target).aggregate(
            latest=Max("after_version")
        )["latest"]
        or 0
    )


def read_alias(request, product_id, city_id):
    with request_transaction():
        product, city, scopes, auth = _authorized_target(request, product_id, city_id)
        alias = CitySearchAlias.objects.filter(product=product, city=city).first()
        version = _version(alias, f"{product_id}:{city_id}")
        value = alias.search_alias if alias else ""
        _require_scope(product, scopes, _finish(auth))
        return {
            **_target_projection(product, city),
            "search_alias": value,
            "version": version,
        }


def write_alias(request, product_id, city_id, data):
    data = _command(data)
    with request_transaction():
        product, city, scopes, auth = _authorized_target(request, product_id, city_id)
        alias = (
            CitySearchAlias.objects.select_for_update().filter(product=product, city=city).first()
        )
        target = f"{product.pk}:{city.pk}"
        version = _version(alias, target)
        value = data["search_alias"] if data["action"] == "set" else ""
        parameter = salted_hmac(KIND, f"{data['action']}:{value}", algorithm="sha256").hexdigest()
        previous = replay(
            auth.user_id,
            data,
            KIND,
            target,
            parameter=parameter,
        )
        if previous:
            _require_scope(product, scopes, _finish(auth))
            return {
                "action_id": str(previous.pk),
                "status": previous.after_state,
                "version": previous.after_version,
            }
        if data["expected_version"] != version:
            raise Denied("precondition_conflict")
        if (data["action"] == "set" and alias and alias.search_alias == value) or (
            data["action"] == "clear" and (alias is None or not alias.search_alias)
        ):
            raise Denied("precondition_conflict")

        before = "unset" if alias is None or not alias.search_alias else "set"
        if alias is None:
            alias = CitySearchAlias(product=product, city=city, version=0)
        alias.search_alias = value
        alias.version = version + 1
        alias.save()
        record = receipt(
            auth.user_id,
            data,
            KIND,
            target,
            before,
            "set" if value else "cleared",
            version,
            alias.version,
            parameter=parameter,
        )
        _require_scope(product, scopes, _finish(auth))
        return {
            "action_id": str(record.pk),
            "status": record.after_state,
            "version": alias.version,
        }


def match_city_names(cities, product, term, catalog):
    """Match formal names or aliases only inside the supplied authorized catalog."""
    if not isinstance(term, str) or product.shape != "L" or product.level != "city":
        return cities.none()
    if catalog.product.pk != product.pk:
        return cities.none()
    authorized_ids = {segment.entity_id for segment in catalog.segments}
    if not authorized_ids:
        return cities.none()
    term = unicodedata.normalize("NFC", term)
    if not term:
        return cities.filter(pk__in=authorized_ids)
    alias_ids = CitySearchAlias.objects.filter(
        product=product,
        city_id__in=authorized_ids,
        search_alias__gt="",
        search_alias__icontains=term,
    ).values("city_id")
    return (
        cities.filter(pk__in=authorized_ids)
        .filter(Q(name__icontains=term) | Q(pk__in=alias_ids))
        .distinct()
    )


def reconcile_retention(product, grants, now):
    """Delete aliases with no catalog retention basis for this exact product."""
    if product.shape != "L" or product.level != "city":
        return
    aliases = CitySearchAlias.objects.filter(product=product).select_related("city")
    for alias in aliases.iterator():
        identifiers = CityIdentifier.objects.filter(
            source_id=product.source_id,
            city=alias.city,
            kind="code",
        )
        scopes = _scopes(product, alias.city, identifiers)
        if not scopes or not all(
            _can_use(product, "retain_normalized", scope, now, grants) for scope in scopes
        ):
            alias.delete()


def _can_use(product, operation, scope, now, grants):
    try:
        applicable(
            product,
            operation,
            scope,
            {"catalog"},
            now,
            grants,
        )
    except Denied:
        return False
    return True
