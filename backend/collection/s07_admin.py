"""Authorized workbench discovery and bounded operational trends from the collection ledger."""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from accounts.research_session import request_transaction
from django.db.models import Avg, Count, Exists, F, OuterRef, Q, Subquery
from observations.catalog import resolve
from observations.models import Product, Receipt, Source, UseGrant
from observations.normalization import INDICATORS
from observations.policy import Denied, Scope, actor, applicable
from observations.quality_governance import product_lock
from observations.queries import pk
from observations.s04_http import decode_cursor, encode_cursor, instant, page_size, params

from . import control
from .admission import LEDGER
from .models import Job, Run, RunItem, WorkWindow
from .queries import tally, window_permission

TERMINAL = frozenset({"finished", "failed", "paused", "expired", "coalesced"})
BASE_KEYS = {"product", "scope", "page_size", "cursor"}
WINDOW_KEYS = {"job", "object_code", "indicator", "start", "end"}
DAY = timedelta(days=1)
MAX_WINDOWS = 1_440


def link(segment, **query):
    suffix = "?" + urlencode(query) if query else ""
    return f"/api/v1/admin/{segment}/{suffix}"


def selector(request, allowed):
    values = params(request.query_params, allowed)
    if values.get("scope", "formal") not in {"formal", "synthetic"}:
        raise Denied("invalid_request")
    if "object_code" in values and (
        len(values["object_code"]) > 100 or "*" in values["object_code"]
    ):
        raise Denied("invalid_request")
    if "indicator" in values and not any(
        values["indicator"] in fields for fields in INDICATORS.values()
    ):
        raise Denied("invalid_request")
    return values


def days(values, *, required=False):
    if not required and "start" not in values and "end" not in values:
        return None
    try:
        start, end = (date.fromisoformat(values.get(k, "")) for k in ("start", "end"))
        if any(
            d.isoformat() != values[k] or d.year in (1, 9999)
            for d, k in zip((start, end), ("start", "end"), strict=True)
        ):
            raise ValueError
    except ValueError:
        raise Denied("invalid_request") from None
    if start >= end:
        raise Denied("invalid_request")
    if end - start > 30 * DAY:
        raise Denied("query_too_large")
    return start, end


def in_scope(product, values):
    return product.synthetic == (values.get("scope") == "synthetic")


def scope_args(product):
    return {"product": product.pk, "scope": "synthetic" if product.synthetic else "formal"}


def principal(user_id, *, manager=False, diagnose=False):
    user = actor(user_id, "manage_collection" if manager else "review_observation")
    if diagnose and not user.has_perm("accounts.diagnose_operations"):
        raise Denied("permission_denied")
    return user


def projection_page(route, request, values, query, project):
    size = page_size(values)
    binding = {k: v for k, v in values.items() if k != "cursor"} | {"page_size": size}
    cursor = decode_cursor(values.get("cursor"), route, request.user.pk, binding)
    if route == "collection-runs":
        if cursor:
            at = instant(cursor["created"])
            query = query.filter(Q(created_at__lt=at) | Q(created_at=at, pk__lt=cursor["id"]))
        query = query.order_by("-created_at", "-pk")
    else:
        if cursor:
            query = query.filter(pk__lt=cursor["id"])
        query = query.order_by("-pk")
    results = []
    for row in query.iterator(chunk_size=100):
        entry = project(row)
        if entry is not None:
            results.append((row, entry))
            if len(results) > size:
                break
    continuation = None
    if len(results) > size:
        last = results[size - 1][0]
        position = {"id": str(last.pk)}
        if route == "collection-runs":
            position["created"] = last.created_at.isoformat()
        continuation = encode_cursor(route, request.user.pk, binding, position)
    return {
        "results": [entry for _, entry in results[:size]],
        "page": {"page_size": size, "next_cursor": continuation},
    }


