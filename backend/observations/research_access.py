"""Resolve complete scoped uses before exposing identity, counts, versions or rows."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q

from .models import Product, SiteEpoch, Source, Station, UseGrant
from .policy import REQUIRED, Denied, Scope, actor, applicable, clock
from .selection import TICK, ReadContext

LOCAL = "local_authenticated"
DERIVED = {"catalog", "research_coverage", "research_summary"}
INPUT = REQUIRED | {"catalog", "research_coverage"}


class ResearchContext(ReadContext):
    """Share S04's identifier intersection. Fetch grants only for the selected dimensions."""

    def __init__(self, user_id, spec, now=None, *, locked=False, nowait=False):
        from django.db import connection

        if not connection.in_atomic_block:
            raise RuntimeError("Research reads require an atomic transaction")
        product_qs = Product.objects.filter(
            pk=spec.product_id, synthetic=spec.synthetic, source__namespace=spec.source
        )
        if not locked:
            sources = list(
                Source.objects.select_for_update(nowait=nowait)
                .filter(pk__in=product_qs.values("source_id"))
                .order_by("pk")
            )
            product_qs = product_qs.select_for_update(of=("self",), nowait=nowait)
        product = product_qs.select_related("source").first()
        if product is None or (not locked and (not sources or product.source_id != sources[0].pk)):
            raise Denied()
        self.products = {product.pk: product}
        self.user_id = user_id
        actor(user_id)
        self._identities = {}
        self._geographies = {}
        self.epochs = {}
        if product.level == "station":
            epochs = SiteEpoch.objects.filter(
                pk__in=spec.ids,
                station__source_id=product.source_id,
                city__source_id=product.source_id,
            )
            if not locked:
                list(
                    Station.objects.select_for_update(nowait=nowait)
                    .filter(pk__in=epochs.values("station_id"))
                    .order_by("pk")
                )
            self.epochs = {e.pk: e for e in epochs.select_related("city", "station")}
        self.now = now or clock()


def uses(purpose, spec, fields=()):
    detail_only = spec.method == "detail"
    retain = REQUIRED | {"catalog"} if detail_only else INPUT
    if purpose in {"export", "export_aggregate"}:
        obligations = [("retain_normalized", "platform", retain | {"export_manifest"})]
        if purpose == "export_aggregate":
            obligations.append(("analyze", LOCAL, REQUIRED | DERIVED))
        obligations.append((purpose, LOCAL, set(fields) | {"export_manifest"}))
        return obligations
    obligations = [("retain_normalized", "platform", retain)]
    if not detail_only:
        obligations.append(("analyze", LOCAL, REQUIRED | DERIVED))
    display = {"catalog"} if detail_only else DERIVED
    if purpose == "details" or detail_only:
        display = display | REQUIRED
    obligations.append(("display", LOCAL, display))
    return obligations


def overlap(grant, scope, fields):
    return (
        grant.geography in {"*", scope.geography}
        and grant.object_code in {"*", scope.object_code}
        and grant.indicator in {"*", scope.indicator}
        and grant.data_from < scope.end
        and grant.data_until > scope.start
        and ("*" in grant.fields or bool(set(grant.fields) & set(fields)))
    )


def affects_basis(product, scope, fields, grant, at):
    """Check a matching grant's effect on the authorized operation and span."""
    if (
        grant.revoked_at
        or grant.valid_from > at
        or any(end and end <= at for end in (grant.valid_until, grant.retain_until))
    ):
        return False
    if grant.effect == "deny" or grant.restrictions:
        return True
    span = Scope(
        scope.geography,
        scope.object_code,
        scope.indicator,
        max(scope.start, grant.data_from),
        min(scope.end, grant.data_until),
    )
    try:
        applicable(product, grant.operation, span, fields, at, [grant], audience=grant.audience)
    except Denied:
        return False
    return True


