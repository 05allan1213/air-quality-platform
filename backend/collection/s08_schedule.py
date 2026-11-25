"""Host templates and transactional schedule controls for the management workspace."""

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from json import dumps
from zoneinfo import ZoneInfo

from accounts.assigned_permissions import CONFIGURE_SCHEDULE, has_assigned_permission
from accounts.governance import command, receipt
from accounts.limits import limited
from accounts.models import GovernanceAction
from accounts.research_session import lock_users_and_groups, request_transaction, scope
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.views.decorators.cache import never_cache
from observations.models import Source
from observations.policy import Denied, Scope, actor, applicable, clock, locked_product
from observations.queries import pk as parse_pk
from observations.s04_http import decode_cursor, encode_cursor, params
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import cnemc_jobs, control
from . import schedule_segments as slots
from .admission import LEDGER, bounds, budget, is_fixture_profile
from .models import ControlEvent, Job, ScheduleTemplate, ScheduleTemplateRevision
from .services import validate_job_registration, wake_scanner
from .views import reject

REASONS = dict(
    create_draft="schedule_create",
    propose="schedule_change",
    activate="schedule_activate",
    discard="schedule_discard",
)
EXTRA = {
    "create_draft": {"template", "cadence_seconds", "first_due_at"},
    "propose": {"template_version", "cadence_seconds", "first_due_at"},
    "activate": {"template_version", "expected_predecessor_version"},
    "discard": {"template_version", "expected_predecessor_version"},
}


def require_configure(user_id):
    if not has_assigned_permission(actor(user_id), CONFIGURE_SCHEDULE):
        raise Denied("permission_denied")


def finish_configure(session):
    now = session.finish(())
    require_configure(session.user_id)
    return now


def version_value(value):
    return type(value) is int and 0 <= value < 2**63


def parse(data, actions):
    args = command(data, actions, set(REASONS.values()), extra=EXTRA)
    action = args["action"]
    if args["reason_code"] != REASONS[action]:
        raise Denied("invalid_request")
    for k in ("template", "template_version", "expected_predecessor_version"):
        if k not in args or k == "expected_predecessor_version" and args[k] is None:
            continue
        if not version_value(args[k]) or k == "template" and args[k] == 0:
            raise Denied("invalid_request")
    if "cadence_seconds" in args:
        if not cadence_ok(args["cadence_seconds"]):
            raise Denied("invalid_request")
        stamp = cnemc_jobs.due_time(args["first_due_at"])
        if stamp.second or stamp.microsecond or not 2 <= stamp.year <= 9998:
            raise Denied("invalid_request")
    return args


def cadence_ok(value):
    return type(value) is int and 3600 <= value <= 86400 and value % 3600 == 0


def lock_related(ids):
    ids = set(ids)
    rows = list(Job.objects.filter(pk__in=ids).select_related("city").order_by("pk"))
    if len(rows) != len(ids) or len({j.product_id for j in rows}) != 1:
        raise Denied()
    source = Source.objects.select_for_update().get(pk=rows[0].product.source_id)
    product = locked_product(rows[0].product_id)
    product.source = source
    result = {
        job.pk: job
        for job in Job.objects.select_for_update(of=("self",))
        .filter(pk__in=ids)
        .select_related("city")
        .order_by("pk")
    }
    for job in result.values():
        job.product = product
    return result


def candidate(basis, cadence, due):
    return Job(
        product=basis.product,
        city=basis.city,
        executor_id=basis.executor_id,
        profile=basis.profile,
        expected_codes=deepcopy(basis.expected_codes),
        roster_basis=basis.roster_basis,
        selection=deepcopy(basis.selection),
        standard=deepcopy(basis.standard),
        backfill_policy=deepcopy(basis.backfill_policy),
        validation_grant_id=basis.validation_grant_id,
        cadence_seconds=cadence,
        next_due_at=due,
    )


def validate_execution(job, due, now, *, host_registration=False, check_budget=True):
    if job.validation_grant_id:
        raise Denied("invalid_schedule")
    caps = validate_job_registration(
        job,
        due,
        registration=host_registration,
        fixture_preview=not host_registration
        and settings.S08_FIXTURE_SCHEDULE
        and is_fixture_profile(job),
    )
    if check_budget:
        budget(job, caps, now)


def check_operating(job, now):
    if (
        job.disposed
        or job.pause_reason
        or any((job.product.collection_pause, job.product.source.collection_pause))
        or slots.handoff_pause_pending(job, now)
    ):
        raise Denied("precondition_conflict")


