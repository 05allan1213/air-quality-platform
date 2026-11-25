"""Authorized, read-only product discovery for the S08 management workbench."""

from itertools import islice

from accounts.research_session import request_transaction
from django.db.models import Exists, OuterRef, Q
from django.views.decorators.cache import never_cache
from observations.models import Product, QualityIssue, Source, UseGrant
from observations.policy import Denied, actor
from observations.quality_governance import checks
from observations.queries import pk
from observations.s04_http import decode_cursor, encode_cursor
from rest_framework.decorators import api_view

from . import cnemc_jobs, control, s07_admin, schedule_segments
from .admission import (
    LEDGER,
    admission,
    bounds,
    budget,
    can_fetch,
    expected,
    parse_hour,
    target_code,
)
from .models import Job, WorkWindow
from .recovery import missing
from .services import MAX_ATTEMPTS, allows_closed
from .views import workbench

PURPOSES = {"execute", "ledger", "quality", "operations"}
REASONS = {"manual", "review", "backfill"}
BASE = {"purpose", "scope", "product", "page_size", "cursor"}


def _principal(user, purpose, reason):
    if purpose != "execute":
        s07_admin.principal(user, diagnose=purpose == "operations")
    else:
        actor(user, "run_collection")
        if reason == "review":
            actor(user, "review_observation")


def _lock_products(values, purpose, product_id, after):
    products = Product.objects.filter(synthetic=values.get("scope") == "synthetic")
    if product_id is not None:
        products = products.filter(pk=product_id)
    if after:
        products = (
            products.filter(pk__gte=after["product"])
            if purpose == "execute"
            else products.filter(pk__gt=after["product"])
        )
    if purpose == "execute":
        products = products.filter(
            Exists(Job.objects.filter(product_id=OuterRef("pk"), enabled=True, disposed=False))
        )
    else:
        grants = UseGrant.objects.filter(product_id=OuterRef("pk"), effect="allow", revoked_at=None)
        if purpose == "operations":
            # Each ledger check requires a single allow covering all ledger fields.
            # This only excludes impossible candidates before taking their locks.
            grants = grants.filter(Q(fields__contains=["*"]) | Q(fields__contains=sorted(LEDGER)))
        for operation in ("retain_normalized", "review"):
            products = products.filter(Exists(grants.filter(operation=operation)))
        if purpose == "ledger":
            products = products.filter(
                Exists(WorkWindow.objects.filter(product_id=OuterRef("pk"), disposed=False))
            )
        elif purpose == "quality":
            products = products.filter(
                Exists(QualityIssue.objects.filter(product_id=OuterRef("pk")))
            )
    ids = list(products.values_list("pk", "source_id"))
    sources = {
        source.pk: source
        for source in Source.objects.select_for_update()
        .filter(pk__in={source_id for _, source_id in ids})
        .order_by("pk")
    }
    locked = list(
        Product.objects.select_for_update()
        .filter(pk__in=[pid for pid, _ in ids], source_id__in=sources)
        .order_by("pk")
    )
    for product in locked:
        product.source = sources[product.source_id]
    return locked


def _lock_execution_jobs(products, after):
    by_id = {product.pk: product for product in products}
    jobs = Job.objects.filter(product_id__in=by_id, enabled=True, disposed=False)
    if after:
        jobs = jobs.filter(
            Q(product_id__gt=after["product"]) | Q(product_id=after["product"], pk__gt=after["job"])
        )
    locked = list(jobs.select_for_update(of=("self",)).select_related("city").order_by("pk"))
    for job in locked:
        job.product = by_id[job.product_id]
    return sorted(locked, key=lambda job: (job.product_id, job.pk))


def _check_execution(job, user, target, reason, now):
    start, end = bounds(job, target)
    endpoint = cnemc_jobs.endpoint_for(job, reason)
    caps = admission(job, user, start, end, endpoint=endpoint)
    schedule_segments.check_owner(job, target, now)
    if (
        job.plan_state == "active"
        and not job.handoff_checked
        and job.predecessor_id
        and schedule_segments.inherited_at(job.predecessor, job.effective_from)
    ):
        raise Denied()
    if not can_fetch(job, end, now, endpoint):
        raise Denied()
    cnemc_jobs.check_total(job)
    budget(job, caps, now)
    target_code(job, target)
    if (
        endpoint
        and job.selection.get("mode") == "include"
        and job.expected_codes != job.selection["objects"]
    ):
        cnemc_jobs.scopes(job, start, end)
    cells = expected(job, start, end)
    window = WorkWindow.objects.filter(
        product=job.product,
        scope_key=schedule_segments.scope_key(job),
        target_start=start,
        target_end=end,
        profile=job.profile,
    ).first()
    if window is None:
        if reason == "backfill":
            if not cells or (endpoint != "A10" and job.profile != "f-exact-v1"):
                raise Denied()
            # Like the recovery scanner, require a registered clock phase to
            # claim an A10 gap when there is no existing collection window.
            if endpoint == "A10":
                phase = job.schedule_anchor_at or job.next_due_at
                if (
                    phase is None
                    or (schedule_segments.hour(phase) - target).total_seconds()
                    % job.cadence_seconds
                ):
                    raise Denied()
        return
    if (
        window.disposed
        or window.expected != cells
        or window.roster_basis != job.roster_basis
        or window.selection != job.selection
        or window.standard != job.standard
        or (window.closed_at and not allows_closed(window, reason))
        or (endpoint and window.runs.filter(reserved_at__isnull=False).count() >= 2 * MAX_ATTEMPTS)
        or (reason == "backfill" and not missing(window))
    ):
        raise Denied()


