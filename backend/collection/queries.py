"""Read coverage from this window's evidence and the current S02 qualification."""

from copy import copy
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F
from observations.catalog import resolve
from observations.models import CityIdentifier, Receipt, SiteEpoch, StationIdentifier
from observations.normalization import INDICATORS
from observations.policy import Denied, Scope, actor, applicable, clock, locked_product
from observations.queries import params_once
from observations.revisions import tables

from . import cnemc_jobs
from .admission import LEDGER, data_scope, selected_scopes, selected_use
from .models import WorkWindow


def tally(window, object_code=None, indicator=None, *, allowed=None, full=True, indicators=None):
    expected = window.expected

    def included(code, ind):
        return (not object_code or code == object_code) and (not indicator or ind == indicator)

    cells = {
        k: dict(
            object_code=code,
            observed_at=at,
            indicator=ind,
            received=False,
            accepted=False,
            valid=False,
            reason="insufficient_evidence",
        )
        for k, (code, at, ind) in (expected or {}).items()
        if included(code, ind) and (allowed is None or k in allowed)
    }
    items = window.items.all()
    if object_code:
        items = items.filter(object_code=object_code)
    if indicator:
        items = items.filter(indicator=indicator)
    if allowed is not None:
        items = items.filter(item_key__in=allowed)
    attempts = 0
    slots = set()
    for i in items.values(
        "item_key", "object_code", "observed_at", "indicator", "accepted", "status", "reason"
    ).iterator():
        attempts += 1
        k = i["item_key"]
        at = i["observed_at"].isoformat()
        if expected is None or k in expected:
            slots.add(k.rsplit("/", 1)[0])
        cell = cells.setdefault(
            k,
            dict(
                object_code=i["object_code"],
                observed_at=at,
                indicator=i["indicator"],
                received=False,
                accepted=False,
                valid=False,
                reason="insufficient_evidence",
            ),
        )
        if i["status"] == "rejected":
            cell.update(received=True, reason=i["reason"] or "invalid_field")
        elif i["status"] == "absent" and not cell["received"]:
            cell["reason"] = "missing_key"
    obs, _, _, _, rf = tables(window.product.level)
    fields = ("collection_item__item_key", rf + "__key_id")
    links = dict(
        Receipt.objects.filter(
            collection_item__in=items.filter(accepted=True), **{rf + "__isnull": False}
        ).values_list(*fields)
    )
    current = {
        pk: (disputed, retired, quality)
        for pk, disputed, retired, quality in obs.objects.filter(
            product_id=window.product_id, pk__in=set(links.values()), revisions__is_current=True
        ).values_list("pk", "disputed", "retired", "revisions__quality")
    }
    for k, pk in links.items():
        if pk not in current:
            continue
        disputed, retired, quality = current[pk]
        valid = not disputed and not retired and quality == "valid"
        cells[k].update(
            received=True,
            accepted=True,
            valid=valid,
            reason=(
                "disputed"
                if disputed
                else "retired"
                if retired
                else ""
                if valid
                else "upstream_not_reported"
                if quality == "missing"
                else quality
            ),
        )
    # A permitted observation outside the independent monitoring schedule still has
    # receipts and an exact key. It cannot enlarge E or fill a different expected slot.
    covered = cells if expected is None else {k: c for k, c in cells.items() if k in expected}
    if expected is not None and window.profile == cnemc_jobs.PROFILE:
        for k, cell in cells.items():
            cell["expected"] = k in expected
    total = len(covered) if expected is not None else None
    received = sum(c["received"] for c in covered.values())
    valid = sum(c["valid"] for c in covered.values())
    causes = (
        set(window.runs.exclude(error_code="").values_list("error_code", flat=True))
        if full
        else set()
    )
    for c in cells.values():
        if c["reason"] == "insufficient_evidence" and causes.intersection(
            {"database_error", "timeout", "upstream_transient", "lease_expired"}
        ):
            c["reason"] = "not_acquired"

    def last(field):
        return max((c["observed_at"] for c in cells.values() if c[field]), default=None)

    latest = last("received")
    latest_valid = last("valid")
    now = clock()
    by_indicator = {}
    for ind in INDICATORS[window.product.shape]:
        if indicator and indicator != ind or indicators is not None and ind not in indicators:
            continue
        subset = [c for c in covered.values() if c["indicator"] == ind]
        by_indicator[ind] = dict(
            E=len(subset) if expected is not None else None,
            O=sum(c["received"] for c in subset),
            V=sum(c["valid"] for c in subset),
        )
    return dict(
        E=total,
        O=received,
        V=valid,
        accepted=sum(c["accepted"] for c in covered.values()),
        object_slots={
            "E": len({k.rsplit("/", 1)[0] for k in covered}) if expected is not None else None,
            "O": len(slots),
        },
        not_received=total - received if total is not None else None,
        not_valid=received - valid,
        received_ratio=received / total if total else None,
        valid_ratio=valid / total if total else None,
        by_indicator=by_indicator,
        attempted_cells=attempts,
        latest_observed_at=latest,
        latest_valid_at=latest_valid,
        observation_age_seconds=(now - datetime.fromisoformat(latest)).total_seconds()
        if latest
        else None,
    ), cells