def review(job, now, anchor=None):
    # Reuse the ledger's current-time and exact target use check.
    control.review_job(job, now)
    if anchor is not None and slots.hour(now) != slots.hour(anchor):
        control.review_job(job, now, target=slots.hour(anchor))


def review_takeover(job, boundary, now):
    # A conflict can be any existing window in the open-ended ledger partition,
    # including a different code/selection and T's earlier hours. Prove the
    # encompassing scope independently of the existence or contents of rows.
    start, _ = bounds(job, boundary)
    use = Scope(
        job.city.geography if job.city_id else "*",
        "*",
        "*",
        start.astimezone(ZoneInfo(job.product.source_timezone)).date(),
        date.max,
    )
    for operation in ("retain_normalized", "review"):
        applicable(job.product, operation, use, LEDGER, now)


def date_value(stamp):
    return stamp.isoformat() if stamp is not None else None


def preview(job, predecessor, now, *, check_conflict=False):
    boundary = slots.hour(job.schedule_anchor_at)
    previous_due = None
    if predecessor:
        previous_due = slots.phase_at_or_before(predecessor, boundary - timedelta(microseconds=1))
        if slots.hour(previous_due) < predecessor.effective_from:
            previous_due = None
    conflict = None
    if check_conflict and job.plan_state == "draft":
        try:
            review_takeover(job, boundary, now)
        except Denied:
            pass
        else:
            if slots.future_ledger(job, boundary).exists():
                conflict = "registered_window"
    return {
        "boundary": date_value(boundary),
        "previous_last_due_at": date_value(previous_due),
        "first_due_at": date_value(job.schedule_anchor_at),
        "gap_seconds": int((job.schedule_anchor_at - previous_due).total_seconds())
        if previous_due
        else None,
        "conflict": conflict,
    }


def project(job, template, predecessor, now, *, check_conflict=False):
    return {
        "job": job.pk,
        "state": job.plan_state,
        "version": slots.effective_control_version(job, now),
        "template": template.pk,
        "template_version": job.schedule_template_version or template.version,
        "current_template_version": template.version,
        "cadence_seconds": job.cadence_seconds,
        "first_due_at": date_value(job.schedule_anchor_at),
        "predecessor": job.predecessor_id,
        "predecessor_version": slots.effective_control_version(predecessor, now)
        if predecessor
        else None,
        "pending_successor": job.pending_successor_id,
        "effective_from": date_value(job.effective_from),
        "effective_until": date_value(job.effective_until),
        "preview": preview(job, predecessor, now, check_conflict=check_conflict),
    }


def check_root(job):
    if slots.overlapping_clocks(job, exclude=(job.pk,)).exists():
        raise Denied("precondition_conflict")


def fresh_draft(args, template, basis, user_id, now, predecessor):
    due = cnemc_jobs.due_time(args["first_due_at"])
    cadence = args["cadence_seconds"]
    if cadence not in template.allowed_cadences or due.minute != template.delivery_minute:
        raise Denied("invalid_request")
    if slots.hour(due) <= now:
        raise Denied("precondition_conflict")
    review(basis, now, due)
    if predecessor is not None:
        if (
            predecessor.plan_state != "active"
            or predecessor.effective_until is not None
            or predecessor.pending_successor_id is not None
            or slots.hour(due) <= predecessor.effective_from
        ):
            raise Denied("precondition_conflict")
    elif (
        slots.same_scope(basis)
        .filter(plan_state="draft", predecessor__isnull=True, disposed=False)
        .exists()
    ):
        raise Denied("precondition_conflict")
    job = candidate(basis, cadence, due)
    validate_execution(job, due, now)
    if predecessor is None:
        check_root(job)
    job.next_due_at = None
    job.plan_state = "draft"
    job.schedule_template = template
    job.schedule_template_version = template.version
    job.schedule_anchor_at = due
    job.predecessor = predecessor
    job.changed_by_id = user_id
    job.save()
    if predecessor:
        predecessor.pending_successor = job
        predecessor.save(update_fields=["pending_successor"])
        predecessor.refresh_from_db(fields=["control_version"])
    return job


