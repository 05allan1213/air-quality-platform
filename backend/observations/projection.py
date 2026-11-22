"""Project the receipt that produced a revision. Later deliveries are not its lineage."""

from datetime import timedelta

from .models import Receipt
from .policy import Denied, Scope
from .revisions import tables

PROVENANCE = (
    "source_timezone",
    "timezone_basis",
    "window_start",
    "window_end",
    "label_position",
    "standard_basis",
    "standard_evidence",
    "source_published_at",
    "mapping",
    "grade",
    "color",
    "comparable",
    "issue",
)
OPTIONAL = ("source_time", "source_description", "source_grade", "primary_pollutant")


def originals(product, revisions):
    if not revisions:
        return {}
    fk = tables(product.level)[4]
    by_id = {r.pk: r for r in revisions}
    receipts = (
        Receipt.objects.filter(
            batch__product=product,
            batch__redacted=False,
            outcome__in=("first", "content_changed"),
            **{fk + "_id__in": by_id},
        )
        .select_related("batch__collection_run", "collection_item__run")
        .order_by("pk")
    )
    result = {}
    for receipt in receipts:
        rid = getattr(receipt, fk + "_id")
        r = by_id[rid]
        if r.kind not in ("initial", "source_revision"):
            continue
        item = receipt.collection_item
        run = getattr(receipt.batch, "collection_run", None)
        if run or item or product.source.namespace in ("cnemc", "cnemc.fixture"):
            if not (
                run
                and item
                and item.run_id == run.pk
                and run.batch_id == receipt.batch_id
                and item.window_id == run.window_id
                and item.object_code == r.key.object_code
                and item.indicator == r.key.indicator
                and item.observed_at == r.key.observed_at_utc
            ):
                continue
        # Ambiguous attribution is unknown, even if the two receipt texts happen to match.
        result[rid] = None if rid in result else receipt
    return result


def metadata(revision, receipt, permitted):
    prov = {key: revision.provenance.get(key) for key in PROVENANCE}
    source = {}
    if receipt is not None:
        if receipt.collection_item_id is None:
            source.update(
                source_time=revision.provenance.get("source_time"),
                source_description=revision.source_description,
            )
        source.update(receipt.collection_metadata or {})
    for field in OPTIONAL:
        prov[field] = source.get(field) if permitted(field) else None
    return prov


def public_rows(ctx, product, revisions):
    original = originals(product, revisions)
    entity = tables(product.level)[2]
    output = []
    for revision in revisions:
        key = revision.key
        entity_id = getattr(key, entity + "_id")
        if key.product_id != product.pk:
            raise Denied()
        geo = ctx._geographies[product.source_id, product.level, entity_id]
        scope = Scope(
            geo,
            key.object_code,
            key.indicator,
            key.source_local_date,
            key.source_local_date + timedelta(days=1),
        )
        base = ctx.basis(product, scope)
        if not base:
            raise Denied()
        receipt = original.get(revision.pk)
        prov = metadata(revision, receipt, lambda f, scope=scope: ctx.basis(product, scope, (f,)))
        disputed = key.disputed
        reliable = not disputed and revision.quality == "valid" and revision.value is not None
        if disputed:
            for label in (
                "issue",
                "grade",
                "color",
                "source_grade",
                "source_description",
                "primary_pollutant",
            ):
                prov[label] = None
        if not reliable or key.standard_series == "unknown":
            prov.update(grade=None, color=None, comparable=False)
        output.append(
            dict(
                **prov,
                **{entity: entity_id},
                source=product.source.namespace,
                product=product.pk,
                synthetic=product.synthetic,
                key=key.pk,
                revision=revision.pk,
                qualification_version=key.qualification_version,
                revision_no=revision.revision_no,
                indicator=key.indicator,
                observed_at=key.observed_at_utc,
                source_local_date=key.source_local_date,
                window_definition=key.window_definition,
                report_kind=key.report_kind,
                standard_series=key.standard_series,
                value=str(revision.value) if not disputed and revision.value is not None else None,
                unit=revision.unit,
                quality="disputed" if disputed else revision.quality,
                censor_limit=None
                if disputed or revision.limit_value is None
                else str(revision.limit_value),
                censor_direction="" if disputed else revision.limit_operator,
                fetched_at=receipt.batch.fetched_at if receipt else None,
                fetched_at_basis="contributing_batch" if receipt else "unknown",
                age_seconds=int((ctx.now - key.observed_at_utc).total_seconds()),
                freshness="unknown",
                source_status="unknown",
                attribution=product.attribution,
                rights_attributions=list(base.attributions),
            )
        )
    return output