def catalogue_scopes(product, entries):
    """Build exact scopes for the window cells using a bounded number of directory reads."""
    by_hour = {}
    for key, (code, at, indicator) in entries.items():
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        by_hour.setdefault(at, []).append((key, code, indicator))
    zone = ZoneInfo(product.source_timezone)
    result = {}
    for at, cells in by_hour.items():
        filters = dict(
            source_id=product.source_id,
            code__in={code for _, code, _ in cells},
            kind="code",
            active_period__contains=at,
        )
        if product.level == "city":
            locations = {
                code: (city, geography)
                for code, city, geography in CityIdentifier.objects.filter(
                    city__source_id=product.source_id, **filters
                ).values_list("code", "city_id", "city__geography")
            }
        else:
            identifiers = dict(
                StationIdentifier.objects.filter(
                    station__source_id=product.source_id, **filters
                ).values_list("code", "station_id")
            )
            cities = {
                station: (epoch, geography)
                for station, epoch, geography in SiteEpoch.objects.filter(
                    station_id__in=identifiers.values(),
                    city__source_id=product.source_id,
                    active_period__contains=at,
                ).values_list("station_id", "pk", "city__geography")
            }
            locations = {code: cities[sid] for code, sid in identifiers.items() if sid in cities}
        local = at.astimezone(zone).date()
        for key, code, indicator in cells:
            if code in locations and key.split("/", 1)[0] == str(locations[code][0]):
                result[key] = Scope(
                    locations[code][1], code, indicator, local, local + timedelta(days=1)
                )
    return result


def project_window(window, job, grant_rows, now, requested_code, indicator):
    """Authorize the frozen projection and return only individually readable cells."""
    selection = window.selection
    objects = selection.get("objects", {})
    mode = selection.get("mode")
    entries = dict(window.expected or {})
    if mode == "include":
        entries = {
            k: value for k, value in entries.items() if value[2] in objects.get(value[0], ())
        }
    elif mode != "all":
        raise Denied()
    for k, code, at, ind in window.items.values_list(
        "item_key", "object_code", "observed_at", "indicator"
    ):
        if mode == "all" or ind in objects.get(code, ()):
            entries[k] = (code, at, ind)
    scopes = catalogue_scopes(window.product, entries)

    def readable(scope):
        for op in ("review", "retain_normalized"):
            applicable(window.product, op, scope, LEDGER, now, grant_rows)

    allowed = set()
    for key, scope in scopes.items():
        try:
            readable(scope)
        except Denied:
            continue
        allowed.add(key)

    indicators = set(INDICATORS[window.product.shape])
    full = False
    if not window.disposed:
        try:
            if mode == "all":
                readable(data_scope(job, window.target_start, window.target_end))
            else:
                frozen = copy(job)
                frozen.selection = selection
                for scope in cnemc_jobs.scopes(frozen, window.target_start, window.target_end):
                    readable(scope)
            full = len(allowed) == len(entries)
        except Denied:
            pass

    if mode == "include":
        indicators = {
            ind
            for code, inds in objects.items()
            if not requested_code or code == requested_code
            for ind in inds
        }
    if not full:
        indicators &= {
            scope.indicator
            for key, scope in scopes.items()
            if key in allowed and (not requested_code or scope.object_code == requested_code)
        }
        if not indicators or indicator and indicator not in indicators:
            raise Denied()
    elif requested_code and mode == "include" and not indicators:
        raise Denied()
    return full, allowed, indicators