def decide(args, job, template, basis, predecessor, now):
    if (
        job.plan_state != "draft"
        or args["expected_predecessor_version"]
        != (slots.effective_control_version(predecessor, now) if predecessor else None)
        or predecessor is not None
        and predecessor.pending_successor_id != job.pk
    ):
        raise Denied("precondition_conflict")
    activating = args["action"] == "activate"
    if activating:
        due = job.schedule_anchor_at
        boundary = slots.hour(due)
        if boundary <= now:
            raise Denied("precondition_conflict")
        if (
            job.schedule_template_version != template.version
            or job.cadence_seconds not in template.allowed_cadences
            or due.minute != template.delivery_minute
        ):
            raise Denied("precondition_conflict")
        check_operating(job, now)
        if predecessor:
            check_operating(predecessor, now)
            if not predecessor.enabled or predecessor.effective_until is not None:
                raise Denied("precondition_conflict")
        else:
            slots.check_no_overlap(job, boundary)
        if predecessor:
            slots.check_no_overlap(job, boundary, replace=predecessor)
        validate_execution(candidate(basis, job.cadence_seconds, due), due, now)
        review_takeover(job, boundary, now)
        if slots.future_ledger(job, boundary).exists():
            raise Denied("precondition_conflict")
        cnemc_jobs.ensure_schedule(job, due=due, replace=predecessor)
        wake_scanner()
        job.plan_state, job.effective_from, job.next_due_at, job.enabled = (
            "active",
            boundary,
            due,
            True,
        )
        if predecessor:
            predecessor.effective_until = boundary
    else:
        job.plan_state = "discarded"
    if predecessor:
        predecessor.pending_successor = None
        predecessor.save(update_fields=["pending_successor", "effective_until"])
        predecessor.refresh_from_db(fields=["control_version"])
    job.save(update_fields=["plan_state", "effective_from", "next_due_at", "enabled"])
    job.refresh_from_db(fields=["control_version"])
    return job


def locate(template_id=None, job_id=None):
    if job_id is not None:
        template_id = (
            Job.objects.filter(pk=job_id).values_list("schedule_template_id", flat=True).first()
        )
    basis_id = (
        ScheduleTemplate.objects.filter(pk=template_id)
        .values_list("basis_job_id", flat=True)
        .first()
    )
    if basis_id is None:
        raise Denied()
    # Acquire the Source before reading chain pointers, which other commands may change.
    source_id, product_id = (
        Job.objects.filter(pk=basis_id).values_list("product__source_id", "product_id").get()
    )
    Source.objects.select_for_update().get(pk=source_id)
    locked_product(product_id)
    ids = {basis_id}
    if job_id is not None:
        ids.add(job_id)
        predecessor_id = (
            Job.objects.filter(pk=job_id).values_list("predecessor_id", flat=True).get()
        )
        if predecessor_id is not None:
            ids.add(predecessor_id)
    jobs = lock_related(ids)
    template = ScheduleTemplate.objects.select_for_update().get(
        pk=template_id, basis_job_id=basis_id
    )
    if not template_available(jobs[basis_id]):
        raise Denied()
    job = jobs.get(job_id)
    predecessor = jobs.get(job.predecessor_id) if job else None
    return template, jobs[basis_id], job, predecessor


