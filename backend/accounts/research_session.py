"""Lock boundaries shared by private research resources and CSV requests."""

from contextlib import contextmanager
from time import monotonic

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import DateTimeField, Func, Subquery
from django.utils.crypto import constant_time_compare
from observations.models import Product, SiteEpoch, Source, Station
from observations.policy import Denied, clock
from observations.research_access import Access, ResearchContext
from observations.s04_http import decode_cursor, encode_cursor, page_size, params
from rest_framework.response import Response

from .api import error
from .models import AccountState
from .session_generation import matches_generation


class Budget:
    def __init__(self):
        self.deadline = monotonic() + 10
        self.limit = 5000

    def remaining_ms(self):
        remaining = int((self.deadline - monotonic()) * 1000)
        if remaining < 1:
            raise Denied("database_unavailable")
        return remaining

    def execute(self, execute, sql, params, many, context):
        limit = min(5000, self.remaining_ms())
        if limit != self.limit:
            execute(
                "SELECT set_config('statement_timeout', %s, true)", [f"{limit}ms"], False, context
            )
            self.limit = limit
        result = execute(sql, params, many, context)
        self.remaining_ms()
        return result


@contextmanager
def request_transaction():
    budget = Budget()
    with transaction.atomic(durable=True):
        with connection.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cur.execute("SET LOCAL lock_timeout = '1s'")
            cur.execute("SET LOCAL statement_timeout = '5s'")
        with connection.execute_wrapper(budget.execute):
            yield budget
            budget.remaining_ms()


def authorize_inputs(user_id, specs, *, purpose="analysis", fields=(), now=None):
    """Check all groups without dependency locks or constructing a data relation."""
    accesses = []
    too_large = False
    for spec in specs:
        try:
            ctx = ResearchContext(user_id, spec, now, locked=True)
            now = ctx.now  # Use the first principal check's PG instant for every group.
            accesses.append(Access(ctx, spec, purpose, fields))
        except Denied as exc:
            if exc.code != "query_too_large":
                raise
            too_large = True
    if too_large:
        raise Denied("query_too_large")
    return accesses


def lock_dependencies(specs):
    """Lock all dependency kinds in the global order, even for reverse input order."""
    products = {spec.product_id for spec in specs}
    source_ids = Product.objects.filter(pk__in=products).values("source_id")
    locked_sources = set(
        Source.objects.select_for_update()
        .filter(pk__in=source_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    by_id = {
        p.pk: p
        for p in Product.objects.select_for_update(of=("self",))
        .filter(pk__in=products)
        .select_related("source")
        .order_by("pk")
    }
    if any(p.source_id not in locked_sources for p in by_id.values()):
        raise Denied("input_changed")
    epoch_ids = {
        entity for spec in specs if spec.entity_key == "site_epochs" for entity in spec.ids
    }
    epochs = SiteEpoch.objects.filter(pk__in=epoch_ids, station__source_id__in=locked_sources)
    set(
        Station.objects.select_for_update()
        .filter(pk__in=epochs.values("station_id"))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


class SessionCheck:
    """Recheck the signed database session referenced by the incoming cookie."""

    def __init__(self, request):
        self.user_id = request.user.pk
        self.key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        user = get_user_model().objects.select_for_update().filter(pk=self.user_id).first()
        if not self.key or request.session.session_key != self.key:
            raise Denied("not_authenticated")
        # The user lock serializes every session-generation writer. Read the bound
        # generation in the next statement, after any wait for that lock.
        self.rows = Session.objects.filter(session_key=self.key).annotate(
            current_generation=Subquery(
                AccountState.objects.filter(pk=self.user_id).values("session_generation")[:1]
            )
        )
        row = self.rows.select_for_update().first()
        self.now = clock()
        self.verify(user, row, self.now)

    def finish(self, relations):
        user = get_user_model().objects.filter(pk=self.user_id).first()
        row = self.rows.annotate(
            checked_at=Func(function="clock_timestamp", output_field=DateTimeField())
        ).first()
        if row is None:
            raise Denied("not_authenticated")
        now = row.checked_at
        self.verify(user, row, now)
        # Recheck the complete frozen basis on the same PG clock as the session and
        # principal. The locked user was just revalidated above.
        for relation in relations:
            access = relation.access
            if access.evaluate(access.obligations, now) != access.basis:
                raise Denied()
        return now

    def verify(self, user, row, now):
        if user is None or not user.is_active or row is None or row.expire_date <= now:
            raise Denied("not_authenticated")
        data = row.get_decoded()
        digest = data.get(HASH_SESSION_KEY)
        if (
            str(data.get(SESSION_KEY)) != str(user.pk)
            or data.get(BACKEND_SESSION_KEY) not in settings.AUTHENTICATION_BACKENDS
            or not matches_generation(data, row.current_generation or 0)
            or not isinstance(digest, str)
            or not constant_time_compare(digest, user.get_session_auth_hash())
        ):
            raise Denied("not_authenticated")


def scope(values):
    value = values.get("scope", "formal")
    if value not in ("formal", "synthetic"):
        raise Denied("invalid_request")
    return value == "synthetic"


def owned(model, user_id, synthetic, pk=None, *, lock=False):
    items = model.objects.filter(owner_id=user_id, synthetic_scope=synthetic)
    if pk is None:
        return items
    if lock:
        items = items.select_for_update()
    item = items.filter(pk=pk).first()
    if item is None:
        raise Denied("not_found")
    return item


def paged(model, request, route, project, *, prefetch=None):
    from uuid import UUID

    from django.db.models import Q
    from observations.s04_http import instant

    query = params(request.query_params, {"scope", "cursor", "page_size"})
    synthetic, size = scope(query), page_size(query)
    user_id = request.user.pk
    conditions = [synthetic, size]
    cursor = decode_cursor(query.get("cursor"), route, user_id, conditions)
    items = owned(model, user_id, synthetic).order_by("-created_at", "-id")
    if cursor:
        created = instant(cursor["created"])
        last_id = UUID(cursor["id"])
        items = items.filter(Q(created_at__lt=created) | Q(created_at=created, id__lt=last_id))
    if prefetch:
        items = items.prefetch_related(prefetch)
    rows = list(items[: size + 1])
    next_cursor = None
    if len(rows) > size:
        last = rows[size - 1]
        next_cursor = encode_cursor(
            route, user_id, conditions, {"created": last.created_at.isoformat(), "id": str(last.pk)}
        )
    SessionCheck(request)
    return Response(
        {
            "results": [project(row) for row in rows[:size]],
            "page": {"page_size": size, "next_cursor": next_cursor},
        }
    )


def reject(exc):
    status = {
        "use_denied": 403,
        "permission_denied": 403,
        "not_authenticated": 403,
        "not_found": 404,
        "input_changed": 409,
        "method_changed": 409,
        "expired": 410,
        "incompatible_series": 422,
        "rate_limited": 429,
        "database_unavailable": 503,
    }.get(exc.code, 400)
    return error(exc.code, status)
