"""Atomic chunks from a single in-memory response. Recovery never replays its bytes."""

from dataclasses import dataclass, replace
from datetime import timedelta

from django.db import transaction
from observations import revisions
from observations.catalog import resolve
from observations.models import Batch, QualityIssue, Receipt
from observations.policy import Denied, Scope, applicable, clock

from .adapters import WireError
from .admission import cell_key, data_scope
from .models import RunItem
from .services import LEASE, authorized, fence, guard_update, locked, terminal

CHUNK_SIZE = 128


@dataclass
class Cell:
    key: str
    entity_id: int
    row: object
    values: list


def identify(run, row, *, for_write=False):
    p = run.job.product
    t = row.time
    entity, geo = resolve(
        p, row.object_code, t.observed_at_utc, t.window_start, t.window_end, for_write=for_write
    )
    city = entity.city if p.level == "station" else entity
    if (run.job.city_id and city.pk != run.job.city_id) or city.name != row.area:
        raise Denied("city_mismatch")
    if p.level == "station" and entity.name != row.station_name:
        raise Denied("station_mismatch")
    return entity, geo


def batch_for(run):
    if run.batch_id is None:
        run.batch = Batch.objects.create(
            product=run.job.product,
            profile=run.job.profile,
            identity=None,
            acquired_at=run.reserved_at,
            fetched_at=run.fetched_at,
        )
        run.save(update_fields=["batch"])
    return run.batch


@transaction.atomic
def manifest(run_id, owner, parsed):
    run = locked(run_id)
    fence(run, owner)
    authorized(run)
    if run.request_state != "responded" or run.manifest_count is not None:
        raise Denied("invalid_manifest")
    cells = {}
    problems = set(parsed.issues)
    for row in parsed.rows:
        try:
            entity, _ = identify(run, row)
        except Denied as exc:
            if run.job.product.shape == "C":
                raise WireError(exc.code, responded=True) from None
            problems.add(exc.code)
            continue
        for value in row.fields:
            key = cell_key(entity.pk, row.time.observed_at_utc, value.indicator)
            if run.window.expected is not None and key not in run.window.expected:
                problems.add("outside_roster")
                continue
            if key not in cells:
                cells[key] = Cell(key, entity.pk, row, [])
            cells[key].values.append((row, value))
            if len(cells[key].values) > CHUNK_SIZE:
                raise WireError("too_many_duplicates", responded=True)
    run.manifest_count = len(cells)
    run.save(update_fields=["manifest_count"])
    if problems:
        scope = data_scope(run.job, run.window.target_start, run.window.target_end)
        try:
            applicable(run.job.product, "retain_normalized", scope, {"collection_ledger"}, clock())
        except Denied:
            problems.clear()
        for code in sorted(problems):
            QualityIssue.objects.create(
                product=run.job.product, batch=batch_for(run), code=code, scope=scope.serialize()
            )
    authorized(run)
    guard_update(run, owner, lease_until=clock() + LEASE)
    return [cells[key] for key in sorted(cells)]


def chunks(cells):
    current = []
    count = 0
    for cell in cells:
        if current and count + len(cell.values) > CHUNK_SIZE:
            yield current
            current = []
            count = 0
        current.append(cell)
        count += len(cell.values)
    if current or not cells:
        yield current


def metadata(ctx, data, row, geo):
    day = data.time.source_local_date
    scope = Scope(geo, data.object_code, data.indicator, day, day + timedelta(days=1))
    values = {"source_time": data.time.source_time}
    if data.indicator == "AQI":
        values.update(
            source_description=data.source_description,
            source_grade=row.source_grade,
            primary_pollutant=row.primary_pollutant,
        )
    result = {}
    for name, value in values.items():
        if value is None or value == "":
            continue
        try:
            revisions.check(ctx, "retain_normalized", scope, {name})
        except Denied:
            continue
        result[name] = value
    return result or None