def command_identity(args, template, target):
    where = f"template:{template.pk}" if target is None else f"job:{target.pk}"
    digest = sha256(
        dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return where, digest


def replay_result(request, args, job_id=None):
    """Read the original result; never apply a missing command when checking a receipt."""
    template, basis, target, _ = locate(args.get("template"), job_id)
    session = control.lock_principals(request)
    require_configure(request.user.pk)
    prior = GovernanceAction.objects.filter(
        actor_id=request.user.pk, request_id=args["request_id"]
    ).first()
    result = (
        original(prior, args, template, target, basis, finish_configure(session)) if prior else None
    )
    if result is None:
        raise Denied()
    return result


def review_command(action, basis, job, now):
    due = job.schedule_anchor_at
    review(basis, now, due)
    review(job, now, due)
    if action != "discard":
        validate_execution(candidate(basis, job.cadence_seconds, due), due, now, check_budget=False)
    if action == "activate":
        review_takeover(job, slots.hour(due), now)


def original(prior, args, template, target, basis, now):
    review(basis, now)
    if target:
        review(target, now, target.schedule_anchor_at)
    if (prior.origin, prior.parameter) != command_identity(args, template, target):
        raise Denied("request_conflict")
    record = ControlEvent.objects.select_related("job__city").filter(receipt=prior).first()
    if record is None or record.schedule_response is None:
        raise Denied()
    record.job.product = basis.product
    review_command(args["action"], basis, record.job, now)
    return record.schedule_response


def perform(request, args, job_id=None):
    template, basis, target, predecessor = locate(args.get("template"), job_id)
    session = control.lock_principals(request, {basis.executor_id})
    user_id = request.user.pk
    require_configure(user_id)
    now = session.now
    review(basis, now)
    if target:
        review(target, now, target.schedule_anchor_at)
    where, digest = command_identity(args, template, target)
    prior = GovernanceAction.objects.filter(actor_id=user_id, request_id=args["request_id"]).first()
    if prior:
        return original(prior, args, template, target, basis, finish_configure(session))
    action = args["action"]
    current = template.version if target is None else slots.effective_control_version(target, now)
    if (
        current != args["expected_version"]
        or args.get("template_version", template.version) != template.version
    ):
        raise Denied("precondition_conflict")
    previous_state = target.plan_state if target else "unconfigured"
    if action in {"create_draft", "propose"}:
        predecessor = target
        job = fresh_draft(args, template, basis, user_id, now, predecessor)
    else:
        job = decide(args, target, template, basis, predecessor, now)
    record = receipt(
        user_id,
        args,
        "job",
        job.pk,
        previous_state,
        job.plan_state,
        current,
        job.control_version,
        parameter=digest,
        origin=where,
    )
    response = project(job, template, predecessor, record.created_at) | {
        "action_id": str(record.pk)
    }
    ControlEvent.objects.create(
        job=job,
        actor_id=user_id,
        receipt=record,
        action=action,
        occurred_at=record.created_at,
        schedule_response=response,
    )
    end = finish_configure(session)
    review_command(action, basis, job, end)
    if action != "discard" and slots.hour(job.schedule_anchor_at) <= end:
        raise Denied("precondition_conflict")
    if action == "activate" and predecessor:
        check_operating(predecessor, end)
    return response


@never_cache
@api_view(["POST"])
def create(request):
    return schedule_response(request, None)


@never_cache
@api_view(["GET", "POST"])
def schedule(request, pk):
    return schedule_response(request, pk)


def schedule_response(request, job_id):
    if limited("collection", request.user.pk):
        return Response({"code": "rate_limited"}, status=429)
    try:
        if request.query_params:
            raise Denied("invalid_request")
        with request_transaction():
            require_configure(request.user.pk)
            if request.method == "GET":
                template, basis, job, predecessor = locate(job_id=job_id)
                session = control.lock_principals(request, {basis.executor_id})
                now = finish_configure(session)
                review(basis, now)
                review(job, now, job.schedule_anchor_at)
                return Response(project(job, template, predecessor, now, check_conflict=True))
            args = parse(
                request.data,
                {"create_draft"} if job_id is None else {"propose", "activate", "discard"},
            )
            result = perform(request, args, job_id)
    except Denied as exc:
        return reject(exc)
    return Response(result, status=201 if args["action"] in {"create_draft", "propose"} else 200)


@never_cache
@api_view(["GET"])
def template_directory(request):
    if limited("collection", request.user.pk):
        return Response({"code": "rate_limited"}, status=429)
    try:
        return Response(template_options(request))
    except Denied as exc:
        return reject(exc)


def template_options(request):
    """Configure-purpose branch of the shared, authenticated product directory."""
    values = params(request.query_params, {"purpose", "scope", "product", "page_size", "cursor"})
    if values.get("purpose") != "configure":
        raise Denied("invalid_request")
    synthetic = scope(values)
    size = parse_pk(values.get("page_size", "25"))
    if size > 50:
        raise Denied("query_too_large")
    product = parse_pk(values["product"]) if "product" in values else None
    user_id = request.user.pk
    key = [synthetic, product, size]
    after = decode_cursor(values.get("cursor"), "s08.configure", user_id, key)
    with request_transaction():
        session = control.lock_principals(request)
        require_configure(user_id)
        query = ScheduleTemplate.objects.filter(
            basis_job__product__synthetic=synthetic, basis_job__disposed=False
        )
        if product:
            query = query.filter(basis_job__product_id=product)
        if after:
            query = query.filter(
                Q(basis_job__product_id__gt=after["product"])
                | Q(basis_job__product_id=after["product"], pk__gt=after["template"])
            )
        results = []
        for template in (
            query.select_related("basis_job__product__source", "basis_job__city")
            .order_by("basis_job__product_id", "pk")
            .iterator()
        ):
            base = template.basis_job
            if not template_available(base):
                continue
            try:
                review(base, session.now)
            except Denied:
                continue
            plans = template.plans.filter(
                disposed=False, effective_until=None, plan_state__in=("draft", "active")
            ).order_by("pk")
            plan = plans.filter(plan_state="active").first() or plans.first()
            results.append(
                (
                    base,
                    {
                        "template": template.pk,
                        "template_version": template.version,
                        "product": base.product_id,
                        "source": base.product.source.namespace,
                        "contract_version": base.product.contract_version,
                        "scope_label": base.city.name if base.city_id else "national",
                        "allowed_cadences": template.allowed_cadences,
                        "delivery_minute": template.delivery_minute,
                        "plan_state": plan.plan_state if plan else "unconfigured",
                        "job": plan.pk if plan else None,
                    },
                )
            )
            if len(results) > size:
                break
        now = finish_configure(session)
        for base, _ in results:
            review(base, now)
    page = [row for _, row in results[:size]]
    token = None
    if len(results) > size:
        last = page[-1]
        token = encode_cursor(
            "s08.configure", user_id, key, {k: last[k] for k in ("product", "template")}
        )
    return {"results": page, "next_cursor": token}


def template_available(job, *, registration=False):
    if not job.product.synthetic:
        return True
    if cnemc_jobs.managed(job):
        return settings.CNEMC_FIXTURE_HTTP
    return is_fixture_profile(job) and (
        settings.S03_FIXTURE_HTTP or not registration and settings.S08_FIXTURE_SCHEDULE
    )


@transaction.atomic
def register_template(actor_id, job_id, cadences, minute, *, phase=None):
    job = lock_related({job_id})[job_id]
    lock_users_and_groups({actor_id, job.executor_id})
    actor(actor_id, "manage_collection")
    if (
        job.disposed
        or job.validation_grant_id
        or not template_available(job, registration=True)
        or not cadences
        or not all(cadence_ok(cadence) for cadence in cadences)
        or type(minute) is not int
        or not 0 <= minute <= 59
        or not cnemc_jobs.managed(job)
        and minute != 0
    ):
        raise Denied("invalid_schedule")
    cadences = sorted(set(cadences))
    now = clock()
    due = slots.hour(now).replace(minute=minute)
    validate_execution(candidate(job, cadences[0], due), due, now, host_registration=True)
    control.review_job(job, now, target=due)
    template, created = ScheduleTemplate.objects.get_or_create(
        basis_job=job,
        defaults={"allowed_cadences": cadences, "delivery_minute": minute},
    )
    if not created and (template.allowed_cadences, template.delivery_minute) != (cadences, minute):
        template.version += 1
        template.allowed_cadences, template.delivery_minute = cadences, minute
        template.save(update_fields=["version", "allowed_cadences", "delivery_minute"])
    if not template.revisions.filter(version=template.version).exists():
        ScheduleTemplateRevision.objects.create(
            template=template,
            version=template.version,
            allowed_cadences=template.allowed_cadences,
            delivery_minute=template.delivery_minute,
            actor_id=actor_id,
        )
        ControlEvent.objects.create(job=job, actor_id=actor_id, action="schedule_template")
    if phase is not None:
        anchor = cnemc_jobs.due_time(phase)
        if (
            job.plan_state != "legacy"
            or not job.enabled
            or job.next_due_at is None
            or anchor.second
            or anchor.microsecond
            or job.next_due_at.minute != minute
            or anchor > job.next_due_at
            or not 2 <= anchor.year <= 9998
            or job.cadence_seconds not in cadences
            or (job.next_due_at - anchor).total_seconds() % job.cadence_seconds
            or slots.overlapping_clocks(job, exclude=(job.pk,)).exists()
        ):
            raise Denied("precondition_conflict")
        boundary = slots.hour(anchor)
        review_takeover(job, boundary, now)
        if slots.future_ledger(job, boundary).exists():
            raise Denied("precondition_conflict")
        job.schedule_template, job.plan_state = template, "active"
        job.schedule_template_version = template.version
        job.schedule_anchor_at, job.effective_from = anchor, boundary
        job.save(
            update_fields=[
                "schedule_template",
                "schedule_template_version",
                "plan_state",
                "schedule_anchor_at",
                "effective_from",
            ]
        )
        ControlEvent.objects.create(job=job, actor_id=actor_id, action="schedule_enroll")
    elif job.plan_state == "legacy" and job.next_due_at is not None:
        raise Denied("precondition_conflict")
    return template
