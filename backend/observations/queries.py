"""Exact, authorization-scoped projections. The product lock includes serialization."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from samples.normalization import Rejected, parse_time

from .models import (
    Batch,
    Change,
    City,
    CityIdentifier,
    QualityIssue,
    Receipt,
    SiteEpoch,
    StationIdentifier,
)
from .normalization import DAY, INDICATORS, REPORT_KIND, WINDOWS
from .policy import (
    RAW,
    REQUIRED,
    Denied,
    Scope,
    actor,
    applicable,
    batch_issue_fields,
    clock,
    locked_product,
)
from .projection import metadata, originals
from .revisions import key_scope, tables
from .selection import grant_bins, read_basis


def params_once(params):
    if hasattr(params, "getlist") and any(len(params.getlist(k)) != 1 for k in params):
        raise Denied("invalid_request")
    if any(
        not isinstance(value, str) or not value or len(value) > 100 or "*" in value
        for value in params.values()
    ):
        raise Denied("invalid_request")
    return {k: params[k] for k in params}


def pk(value):
    if not value or not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**63:
        raise Denied("invalid_request")
    return int(value)


def product_space(params):
    product = locked_product(pk(params.pop("product", None)))
    scope = params.pop("scope", None)
    if scope not in {None, "synthetic"}:
        raise Denied("invalid_request")
    if product.synthetic != (scope == "synthetic"):
        raise Denied()
    return product


def input_version(key, current, product, basis):
    return dict(
        key=key.pk,
        revision=current.pk,
        qualification=key.qualification_version,
        rights=product.rights_version,
        grants=basis.ids,
    )


def project_revision(r):
    return dict(
        revision_id=r.pk,
        revision_no=r.revision_no,
        value=str(r.value) if r.value is not None else None,
        unit=r.unit,
        quality=r.quality,
        censor_limit=str(r.limit_value) if r.limit_value is not None else None,
        censor_direction=r.limit_operator,
        source_description=r.source_description,
        **r.provenance,
    )


def dimensions(product, entity_id, stamp, start=None, end=None):
    period_filters = {"active_period__contains": stamp}
    if start is not None and end is not None:
        from psycopg.types.range import Range

        period_filters["active_period__contains"] = Range(start, end, "[)")
    if product.level == "station":
        epoch = (
            SiteEpoch.objects.select_related("city")
            .filter(
                pk=entity_id,
                station__source=product.source,
                city__source=product.source,
                **period_filters,
            )
            .first()
        )
        if not epoch:
            raise Denied()
        codes = StationIdentifier.objects.filter(
            source=product.source,
            station_id=epoch.station_id,
            kind="code",
            **period_filters,
        )
        return epoch.city.geography, tuple(codes.order_by("pk").values_list("code", flat=True))
    city = City.objects.filter(pk=entity_id, source=product.source).first()
    if city is None:
        raise Denied()
    codes = CityIdentifier.objects.filter(
        source=product.source, city=city, kind="code", **period_filters
    )
    return city.geography, tuple(codes.order_by("pk").values_list("code", flat=True))


@transaction.atomic
def read_current(actor_id, params, purpose="display"):
    params = params_once(params)
    product = product_space(params)
    if purpose not in {"display", "analyze"}:
        raise Denied()
    actor(actor_id, "review_observation" if purpose == "analyze" else None)
    obs, _, entity, _, _ = tables(product.level)
    anchor = "local_report_date" if params.get("report_kind") == "daily" else "observed_at"
    if set(params) != {
        entity,
        "indicator",
        "report_kind",
        "window_definition",
        "standard_series",
        anchor,
    }:
        raise Denied("invalid_request")
    ident = pk(params[entity])
    ind = params["indicator"]
    window = params["window_definition"]
    if (
        ind not in INDICATORS[product.shape]
        or window not in WINDOWS
        or params["report_kind"] != REPORT_KIND.get(window, "hourly")
    ):
        raise Denied("invalid_request")
    try:
        if anchor == "observed_at":
            stamp = parse_time(params[anchor])
            condition = {"observed_at_utc": stamp}
            day = stamp.astimezone(ZoneInfo(product.source_timezone)).date()
        else:
            if not DAY.fullmatch(params[anchor]):
                raise Denied("invalid_request")
            day = date.fromisoformat(params[anchor])
            stamp = datetime.combine(day, time.min, ZoneInfo(product.source_timezone)).astimezone(
                UTC
            )
            condition = {anchor: day}
        next_day = day + timedelta(days=1)
        hours, position = WINDOWS[window]
        width = timedelta(hours=hours)
        directory_at = stamp - timedelta(microseconds=1) if position == "right" else stamp
        start, end = None, None
        if position == "right":
            start, end = stamp - width, stamp
        elif position == "left":
            start, end = stamp, stamp + width
        elif position == "date":
            start, end = (
                stamp,
                datetime.combine(next_day, time.min, ZoneInfo(product.source_timezone)).astimezone(
                    UTC
                ),
            )
    except (ValueError, OverflowError, Rejected):
        raise Denied("invalid_request") from None
    geo, codes = dimensions(product, ident, directory_at, start, end)
    grants = grant_bins({product.pk})[product.pk]
    extra_checks = []

    def permissions(code, now):
        scope = Scope(geo, code, ind, day, next_day)
        return read_basis(product, scope, (), now, grants, purpose=purpose)

    permitted = {}
    now = clock()
    for code in codes:
        try:
            permitted[code] = permissions(code, now)
        except Denied:
            continue
    if not permitted:
        raise Denied()
    key = obs.objects.filter(
        product=product,
        **{entity + "_id": ident},
        object_code__in=permitted,
        source_local_date=day,
        indicator=ind,
        report_kind=params["report_kind"],
        window_definition=window,
        standard_series=params["standard_series"],
        **condition,
    ).first()
    basis = permitted[key.object_code] if key else next(iter(permitted.values()))
    results = []
    reason = "no_observation_at_time"
    if key and not key.retired:
        current = key.revisions.filter(is_current=True, candidate=False).first()
        if key.disputed:
            reason = "unresolved_dispute"
        elif current and current.quality == "valid" and current.value is not None:
            scope = Scope(geo, key.object_code, ind, day, next_day)

            def allowed(field):
                try:
                    read_basis(product, scope, (field,), now, grants, purpose=purpose)
                except Denied:
                    return False
                extra_checks.append(field)
                return True

            receipt = originals(product, [current]).get(current.pk)
            fields = metadata(current, receipt, allowed)
            projected = project_revision(current)
            projected = {
                k: projected[k]
                for k in (
                    "revision_id",
                    "revision_no",
                    "value",
                    "unit",
                    "quality",
                    "censor_limit",
                    "censor_direction",
                )
            }
            projected.update(fields)
            projected["source_time"] = fields["source_time"] or ""
            projected["source_description"] = fields["source_description"] or ""
            results.append(
                dict(
                    **projected,
                    source=product.source.namespace,
                    product=product.pk,
                    synthetic=product.synthetic,
                    indicator=ind,
                    **{entity: ident},
                    observed_at=key.observed_at_utc,
                    local_report_date=key.local_report_date,
                    standard_series=key.standard_series,
                    window_definition=window,
                    attribution=product.attribution,
                    input_version=input_version(key, current, product, basis),
                )
            )
            reason = None
        elif current:
            reason = current.quality
    end_clock = clock()
    for field in extra_checks:
        read_basis(product, scope, (field,), end_clock, grants, purpose=purpose)
    for code in permitted:
        permitted[code] = permissions(code, end_clock)
    basis = permitted[key.object_code] if key else next(iter(permitted.values()))
    if results:
        results[0]["input_version"] = input_version(key, current, product, basis)
    actor(actor_id, "review_observation" if purpose == "analyze" else None)
    return dict(
        count=len(results),
        results=results,
        reason=reason,
        attribution=product.attribution,
        rights_attributions=basis.attributions,
        synthetic=product.synthetic,
    )


def review_scope(product, key, grants, *, raw=False):
    scope = key_scope(key)
    now = clock()
    for operation in ("retain_normalized", "review"):
        applicable(product, operation, scope, RAW if raw else REQUIRED, now, grants)


def details(r, raw):
    row = dict(
        **project_revision(r),
        current=r.is_current,
        candidate=r.candidate,
        kind=r.kind,
        source_token=r.source_token,
        rule=r.rule_version,
        reason=r.reason,
        actor_id=r.actor_id,
    )
    if raw:
        row.update(raw_value=r.raw_value, raw_unit=r.raw_unit)
    return row


@transaction.atomic
def read_evidence(actor_id, params):
    actor(actor_id, "review_observation")
    params = params_once(params)
    product = product_space(params)
    after = pk(params.pop("after")) if "after" in params else 0
    obs, _, _, fk, rf = tables(product.level)
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    checks = {}
    issue_scopes = set()
    issue_fields = batch_issue_fields(product)

    def permitted(key):
        review_scope(product, key, grants)
        try:
            review_scope(product, key, grants, raw=True)
            raw = True
        except Denied:
            raw = False
        checks[key.pk] = (key, raw)
        return raw

    if set(params) == {fk}:
        key = obs.objects.filter(product=product, pk=pk(params[fk])).first()
        if key is None:
            raise Denied()
        raw = permitted(key)
        revs = list(key.revisions.filter(pk__gt=after).order_by("pk")[:101])
        rows = revs[:100]
        issues = list(
            QualityIssue.objects.filter(**{fk: key})
            .order_by("pk")
            .values_list("code", flat=True)[:100]
        )
        receipts = Receipt.objects.filter(**{rf + "__in": rows}).order_by("pk")
        fields = ["batch_id", "outcome", "source_token", rf + "_id"] + (["locator"] if raw else [])
        result = dict(
            revisions=[details(r, raw) for r in rows],
            watermark=key.watermark,
            trusted_scheme=key.trusted_scheme,
            disputed=key.disputed,
            issues=issues,
            receipts=list(receipts.values(*fields)[:100]),
            next_after=rows[-1].pk if len(revs) > 100 else None,
        )
    elif set(params) == {"batch"}:
        batch = Batch.objects.filter(product=product, pk=pk(params["batch"])).first()
        if batch is None:
            raise Denied()
        rows = []
        cursor = None
        authorized = False
        receipts = (
            batch.receipts.filter(**{rf + "__isnull": False})
            .select_related(rf + "__key")
            .order_by("pk")
        )
        for receipt in receipts.iterator():
            revision = getattr(receipt, rf)
            try:
                raw = permitted(revision.key)
            except Denied:
                continue
            authorized = True
            if receipt.pk <= after:
                continue
            if len(rows) == 100:
                cursor = rows[-1]["receipt_id"]
                break
            rows.append(
                dict(
                    receipt_id=receipt.pk,
                    key_id=revision.key_id,
                    outcome=receipt.outcome,
                    revision=details(revision, raw),
                    locator=receipt.locator if raw else None,
                )
            )
        issues = []
        for issue in QualityIssue.objects.filter(
            batch=batch, station_key=None, city_key=None
        ).order_by("pk"):
            issue_scope = Scope.from_issue(issue.scope)
            try:
                for operation in ("review", "retain_normalized"):
                    applicable(product, operation, issue_scope, issue_fields, clock(), grants)
            except Denied:
                continue
            authorized = True
            issue_scopes.add(issue_scope)
            issues.append(issue.code)
            if len(issues) == 100:
                break
        if not authorized:
            raise Denied()
        result = dict(
            receipts=rows,
            next_after=cursor,
            issues=issues,
            fetched_at=batch.fetched_at,
            profile=batch.profile,
        )
    else:
        raise Denied("invalid_request")
    for issue_scope in issue_scopes:
        for op in ("review", "retain_normalized"):
            applicable(product, op, issue_scope, issue_fields, clock(), grants)
    for key, raw in checks.values():
        review_scope(product, key, grants, raw=raw)
    actor(actor_id, "review_observation")
    return dict(**result, attribution=product.attribution, synthetic=product.synthetic)


@transaction.atomic
def changes_since(cursor, product_id, scope, actor_id):
    product = locked_product(product_id)
    actor(actor_id, "review_observation")
    applicable(product, "review", scope, REQUIRED, clock())
    events = []
    for event in Change.objects.filter(product=product, pk__gt=cursor).order_by("pk").iterator():
        key = event.station_key or event.city_key
        if key:
            cell = key_scope(key)
            if not (
                scope.geography in ("*", cell.geography)
                and scope.object_code in ("*", cell.object_code)
                and scope.indicator in ("*", cell.indicator)
                and scope.start <= cell.start
                and cell.end <= scope.end
            ):
                continue
            applicable(product, "retain_normalized", cell, REQUIRED, clock())
        events.append(
            dict(
                id=event.pk,
                kind=event.kind,
                key_id=key.pk if key else None,
                qualification=event.qualification_version,
                rights=event.rights_version,
            )
        )
        if len(events) == 100:
            break
    return events