@transaction.atomic
def read_window(actor_id, window_id, params=None):
    actor(actor_id, "review_observation")
    params = params_once(params or {})
    if set(params) - {"after", "object_code", "indicator", "run", "run_after"}:
        raise Denied("invalid_request")
    try:
        offset = int(params.get("after", "0"))
        run_offset = int(params.get("run_after", "0"))
        if not 0 <= offset <= 1000000 or not 0 <= run_offset <= 1000000:
            raise ValueError
        run_id = UUID(params["run"]) if "run" in params else None
    except ValueError:
        raise Denied("invalid_request") from None
    pid = WorkWindow.objects.filter(pk=window_id).values_list("product_id", flat=True).first()
    product = locked_product(pid)
    window = WorkWindow.objects.get(pk=window_id)
    window.product = product
    first = window.runs.select_related("job__city", "job__product").order_by("created_at").first()
    if first is None:
        raise Denied()
    grant_rows = list(product.usegrant_set.filter(revoked_at=None))

    indicator = params.get("indicator")
    requested_code = params.get("object_code")
    if requested_code is not None and not 0 < len(requested_code) <= 100:
        raise Denied("invalid_request")

    def permit():
        actor(actor_id, "review_observation")
        now = clock()
        if cnemc_jobs.managed(first.job):
            return project_window(window, first.job, grant_rows, now, requested_code, indicator)
        try:
            for op in ("review", "retain_normalized"):
                selected_use(
                    first.job, window.target_start, window.target_end, op, LEDGER, now, grant_rows
                )
            return True, None, None
        except Denied:
            if not requested_code or (
                first.job.expected_codes is not None
                and requested_code not in first.job.expected_codes
            ):
                raise
        selection = copy(first.job)
        selection.expected_codes = [requested_code]
        scopes = [
            scope
            for scope in selected_scopes(selection, window.target_start, window.target_end)
            if scope.object_code == requested_code
            and (not indicator or scope.indicator == indicator)
        ]
        if not scopes:
            raise Denied()
        for scope in scopes:
            for op in ("review", "retain_normalized"):
                applicable(product, op, scope, LEDGER, now, grant_rows)
        return False, None, None

    projection = permit()
    full, allowed, indicators = projection
    if window.disposed:
        survivors = window.items.all()
        if requested_code:
            survivors = survivors.filter(object_code=requested_code)
        if indicator:
            survivors = survivors.filter(indicator=indicator)
        if not survivors.exists():
            return dict(window_id=window.pk, state="disposed", coverage=None)
    if indicator and indicator not in INDICATORS[product.shape]:
        return dict(window_id=window.pk, state="not_applicable", coverage=None)
    coverage, cells = tally(
        window, requested_code, indicator, allowed=allowed, full=full, indicators=indicators
    )
    # A mixed batch may survive partial erasure. Never project its original counts or
    # fetch history to a caller who may only see a subset of its item lineage.
    runs = (
        window.runs.order_by("created_at", "pk")
        if full and not window.disposed
        else window.runs.none()
    )
    runs = runs.filter(pk=run_id) if run_id else runs
    entries = list(
        runs.values(
            "id",
            "reason",
            "predecessor_id",
            "status",
            "result",
            "error_code",
            "dispatch",
            "deliveries",
            "request_state",
            "created_at",
            "reserved_at",
            "started_at",
            "finished_at",
            "fetched_at",
            "generation",
            "batch_id",
            "manifest_count",
            "next_retry_at",
            *(["endpoint"] if cnemc_jobs.managed(first.job) else []),
        )[run_offset : run_offset + 51]
    )
    keys = sorted(cells)[offset : offset + 51]
    subset = window.items.filter(item_key__in=keys[:50]).order_by("id")
    if run_id:
        subset = subset.filter(run_id=run_id)
    details = list(
        subset.values(
            "id", "run_id", "item_key", "status", "accepted", "reason", batch_id=F("run__batch_id")
        )[:101]
    )
    page_items = [item["id"] for item in details[:100]]
    optional_checks = set()
    receipts = []
    read_at = clock()
    for receipt in (
        Receipt.objects.filter(collection_item_id__in=page_items)
        .select_related("collection_item")
        .order_by("pk")[:101]
    ):
        entry = dict(
            id=receipt.pk,
            item_id=receipt.collection_item_id,
            batch_id=receipt.batch_id,
            outcome=receipt.outcome,
        )
        if receipt.collection_metadata:
            item = receipt.collection_item
            day = item.observed_at.astimezone(ZoneInfo(product.source_timezone)).date()
            try:
                _, geo = resolve(product, item.object_code, item.observed_at)
            except Denied:
                geo = None
            if geo is not None:
                scope = Scope(geo, item.object_code, item.indicator, day, day + timedelta(days=1))
                for field, value in receipt.collection_metadata.items():
                    try:
                        for op in ("review", "retain_normalized"):
                            applicable(product, op, scope, {field}, read_at, grant_rows)
                    except Denied:
                        continue
                    entry.setdefault("metadata", {})[field] = value
                    optional_checks.add((scope, field))
        receipts.append(entry)
    if permit() != projection:
        raise Denied()
    now = clock()
    for scope, field in optional_checks:
        for op in ("review", "retain_normalized"):
            applicable(product, op, scope, {field}, now, grant_rows)
    return dict(
        window_id=window.pk,
        product=product.pk,
        synthetic=product.synthetic,
        target_start=window.target_start,
        target_end=window.target_end,
        scope=window.scope_key,
        profile=window.profile,
        roster_basis=window.roster_basis if full else "",
        state="disposed"
        if window.disposed
        else "closed"
        if window.closed_at
        else "active"
        if window.owner
        else "open",
        closed_at=window.closed_at,
        result=window.result if full or window.disposed else None,
        coverage=coverage,
        cells=[dict(item_key=k, **cells[k]) for k in keys[:50]],
        next_after=offset + 50 if len(keys) > 50 else None,
        runs=entries[:50],
        next_run_after=run_offset + 50 if len(entries) > 50 else None,
        items=details[:100],
        items_truncated=len(details) > 100,
        receipts=receipts[:100],
        receipts_truncated=len(receipts) > 100,
    )