def review_window(window, job, grants, now, values):
    # The numeric job filter must denote a job represented by this business window.
    if window.disposed or job is None:
        raise Denied()
    code, indicator = values.get("object_code"), values.get("indicator")
    if indicator is not None and indicator not in INDICATORS[window.product.shape]:
        raise Denied()
    authorization = window_permission(window, job, grants, now, code, indicator)
    _, _, allowed_indicators = authorization
    # A filtered query must not disclose that a different object has collection activity.
    if code is not None:
        scope = None
        if window.expected is not None:
            if not any(
                row[0] == code and (indicator is None or row[2] == indicator)
                for row in window.expected.values()
            ):
                raise Denied()
        elif cnemc_mode := window.selection.get("mode"):
            if cnemc_mode == "include":
                selected = window.selection.get("objects", {}).get(code, ())
                if not selected or indicator is not None and indicator not in selected:
                    raise Denied()
            else:
                scope = True
        elif job.expected_codes is not None:
            if code not in job.expected_codes:
                raise Denied()
        else:
            scope = True
        if scope:
            entity, _ = resolve(window.product, code, window.target_end - timedelta(microseconds=1))
            if job.city_id and job.city_id != (
                entity.city_id if window.product.level == "station" else entity.pk
            ):
                raise Denied()
        if allowed_indicators is not None and not allowed_indicators:
            raise Denied()
    return authorization


def window_candidates(product, values, window_days):
    windows = WorkWindow.objects.filter(product=product, disposed=False)
    if window_days:
        zone = ZoneInfo(product.source_timezone)
        start, end = (datetime.combine(day, time.min, zone) for day in window_days)
        # The local day containing the end-exclusive window's last instant owns it.
        windows = windows.filter(target_end__gt=start, target_end__lte=end)
    if "job" in values:
        windows = windows.filter(
            Exists(Run.objects.filter(window_id=OuterRef("pk"), job_id=pk(values["job"])))
        )
    return windows.annotate(
        first_job=Subquery(
            Run.objects.filter(window_id=OuterRef("pk"))
            .order_by("created_at", "pk")
            .values("job_id")[:1]
        )
    )


def selects_product(values):
    product = product_lock(pk(values.get("product")))
    if not in_scope(product, values):
        raise Denied()
    return product


def grant_rows(product):
    return list(
        product.usegrant_set.filter(revoked_at=None, operation__in=("review", "retain_normalized"))
    )


def locked_job_products(values):
    products = Product.objects.filter(synthetic=values.get("scope") == "synthetic")
    if "product" in values:
        products = products.filter(pk=pk(values["product"]))
    products = products.filter(
        Exists(Job.objects.filter(product_id=OuterRef("pk"), disposed=False)),
        Exists(
            UseGrant.objects.filter(
                product_id=OuterRef("pk"), operation="review", effect="allow", revoked_at=None
            )
        ),
        Exists(
            UseGrant.objects.filter(
                product_id=OuterRef("pk"),
                operation="retain_normalized",
                effect="allow",
                revoked_at=None,
            )
        ),
    )
    ids = list(products.order_by("pk").values_list("pk", "source_id"))
    # This is the same source/product/principal lock sequence as collection control.
    list(Source.objects.select_for_update().filter(pk__in={s for _, s in ids}).order_by("pk"))
    return (
        Product.objects.select_for_update(of=("self",))
        .select_related("source")
        .filter(pk__in=[i for i, _ in ids])
        .order_by("pk")
    )


def list_jobs(request):
    values = selector(request, BASE_KEYS)
    with request_transaction():
        principal(request.user.pk, manager=True)
        products = {p.pk: p for p in locked_job_products(values)}
        auth = control.lock_principals(request)
        user = principal(request.user.pk, manager=True)
        rows = Job.objects.filter(product_id__in=products, disposed=False).select_related("city")
        checked = []

        def project(job):
            job.product = products[job.product_id]
            try:
                control.review_job(job, auth.now)
            except Denied:
                return None
            checked.append(job)
            links = {"detail": link(f"collection-jobs/{job.pk}")}
            if user.has_perm("observations.review_observation"):
                query = scope_args(job.product) | {"job": job.pk}
                links.update(
                    windows=link("collection-windows", **query),
                    runs=link("collection-runs", **query),
                )
            return {
                "id": job.pk,
                "product": job.product_id,
                "synthetic": job.product.synthetic,
                "profile": job.profile,
                "state": control.state("job", job),
                "links": links,
            }

        result = projection_page("collection-jobs", request, values, rows, project)
        now = auth.finish(())
        for job in checked:
            control.review_job(job, now)
        return result