def _execute(jobs, user, target, reason, now):
    for job in jobs:
        try:
            _check_execution(job, user, target, reason, now)
        except Denied:
            continue
        yield (
            (job.product_id, job.pk),
            {"product": job.product_id, "job": job.pk, "reason": reason, "target": target},
            job,
        )


def _window_basis(product, grants, span, now):
    jobs = {}
    windows = s07_admin.window_candidates(product, {}, span).order_by("pk")
    for window in windows.iterator(chunk_size=100):
        window.product = product
        if window.first_job not in jobs:
            jobs[window.first_job] = (
                Job.objects.filter(pk=window.first_job, product=product)
                .select_related("city")
                .first()
            )
        job = jobs[window.first_job]
        if job:
            job.product = product
        try:
            s07_admin.review_window(window, job, grants, now, {})
        except Denied as exc:
            if exc.code == "invalid_request":
                raise
            continue
        return window, job
    return None


def _quality_basis(product, grants, span, now):
    start, end = span
    events = (
        QualityIssue.objects.filter(product=product)
        .filter(
            Q(city_key__source_local_date__gte=start, city_key__source_local_date__lt=end)
            | Q(station_key__source_local_date__gte=start, station_key__source_local_date__lt=end)
            | Q(city_key__isnull=True, station_key__isnull=True)
        )
        .select_related(
            "city_key__city",
            "station_key__site_epoch__city",
            "run__job",
            "run__window",
            "run_item",
            "batch",
            "receipt",
        )
        .order_by("pk")
    )
    for event in events.iterator(chunk_size=100):
        key = event.city_key or event.station_key
        try:
            scope = checks(event, product, key, grants, now)
        except Denied:
            continue
        if scope.start < end and start < scope.end:
            return event, key
    return None


def _product_options(products, purpose, span, now):
    start, end = span
    for product in products:
        grants = s07_admin.grant_rows(product)
        query = s07_admin.scope_args(product)
        dated = {**query, "start": start.isoformat(), "end": end.isoformat()}
        if purpose == "quality":
            basis = _quality_basis(product, grants, span, now)
            links = {"quality_issues": s07_admin.link("quality-issues", **query)}
        else:
            basis = _window_basis(product, grants, span, now)
            if purpose == "operations":
                if not basis and s07_admin.permitted_days(product, {}, start, end, grants, now):
                    basis = ()
                links = {"trends": s07_admin.link("operations/trends", **dated)}
            else:
                links = {
                    "windows": s07_admin.link("collection-windows", **dated),
                    "runs": s07_admin.link("collection-runs", **dated),
                }
        if basis is not None:
            yield (
                (product.pk,),
                {
                    "product": product.pk,
                    "source": product.source.namespace,
                    "shape": product.shape,
                    "links": links,
                },
                (product, grants, basis),
            )


def _check_product(purpose, span, authorization, now):
    product, grants, basis = authorization
    if purpose == "quality":
        event, key = basis
        checks(event, product, key, grants, now)
    elif basis:
        s07_admin.review_window(*basis, grants, now, {})
    elif not s07_admin.permitted_days(product, {}, *span, grants, now):
        raise Denied()


def _options(request):
    purpose = request.query_params.get("purpose")
    if purpose == "configure":
        from .s08_schedule import template_options

        return template_options(request)
    if purpose not in PURPOSES:
        raise Denied("invalid_request")
    execute = purpose == "execute"
    values = s07_admin.selector(
        request, BASE | ({"target", "reason"} if execute else {"start", "end"})
    )
    product_id = pk(values["product"]) if "product" in values else None
    size = pk(values.get("page_size", "25"))
    if size > 50:
        raise Denied("query_too_large")
    reason = values.get("reason")
    if execute:
        if reason not in REASONS or "target" not in values:
            raise Denied("invalid_request")
        target = parse_hour(values["target"])
        condition = [reason, target]
    else:
        span = s07_admin.days(values, required=True)
        condition = span
    user = request.user.pk
    route = f"s08.{purpose}"
    binding = [values.get("scope", "formal"), product_id, size, condition]
    after = decode_cursor(values.get("cursor"), route, user, binding)
    position_fields = ("product", "job") if execute else ("product",)
    with request_transaction():
        _principal(user, purpose, reason)
        products = _lock_products(values, purpose, product_id, after)
        jobs = _lock_execution_jobs(products, after) if execute else ()
        session = control.lock_principals(request, {job.executor_id for job in jobs})
        _principal(user, purpose, reason)
        options = (
            _execute(jobs, user, target, reason, session.now)
            if execute
            else _product_options(products, purpose, span, session.now)
        )
        visible = list(islice(options, size + 1))
        now = session.finish(())
        _principal(user, purpose, reason)
        for _, _, authorization in visible:
            if execute:
                try:
                    _check_execution(authorization, user, target, reason, now)
                except Denied:
                    raise Denied() from None
            else:
                _check_product(purpose, span, authorization, now)
    token = None
    if len(visible) > size:
        last = visible[size - 1][0]
        token = encode_cursor(route, user, binding, dict(zip(position_fields, last, strict=True)))
    return {"results": [row for _, row, _ in visible[:size]], "next_cursor": token}


@never_cache
@api_view(["GET"])
def directory(request):
    return workbench(request, _options)