def authorized_parts(product, scope, operation, audience, fields, rows, now):
    candidates = [
        g
        for g in rows
        if g.operation == operation and g.audience == audience and overlap(g, scope, fields)
    ]
    future = min(
        (
            g.valid_from
            for g in candidates
            if g.valid_from > now and affects_basis(product, scope, fields, g, g.valid_from)
        ),
        default=None,
    )
    # Coalesce by the current basis so future grants never split the signed manifest.
    cuts = {scope.start, scope.end}
    for grant in candidates:
        cuts.update((max(scope.start, grant.data_from), min(scope.end, grant.data_until)))
    ordered = sorted(cuts)
    parts = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        span = Scope(scope.geography, scope.object_code, scope.indicator, start, end)
        basis = applicable(product, operation, span, fields, now, candidates, audience=audience)
        if parts and parts[-1][2] == basis:
            parts[-1] = (parts[-1][0], end, basis)
        else:
            parts.append((start, end, basis))
    result = []
    for start, end, basis in parts:
        until = min((t for t in (basis.until, future) if t), default=None)
        result.append(
            dict(
                product=product.pk,
                operation=operation,
                audience=audience,
                fields=sorted(fields),
                scope=Scope(
                    scope.geography, scope.object_code, scope.indicator, start, end
                ).serialize(),
                ids=list(basis.ids),
                grant_until=basis.until.isoformat() if basis.until else None,
                until=until.isoformat() if until else None,
                attributions=list(basis.attributions),
            )
        )
    return result


class Access:
    def __init__(self, ctx, spec, purpose, fields):
        self.ctx = ctx
        self.product = p = ctx.products[spec.product_id]
        self.spec = spec
        self.obligations = uses(purpose, spec, fields)
        self.identities = ctx.identities(p, spec.ids)
        if set(spec.ids) != {i[0] for i in self.identities} or (p.level == "city") != (
            spec.entity_key == "cities"
        ):
            raise Denied()
        zone = ZoneInfo(p.source_timezone)
        self.first = spec.start.astimezone(zone).date()
        self.last = (spec.end - TICK).astimezone(zone).date() + timedelta(days=1)
        self.scopes = set()
        for entity in spec.ids:
            records = [r for r in self.identities if r[0] == entity]
            intersect = [r for r in records if r[3] < spec.end and (not r[4] or r[4] >= spec.start)]
            # Entirely outside a known epoch still requires the requested dates and directory use.
            for _, code, geo, low, high in intersect or records:
                first = (
                    max(self.first, low.astimezone(zone).date())
                    if len(intersect) > 1
                    else self.first
                )
                last = (
                    min(self.last, high.astimezone(zone).date() + timedelta(days=1))
                    if len(intersect) > 1 and high
                    else self.last
                )
                for ind in spec.indicators:
                    self.scopes.add(Scope(geo, code, ind, first, last))
        rows = UseGrant.objects.filter(
            product=p,
            revoked_at__isnull=True,
            data_from__lt=self.last,
            data_until__gt=self.first,
            geography__in={"*", *(s.geography for s in self.scopes)},
            object_code__in={"*", *(s.object_code for s in self.scopes)},
            indicator__in={"*", *spec.indicators},
        ).filter(
            Q(operation="retain_normalized", audience="platform")
            | Q(operation__in=["display", "analyze", "export", "export_aggregate"], audience=LOCAL)
        )
        self.grants = list(rows.order_by("pk"))
        self.basis = self.evaluate(self.obligations, ctx.now)

    def evaluate(self, obligations, now):
        result = []
        for op, audience, fields in obligations:
            for scope in sorted(
                self.scopes, key=lambda s: (s.geography, s.object_code, s.indicator, s.start, s.end)
            ):
                result.extend(
                    authorized_parts(self.product, scope, op, audience, fields, self.grants, now)
                )
        if len(result) > 256 * len(obligations):
            raise Denied("query_too_large")
        return result

    def optionally(self, obligations):
        try:
            extra = self.evaluate(obligations, self.ctx.now)
        except Denied as exc:
            if exc.code != "use_denied":
                raise
            return False
        self.obligations += obligations
        self.basis += extra
        return True

    def after(self, now=None):
        actor(self.ctx.user_id)
        if self.evaluate(self.obligations, now or clock()) != self.basis:
            raise Denied()