def list_activity(request, kind):
    values = selector(request, BASE_KEYS | WINDOW_KEYS | ({"window"} if kind == "runs" else set()))
    route = f"collection-{kind}"
    span = days(values)
    window_id = pk(values["window"]) if "window" in values else None
    pk(values.get("product"))
    if "job" in values:
        pk(values["job"])
    with request_transaction():
        principal(request.user.pk)
        try:
            product = selects_product(values)
        except Denied as exc:
            if exc.code != "use_denied":
                raise
            auth = control.lock_principals(request)
            principal(request.user.pk)
            result = projection_page(route, request, values, WorkWindow.objects.none(), None)
            auth.finish(())
            return result
        auth = control.lock_principals(request)
        user = principal(request.user.pk)
        grants = grant_rows(product)
        windows = window_candidates(product, values, span)
        if window_id is not None:
            windows = windows.filter(pk=window_id)
        jobs = {row.pk: row for row in Job.objects.filter(product=product).select_related("city")}
        for job in jobs.values():
            job.product = product
        checked = {}

        def project(row):
            run = row if kind == "runs" else None
            window = run.window if run else row
            window.product = product
            job = jobs.get(run.job_id if run else window.first_job)
            try:
                authorization = review_window(window, job, grants, auth.now, values)
            except Denied as error:
                if error.code == "invalid_request":
                    raise
                return None
            full = authorization[0]
            if run and (not full or run.request_state == "disposed"):
                return None
            checked[(window.pk, job.pk)] = (window, job, authorization)
            query = scope_args(product)
            links = {"quality_issues": link("quality-issues", **query, window=window.pk)}
            if user.has_perm("observations.manage_grants"):
                links["disposition"] = link(f"disposition-products/{product.pk}")
            if not run:
                links["detail"] = link(
                    f"collection-windows/{window.pk}",
                    **{key: values[key] for key in ("object_code", "indicator") if key in values},
                )
                if full:
                    links["runs"] = link("collection-runs", **query, window=window.pk)
                return {
                    "id": window.pk,
                    "product": product.pk,
                    "synthetic": product.synthetic,
                    "target_start": window.target_start,
                    "target_end": window.target_end,
                    "full_scope": full,
                    "result": window.result if full else None,
                    "links": links,
                }
            links["detail"] = link(f"collection-runs/{run.pk}")
            links["window"] = link(f"collection-windows/{window.pk}")
            links["quality_issues"] = link("quality-issues", **query, run=run.pk)
            return {
                "id": run.pk,
                "window": window.pk,
                "product": product.pk,
                "synthetic": product.synthetic,
                "created_at": run.created_at,
                "status": run.status,
                "dispatch": run.dispatch,
                "result": run.result,
                "links": links,
            }

        if kind == "runs":
            rows = Run.objects.filter(window_id__in=windows.values("pk")).select_related("window")
            if "job" in values:
                rows = rows.filter(job_id=pk(values["job"]))
        else:
            rows = windows
        result = projection_page(route, request, values, rows, project)
        now = auth.finish(())
        for window, job, before in checked.values():
            if before != review_window(window, job, grants, now, values):
                raise Denied()
        return result


def timing(sample_count, known_count, average):
    return {
        "sample_count": sample_count,
        "known_count": known_count,
        "mean_seconds": average.total_seconds()
        if average is not None and sample_count == known_count
        else None,
    }


def ratio(num, den):
    return {"numerator": num, "denominator": den, "ratio": num / den if den else None}


def terminal_attempts(window_ids):
    rows = Run.objects.filter(window_id__in=window_ids, status__in=TERMINAL).exclude(
        request_state="disposed"
    )
    queue_valid = Q(started_at__gte=F("created_at"))
    execution_valid = Q(finished_at__gte=F("started_at"))
    scheduled = Q(scheduled_for__isnull=False)
    schedule_valid = scheduled & Q(started_at__isnull=False)
    stats = rows.aggregate(
        total=Count("pk"),
        responded=Count("pk", filter=Q(request_state="responded")),
        queue_known=Count("pk", filter=queue_valid),
        queue_mean=Avg(F("started_at") - F("created_at"), filter=queue_valid),
        execution_known=Count("pk", filter=execution_valid),
        execution_mean=Avg(F("finished_at") - F("started_at"), filter=execution_valid),
        scheduled=Count("pk", filter=scheduled),
        schedule_known=Count("pk", filter=schedule_valid),
        schedule_mean=Avg(F("started_at") - F("scheduled_for"), filter=schedule_valid),
    )
    total = stats["total"]
    return {
        "terminal_attempt_states": dict(
            rows.order_by()
            .values("status")
            .annotate(total=Count("pk"))
            .values_list("status", "total")
        ),
        "response_attempt_rate": ratio(stats["responded"], total),
        "queue_wait": timing(total, stats["queue_known"], stats["queue_mean"]),
        "execution": timing(total, stats["execution_known"], stats["execution_mean"]),
        "schedule_delay": timing(
            stats["scheduled"], stats["schedule_known"], stats["schedule_mean"]
        ),
    }


