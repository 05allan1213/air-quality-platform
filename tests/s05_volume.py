"""Isolation-guarded volume fixtures cloned from formally ingested F templates."""

import os
import re
from collections.abc import Iterable
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from observations.models import Receipt
from observations.revisions import tables
from s05_support import HOUR, START, candidate, ingest


def require_owned_database():
    run = os.environ.get("S05_VERIFY_RUN", "")
    evidence = os.environ.get("S05_EVIDENCE_DIR", "")
    if (
        not re.fullmatch(r"s05-[0-9a-f]{10}", run)
        or not evidence
        or Path(evidence).name != run
        or settings.CNEMC_MODE != "off"
        or connection.settings_dict["HOST"] != str(Path(evidence) / "pg")
    ):
        raise RuntimeError("Use scripts/verify_s05.py for volume verification")
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, inet_server_addr() IS NULL,
                   (SELECT rolcreatedb AND NOT rolsuper FROM pg_roles WHERE rolname = current_user)
        """)
        if cursor.fetchone() != ("test_s05verify", "s05_app", True, True):
            raise RuntimeError("Expected the runner-owned disposable pytest database")
    return Path(evidence)


def coordinates(codes, indicators, hours) -> Iterable[tuple[str, str, int]]:
    for hour in hours:
        for code in codes:
            for indicator in indicators:
                yield code, indicator, hour


def clone_fields(row, excluded):
    return {
        field.attname: getattr(row, field.attname)
        for field in row._meta.concrete_fields
        if not field.primary_key and field.attname not in excluded
    }


@transaction.atomic
def add_rows(operator, p, cells):
    """Seed distinct hours, including real receipt shape, for an authorized UTC F product.

    Each code/indicator template enters through ingest_batch once. Its key, revision, and
    contributing receipt are cloned only in the runner DB, keeping keys, units, supported
    standards and current flags real without timing thousands of unrelated write callbacks.
    All cells have fixed small values, so use analytical fixtures for correctness assertions.
    """
    require_owned_database()
    if not p.synthetic or p.family != "F" or p.source_timezone != "UTC":
        raise ValueError("Volume fixtures only support synthetic UTC inputs")
    cells = list(cells)
    if not cells:
        return
    keys, revisions, _, _, revision_field = tables(p.level)
    pairs = {(code, indicator) for code, indicator, _ in cells}
    existing = {
        (r.key.object_code, r.key.indicator): r
        for r in revisions.objects.filter(
            key__product=p, is_current=True, key__observed_at_utc=START
        ).select_related("key")
    }
    missing = pairs - set(existing)
    if missing:
        ingest(
            operator,
            p,
            [candidate(p, code, indicator=ind, value=10) for code, ind in sorted(missing)],
        )
        existing.update(
            {
                (r.key.object_code, r.key.indicator): r
                for r in revisions.objects.filter(
                    key__product=p, is_current=True, key__observed_at_utc=START
                ).select_related("key")
            }
        )
    templates = {pair: existing[pair] for pair in pairs}
    assert templates.keys() == pairs
    receipt_fk = revision_field + "_id"
    originals = {
        getattr(row, receipt_fk): row
        for row in Receipt.objects.filter(
            **{receipt_fk + "__in": [r.pk for r in templates.values()]}
        )
    }
    last_by_batch = {}
    for row in (
        Receipt.objects.filter(batch__product=p)
        .order_by("batch_id", "-ordinal")
        .distinct("batch_id")
    ):
        last_by_batch[row.batch_id] = row.ordinal
    rows = [(templates[code, ind], START + hour * HOUR) for code, ind, hour in cells if hour != 0]
    created = keys.objects.bulk_create(
        [
            keys(
                **clone_fields(r.key, {"observed_at_utc", "source_local_date"}),
                observed_at_utc=at,
                source_local_date=at.date(),
            )
            for r, at in rows
        ],
        batch_size=1000,
    )
    copies = revisions.objects.bulk_create(
        [
            revisions(
                **clone_fields(r, {"key_id", "provenance"}),
                key=key,
                provenance=r.provenance | {"source_time": at.isoformat()},
            )
            for (r, at), key in zip(rows, created, strict=True)
        ],
        batch_size=1000,
    )
    receipts = []
    for (original, _), revision in zip(rows, copies, strict=True):
        receipt = originals[original.pk]
        last_by_batch[receipt.batch_id] += 1
        receipts.append(
            Receipt(
                **clone_fields(receipt, {receipt_fk, "ordinal"}),
                **{receipt_fk: revision.pk},
                ordinal=last_by_batch[receipt.batch_id],
            )
        )
    Receipt.objects.bulk_create(receipts, batch_size=1000)
