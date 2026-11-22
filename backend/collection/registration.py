"""Idempotent, offline CNEMC registration from an operator's bounded private manifest."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from observations.models import (
    City,
    CityIdentifier,
    Product,
    SiteEpoch,
    Source,
    Station,
    StationIdentifier,
)
from observations.policy import OPERATIONS, Denied, Scope, actor, applicable, clock, register_grant
from psycopg.types.range import Range

from . import cnemc_jobs
from .adapters import cnemc
from .admission import validate_limits
from .services import register_job


def schema(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() <= {*required, *optional}:
        raise Denied("invalid_manifest")


def text(value, maximum=100):
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= maximum
        or value.strip() != value
        or not value.isprintable()
    ):
        raise Denied("invalid_manifest")
    return value


def sequence(values, cap):
    if not isinstance(values, list) or len(values) > cap:
        raise Denied("invalid_manifest")
    return values


def moment(value):
    try:
        at = datetime.fromisoformat(text(value, 48))
        if at.utcoffset() is None:
            raise ValueError
        return at
    except ValueError:
        raise Denied("invalid_manifest") from None


def grant(actor_id, product, value, allocation):
    required = {
        "operation",
        "audience",
        "geography",
        "object_code",
        "indicator",
        "data_from",
        "data_until",
        "fields",
        "terms",
        "terms_version",
        "valid_from",
    }
    optional = {
        "valid_until",
        "retain_until",
        "expiry_mode",
        "restrictions",
        "effect",
        "attribution",
        "collection_limits",
        "endpoints",
        "max_total_requests",
    }
    schema(value, required, optional)
    terms = dict(value)
    for k in ("valid_from", "valid_until", "retain_until"):
        if k in terms and terms[k] is not None:
            terms[k] = moment(terms[k])
    for k in ("data_from", "data_until"):
        terms[k] = date.fromisoformat(text(terms[k], 10))
    for k in ("geography", "object_code", "indicator", "terms_version"):
        text(terms[k])
    text(terms["terms"], 512)
    if terms["operation"] not in OPERATIONS or OPERATIONS[terms["operation"]] != terms["audience"]:
        raise Denied("invalid_grant")
    if terms["operation"] == "acquire_online":
        endpoints = sequence(terms.get("endpoints", []), 6)
        allowed = {cnemc.PRODUCTS[product.code]}
        if product.code == "station-national-hour":
            allowed.add("A10")
        if not endpoints or not all(isinstance(ep, str) and ep in allowed for ep in endpoints):
            raise Denied("invalid_grant")
        terms["endpoints"] = sorted(set(endpoints))
        if terms.get("effect", "allow") == "allow":
            validate_limits(terms.get("collection_limits"))
        cap = terms.get("max_total_requests")
        if cap is not None and (type(cap) is not int or not 1 <= cap <= 2**31 - 1):
            raise Denied("invalid_grant")
        if cap is not None:
            terms["validation_allocation"] = (
                {"basis": terms["terms_version"], "total": cap}
                if allocation is None
                else allocation
            )
    elif any(k in terms for k in ("endpoints", "collection_limits", "max_total_requests")):
        raise Denied("invalid_grant")
    matches = list(product.usegrant_set.filter(**terms)[:2])
    if matches and matches[0].validation_disabled:
        raise Denied("validation_grant_disabled")
    if len(matches) > 1 or matches and matches[0].revoked_at:
        raise Denied("registration_conflict")
    return matches[0] if matches else register_grant(actor_id, product.pk, **terms)


def catalog_period(row):
    lo = moment(row["from"])
    hi = moment(row["until"]) if row.get("until") else None
    if hi and hi <= lo:
        raise Denied("invalid_catalog")
    return Range(lo, hi, "[)")


def check_catalog(product, code, geography, period):
    zone = ZoneInfo(cnemc.TIMEZONE)
    lo = period.lower.astimezone(zone).date()
    hi = (
        ((period.upper - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1))
        if period.upper
        else date.max
    )
    scope = Scope(geography, code, "*", lo, hi)
    applicable(product, "retain_normalized", scope, cnemc_jobs.CATALOG, clock())


def identifier(source, code, period, model):
    matches = list(
        model.objects.filter(source=source, kind="code", code=code, active_period__overlap=period)[
            :2
        ]
    )
    if matches and (len(matches) != 1 or matches[0].active_period != period):
        raise Denied("catalog_period_conflict")
    return matches[0] if matches else None


def catalog(source, products, cities, stations):
    for rows, station in ((cities, False), (stations, True)):
        seen = set()
        for row in rows:
            schema(
                row,
                {
                    "product",
                    "code",
                    "name",
                    "from",
                    "evidence",
                    "city_code" if station else "geography",
                },
                {"until"},
            )
            code, name = text(row["code"]), text(row["name"])
            evidence = text(row["evidence"], 240)
            period = catalog_period(row)
            if (code, period.lower) in seen:
                raise Denied("duplicate_catalog_entry")
            seen.add((code, period.lower))
            product = products[row["product"]]
            if not station:
                geo = text(row["geography"])
                if not product.synthetic and not re.fullmatch(r"[0-9]{6}", code):
                    raise Denied("invalid_city_code")
                check_catalog(product, code, geo, period)
                ident = identifier(source, code, period, CityIdentifier)
                if ident:
                    if (ident.city.name, ident.city.geography, ident.evidence) != (
                        name,
                        geo,
                        evidence,
                    ):
                        raise Denied("catalog_conflict")
                else:
                    city = City.objects.create(source=source, name=name, geography=geo)
                    CityIdentifier.objects.create(
                        source=source, city=city, code=code, active_period=period, evidence=evidence
                    )
            else:
                city = (
                    CityIdentifier.objects.select_related("city")
                    .filter(
                        source=source,
                        kind="code",
                        code=text(row["city_code"]),
                        active_period__contains=period,
                    )
                    .first()
                )
                if not city or product.level != "station":
                    raise Denied("identity_unresolved")
                check_catalog(product, code, city.city.geography, period)
                ident = identifier(source, code, period, StationIdentifier)
                if ident:
                    if (
                        ident.evidence != evidence
                        or not SiteEpoch.objects.filter(
                            station_id=ident.station_id,
                            city=city.city,
                            name=name,
                            active_period=period,
                            location_evidence=evidence,
                        ).exists()
                    ):
                        raise Denied("catalog_conflict")
                else:
                    st = Station.objects.create(source=source, name=name)
                    StationIdentifier.objects.create(
                        source=source,
                        station=st,
                        code=code,
                        active_period=period,
                        evidence=evidence,
                    )
                    SiteEpoch.objects.create(
                        station=st,
                        city=city.city,
                        name=name,
                        active_period=period,
                        location_evidence=evidence,
                        certainty="documented",
                    )


@transaction.atomic
def install(actor_id, manifest):
    actor(actor_id, "manage_collection")
    actor(actor_id, "manage_grants")
    schema(
        manifest,
        {"namespace", "collection_limits", "products"},
        {"cities", "stations", "jobs", "validation_allocation"},
    )
    namespace = manifest["namespace"]
    if namespace not in ("cnemc", "cnemc.fixture"):
        raise Denied("invalid_manifest")
    limits = validate_limits(manifest["collection_limits"])
    synthetic = namespace == "cnemc.fixture"
    source, _ = Source.objects.get_or_create(
        namespace=namespace,
        defaults={
            "publisher": "Synthetic CNEMC fixture" if synthetic else "CNEMC",
            "collection_limits": limits,
        },
    )
    source = Source.objects.select_for_update().get(pk=source.pk)
    if source.collection_limits != limits:
        raise Denied("registration_conflict")
    products, grants, jobs = {}, {}, []
    for item in sequence(manifest["products"], len(cnemc.PRODUCTS)):
        schema(item, {"code", "evidence", "attribution", "grants"})
        code = item["code"]
        if code not in cnemc.PRODUCTS or code in products:
            raise Denied("invalid_manifest")
        contract = cnemc.ENDPOINTS[cnemc.PRODUCTS[code]]
        config = dict(
            family="F" if synthetic else contract.shape,
            shape=contract.shape,
            level="station" if contract.shape == "S" else "city",
            synthetic=synthetic,
            source_timezone=cnemc.TIMEZONE,
            contract_version=cnemc.VERSION,
            evidence=text(item["evidence"], 240),
            attribution=text(item["attribution"], 240),
        )
        p, _ = Product.objects.get_or_create(source=source, code=code, defaults=config)
        if any(getattr(p, k) != v for k, v in config.items()):
            raise Denied("registration_conflict")
        products[code] = p
        grants[code] = [
            grant(actor_id, p, g, manifest.get("validation_allocation"))
            for g in sequence(item["grants"], 4096)
        ]
    bounded = [g for rows in grants.values() for g in rows if g.max_total_requests]
    if len(bounded) > 1 or "validation_allocation" in manifest:
        allocation = manifest.get("validation_allocation")
        schema(allocation, {"basis", "total"})
        if (
            not text(allocation["basis"], 240)
            or type(allocation["total"]) is not int
            or sum(g.max_total_requests for g in bounded) > allocation["total"]
        ):
            raise Denied("invalid_validation_allocation")
    catalog(
        source,
        products,
        sequence(manifest.get("cities", []), 2048),
        sequence(manifest.get("stations", []), 2048),
    )
    for spec in sequence(manifest.get("jobs", []), 2048):
        schema(
            spec,
            {"product", "executor", "selection"},
            {
                "city_code",
                "cadence",
                "first_due",
                "expected",
                "roster_basis",
                "standard",
                "backfill_policy",
                "validation_terms_version",
            },
        )
        p = products[spec["product"]]
        context = cnemc_jobs.due_time(spec["first_due"]) if spec.get("first_due") else clock()
        city_id = None
        if spec.get("city_code"):
            obj = CityIdentifier.objects.filter(
                source=source,
                kind="code",
                code=text(spec["city_code"]),
                active_period__contains=context,
            ).first()
            if obj is None:
                raise Denied("identity_unresolved")
            city_id = obj.city_id
        verification = None
        if "validation_terms_version" in spec:
            matches = [
                g
                for g in grants[p.code]
                if g.max_total_requests and g.terms_version == spec["validation_terms_version"]
            ]
            if len(matches) != 1:
                raise Denied("validation_grant_required")
            verification = matches[0].pk
        # Register through the same admission service; repeated manifests reuse the disabled configuration.
        new = register_job(
            actor_id,
            spec["executor"],
            p.pk,
            city_id,
            cnemc_jobs.PROFILE,
            spec.get("cadence", 3600),
            spec.get("first_due"),
            spec.get("expected"),
            spec.get("roster_basis", ""),
            selection=spec["selection"],
            standard=spec.get("standard"),
            backfill_policy=spec.get("backfill_policy"),
            validation_grant_id=verification,
            reuse=True,
        )
        jobs.append(new.pk)
    return {
        "source": source.pk,
        "products": {code: p.pk for code, p in products.items()},
        "jobs": jobs,
    }