def bucket(day, windows):
    totals = Counter()
    states = Counter()
    full_windows = []
    for window_id, result, coverage, first in windows:
        if coverage["E"] is None:
            totals["unknown_E_windows"] += 1
        else:
            totals["E"] += coverage["E"]
        totals["O"] += coverage["O"]
        totals["V"] += coverage["V"]
        totals["windows_with_first_observation"] += first
        if result is not None:
            states[result] += 1
            full_windows.append(window_id)
    expected = None if totals["unknown_E_windows"] else totals["E"]
    return {
        "date": day,
        "window_count": len(windows),
        "partially_authorized_windows": len(windows) - len(full_windows),
        "window_results": dict(states),
        "coverage": {
            "E": expected,
            "O": totals["O"],
            "V": totals["V"],
            "unknown_E_windows": totals["unknown_E_windows"],
            "received": ratio(totals["O"], expected),
            "valid": ratio(totals["V"], expected),
        },
        "first_observation_window_rate": ratio(
            totals["windows_with_first_observation"], len(windows)
        ),
        # The collection ledger records fetch time. It does not record trusted publication time.
        "source_publication_delay": None,
        **terminal_attempts(full_windows),
    }


def permitted_days(product, values, start, end, grants, now):
    """Keep an empty response distinguishable from access to a hidden product."""
    zone = ZoneInfo(product.source_timezone)
    code, indicator = values.get("object_code"), values.get("indicator")
    for offset in range((end - start).days):
        day = start + offset * DAY
        for grant in grants:
            if grant.operation != "review" or grant.effect != "allow":
                continue
            object_code = code or grant.object_code
            field = indicator or grant.indicator
            if code:
                try:
                    _, geography = resolve(product, code, datetime.combine(day, time.min, zone))
                except Denied:
                    continue
            else:
                geography = grant.geography
            span = Scope(geography, object_code, field, day, day + DAY)
            try:
                for operation in ("review", "retain_normalized"):
                    applicable(product, operation, span, LEDGER, now, grants)
            except Denied:
                continue
            return True
    return False


def trends(request):
    values = selector(request, {"product", "scope"} | WINDOW_KEYS)
    start, end = days(values, required=True)
    with request_transaction():
        principal(request.user.pk, diagnose=True)
        product = selects_product(values)
        auth = control.lock_principals(request)
        principal(request.user.pk, diagnose=True)
        grants = grant_rows(product)
        zone = ZoneInfo(product.source_timezone)
        candidates = window_candidates(product, values, (start, end)).order_by("pk")
        jobs = {row.pk: row for row in Job.objects.filter(product=product).select_related("city")}
        for job in jobs.values():
            job.product = product
        buckets = defaultdict(list)
        checked = []
        supported_code, indicator = values.get("object_code"), values.get("indicator")
        for window in candidates.iterator(chunk_size=100):
            window.product = product
            job = jobs.get(window.first_job)
            try:
                authorization = review_window(window, job, grants, auth.now, values)
            except Denied as error:
                if error.code == "invalid_request":
                    raise
                continue
            checked.append((window, job, authorization))
            if len(checked) > MAX_WINDOWS:
                raise Denied("query_too_large")
            full, allowed, indicators = authorization
            coverage, _ = tally(
                window,
                supported_code,
                indicator,
                allowed=allowed,
                full=False,
                indicators=indicators,
            )
            items = RunItem.objects.filter(window=window, accepted=True)
            if supported_code:
                items = items.filter(object_code=supported_code)
            if indicator:
                items = items.filter(indicator=indicator)
            if allowed is not None:
                items = items.filter(item_key__in=allowed)
            first = Receipt.objects.filter(
                collection_item_id__in=items.values("pk"), outcome="first"
            ).exists()
            day = (window.target_end - timedelta(microseconds=1)).astimezone(zone).date()
            buckets[day].append((window.pk, window.result if full else None, coverage, first))
        rendered = [
            bucket(start + offset * DAY, buckets[start + offset * DAY])
            for offset in range((end - start).days)
        ]
        now = auth.finish(())
        for window, job, before in checked:
            if before != review_window(window, job, grants, now, values):
                raise Denied()
        if not checked and not permitted_days(product, values, start, end, grants, now):
            raise Denied()
        return {
            "product": product.pk,
            "synthetic": product.synthetic,
            "start": start,
            "end": end,
            "bucket_timezone": product.source_timezone,
            "window_anchor": "target_end_exclusive",
            "evaluated_at": now,
            "buckets": rendered,
        }