def write_cell(ctx, run, cell, ordinal):
    row = cell.row
    field = cell.values[0][1]
    entity, geo = identify(run, row, for_write=True)
    if entity.pk != cell.entity_id:
        raise Denied("identity_changed")
    good = [(r, m.data) for r, m in cell.values if m.data is not None]
    present = any(m.present for _, m in cell.values)
    accepted = bool(good) and not run.window.items.filter(item_key=cell.key, accepted=True).exists()
    status = (
        ("accepted" if accepted else "reread") if good else ("rejected" if present else "absent")
    )
    item = RunItem.objects.create(
        run=run,
        window=run.window,
        item_key=cell.key,
        object_code=row.object_code,
        indicator=field.indicator,
        observed_at=row.time.observed_at_utc,
        owner=run.owner,
        generation=run.generation,
        accepted=accepted,
        status=status,
        reason="" if good else field.issue,
    )
    for r, data in good:
        if r != row and identify(run, r, for_write=True)[0].pk != cell.entity_id:
            raise Denied("identity_changed")
        details = metadata(ctx, data, r, geo)
        # Original representations and optional source text belong to this reading.
        # Canonical provenance avoids manufacturing a revision after field withdrawal.
        data = replace(
            data,
            source_description="",
            time=replace(
                data.time,
                source_time=data.time.observed_at_utc.isoformat(),
                timezone_basis="fixture_source_label",
            ),
        )
        ordinal += 1
        revisions.apply_observation(ctx, revisions.Candidate(data), ordinal)
        if (
            Receipt.objects.filter(
                batch=ctx.batch, ordinal=ordinal, rule_version=1, collection_item=None
            ).update(collection_item=item, collection_metadata=details)
            != 1
        ):
            raise Denied("invalid_receipt_lineage")
    return ordinal


def summarize(run):
    from .queries import tally

    w = run.window
    coverage = tally(w)[0]
    outcomes = set(
        Receipt.objects.filter(collection_item__run=run).values_list("outcome", flat=True)
    )
    if not outcomes and not run.items.filter(status="rejected").exists():
        return "no_new", coverage
    if not coverage["V"]:
        return "quality_only", coverage
    if coverage["O"] > coverage["V"] or (
        coverage["E"] is not None and coverage["E"] > coverage["O"]
    ):
        return "partial", coverage
    if "first" in outcomes:
        return "new", coverage
    if outcomes & {"content_changed", "unordered_conflict", "same_version_conflict"}:
        return "changed", coverage
    return "no_new", coverage


@transaction.atomic
def commit_chunk(run_id, owner, cells, ordinal, last):
    run = locked(run_id)
    fence(run, owner)
    authorized(run)
    if run.request_state != "responded" or run.manifest_count is None:
        raise Denied("invalid_manifest")
    if cells:
        ctx = revisions.WriteContext(
            run.job.product,
            run.executor_id,
            batch_for(run),
            list(run.job.product.usegrant_set.filter(revoked_at=None)),
            set(),
        )
        revisions.lock_stations(
            ctx.product,
            [revisions.Candidate(m.data) for cell in cells for _, m in cell.values if m.data],
        )
        for cell in cells:
            ordinal = write_cell(ctx, run, cell, ordinal)
    if last:
        if run.items.count() != run.manifest_count:
            raise Denied("incomplete_manifest")
        result, coverage = summarize(run)
        run.result = result
        run.save(update_fields=["result"])
        w = run.window
        complete = (
            coverage["accepted"] == coverage["E"]
            if coverage["E"] is not None
            else bool(run.manifest_count) and coverage["accepted"] == run.manifest_count
        )
        authorized(run)
        if cells:
            revisions.finish_checks(ctx)
        guard_update(
            run,
            owner,
            owner=None,
            lease_until=None,
            result=w.result if w.closed_at else result,
            closed_at=w.closed_at or (clock() if complete else None),
        )
        terminal(run, "finished")
    else:
        authorized(run)
        if cells:
            revisions.finish_checks(ctx)
        guard_update(
            run,
            owner,
            lease_until=clock() + LEASE,
            result=run.window.result if run.window.closed_at else "partial",
        )
    return ordinal
