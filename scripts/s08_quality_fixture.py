"""Authored historical F quality inputs for isolated S08 backend checks."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from s06_guard import require

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
AT = datetime(2024, 2, 6, 1, tzinfo=UTC)
START, END = AT - DAY, AT + 2 * DAY
PROFILE = "f-exact-v1"
BASIS = "S08 authored F quality roster and historical ledger"


def job_for(product, city, executor, code):
    from collection.models import Job

    return Job.objects.create(
        product=product,
        city=city,
        executor_id=executor,
        profile=PROFILE,
        expected_codes=[code],
        roster_basis=BASIS,
    )


def window_for(job, at):
    from collection.admission import expected
    from collection.models import WorkWindow

    return WorkWindow.objects.create(
        product=job.product,
        scope_key=f"city:{job.city_id}",
        target_start=at,
        target_end=at + HOUR,
        profile=job.profile,
        expected=expected(job, at, at + HOUR),
        roster_basis=BASIS,
        result="partial",
    )


def historical_run(curator, job, window, attempt, result, batch=None):
    from collection.models import Run

    started = window.target_end + timedelta(minutes=attempt * 5)
    return Run.objects.create(
        job=job,
        window=window,
        actor=curator,
        executor_id=job.executor_id,
        reason="manual",
        attempt=attempt,
        status="finished",
        result=result,
        dispatch="sent",
        deliveries=1,
        request_state="responded",
        reserved_at=started,
        created_at=started - timedelta(seconds=1),
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        fetched_at=started,
        generation=attempt,
        manifest_count=int(batch is not None),
        batch=batch,
    )


def record(curator, job, window, value, token, *, attempt=1, code=None, indicator="CO"):
    """Ingest through the domain writer, then attach its actual historical receipt."""
    from collection.admission import cell_key
    from collection.models import RunItem
    from observations.models import QualityIssue
    from observations.revisions import fixture_ticket, ingest_batch
    from s05_support import candidate

    product = job.product
    code = code or job.expected_codes[0]
    at = window.target_start
    started = window.target_end + timedelta(minutes=attempt * 5)
    batch, created = ingest_batch(
        curator.pk,
        product.pk,
        fixture_ticket(product.pk),
        product.contract_version,
        [candidate(product, code, at, value, indicator=indicator, token=token, fetched_at=started)],
    )
    require(created, "quality_fixture_fresh_batch")
    receipt = batch.receipts.get()
    revision = receipt.city_revision or receipt.station_revision
    entity_id = revision.key.city_id if product.level == "city" else revision.key.site_epoch_id
    run = historical_run(
        curator, job, window, attempt, "quality_only" if revision.candidate else "partial", batch
    )
    owner = uuid4()
    accepted = receipt.outcome == "first"
    item = RunItem.objects.create(
        run=run,
        window=window,
        owner=owner,
        generation=run.generation,
        item_key=cell_key(entity_id, at, indicator),
        object_code=code,
        indicator=indicator,
        observed_at=at,
        accepted=accepted,
        status="accepted" if accepted else "reread",
    )
    receipt.collection_item = item
    receipt.save(update_fields=["collection_item"])
    QualityIssue.objects.filter(batch=batch, receipt=receipt).update(run=run, run_item=item)
    return run, receipt


def case(window, run, event, receipt):
    return {
        "product": window.product_id,
        "issue": event.pk,
        "window": window.pk,
        "run": str(run.pk),
        "batch": receipt.batch_id,
        "receipt": receipt.pk,
        "city": run.job.city_id,
        "observed_at": window.target_start.isoformat(),
    }


def seed_ledger(curator, accounts, source):
    from collection.admission import LEDGER
    from collection.models import Job
    from collection.queries import read_window, tally
    from collection.writer import summarize
    from observations.policy import REQUIRED
    from s05_support import RETAIN, city, grant, product, site

    stations = product("ledger-stations", shape="S", source=source)
    parent = city(stations, "603", geography="ledger-city", start=START, end=END)
    codes = [f"LEDGER-S{n:02d}" for n in range(1, 10)]
    for code in codes:
        site(stations, code, parent=parent, start=START, end=END, name=code)
    job = Job.objects.create(
        product=stations,
        city=parent,
        executor_id=accounts["operator"]["id"],
        profile=PROFILE,
        expected_codes=codes,
        roster_basis=BASIS,
    )
    narrow_at = AT + DAY
    grant(curator, stations, "process_local", fields=REQUIRED, start=START, end=END)
    grant(curator, stations, "retain_normalized", fields=RETAIN | LEDGER, start=START, end=END)
    grant(curator, stations, "review", fields=LEDGER, start=AT, end=narrow_at.replace(hour=0))
    grant(
        curator,
        stations,
        "review",
        fields=LEDGER,
        start=narrow_at,
        end=END,
        object_code=codes[0],
        indicator="CO",
    )

    full = window_for(job, AT)
    first, _ = record(curator, job, full, 1, 1)
    empty = historical_run(curator, job, full, 2, "")
    empty.result, _ = summarize(empty, response_anomaly=False)
    empty.save(update_fields=["result"])
    narrow = window_for(job, narrow_at)
    record(curator, job, narrow, 2, 1)
    hidden, _ = record(curator, job, narrow, 5, 1, attempt=2, code=codes[-1], indicator="NO2")

    # Check the committed PostgreSQL facts and the production reader's scope before
    # exposing the disposable lookup IDs to Playwright's private manifest.
    full.refresh_from_db()
    empty.refresh_from_db()
    totals, _ = tally(full)
    require(
        not job.enabled
        and job.next_due_at is None
        and full.owner is None
        and full.closed_at is None
        and full.result == "partial"
        and empty.status == "finished"
        and empty.result == "no_new"
        and empty.manifest_count == 0
        and not empty.items.exists()
        and full.runs.count() == 2
        and full.runs.filter(pk=first.pk, result="partial").exists()
        and (totals["E"], totals["O"], totals["V"], totals["accepted"]) == (63, 1, 1, 1),
        "ledger_fixture_empty_second_run",
    )
    reviewer = accounts["reviewer"]["id"]
    first_page = read_window(reviewer, full.pk)
    next_page = read_window(reviewer, full.pk, {"after": "50"})
    permitted = read_window(reviewer, narrow.pk, {"object_code": codes[0], "indicator": "CO"})
    narrow_totals, _ = tally(narrow)
    require(
        len(first_page["cells"]) == first_page["next_after"] == 50
        and len(next_page["cells"]) == 13
        and next_page["next_after"] is None
        and narrow_totals["accepted"] == 2
        and permitted["state"] == "partially_authorized"
        and permitted["result"] is None
        and not permitted["runs"]
        and len(permitted["items"]) == len(permitted["receipts"]) == 1
        and len(permitted["cells"]) == 1
        and permitted["cells"][0]["object_code"] == codes[0],
        "ledger_fixture_projection_bounds",
    )
    return {
        "product": stations.pk,
        "start": AT.date().isoformat(),
        "end": (narrow_at + DAY).date().isoformat(),
        "full_window": full.pk,
        "empty_run": str(empty.pk),
        "narrow_window": narrow.pk,
        "hidden_run": str(hidden.pk),
        "allowed_code": codes[0],
        "hidden_code": codes[-1],
    }


def seed_operations(curator, source, window, receipt):
    from accounts.governance import receipt as audit_receipt
    from observations.models import DispositionJob
    from observations.policy import revoke_grant
    from s05_support import grant, product

    revision = receipt.city_revision
    require(
        receipt.outcome == "first"
        and revision.quality == "valid"
        and revision.value is not None
        and not revision.candidate
        and receipt.batch.fetched_at < revision.created_at,
        "operations_fixture_first_valid_write",
    )

    handoff = product("operations-grant-handoff", shape="C", source=source)
    current = grant(curator, handoff, "display", start=START, end=END)
    previous = grant(curator, handoff, "display", start=START - DAY, end=START)
    handoff.refresh_from_db(fields=["rights_version"])
    before = handoff.rights_version
    reason = "terms_ended"
    revoke_grant(curator.pk, previous.pk, reason)
    handoff.refresh_from_db(fields=["rights_version"])
    action = audit_receipt(
        curator.pk,
        {"action": "withdraw", "reason_code": reason, "request_id": uuid4()},
        "use_grant",
        previous.pk,
        "registered",
        "withdrawn",
        before,
        handoff.rights_version,
    )
    # This completed audit is independent of the withdrawal case. Its due job is
    # left for the read-only handoff preview.
    require(
        DispositionJob.objects.filter(product=handoff, reason="revocation").exists(),
        "operations_fixture_disposition",
    )
    return {
        "product": window.product_id,
        "window": window.pk,
        "start": AT.date().isoformat(),
        "end": (AT + DAY).date().isoformat(),
        "observed_at": window.target_start.isoformat(),
        "fetched_at": receipt.batch.fetched_at.isoformat(),
        "first_valid_write_at": revision.created_at.isoformat(),
        "audit": {
            "kind": "grant",
            "id": previous.pk,
            "start": (action.created_at - HOUR).isoformat(),
            "end": (action.created_at + DAY).isoformat(),
        },
        "grant": {"id": current.pk, "product": handoff.pk, "version": handoff.rights_version},
    }


def seed_quality(curator, accounts, run_id):
    from accounts.research_exports import export_fields
    from collection.admission import LEDGER
    from collection.models import Job, Run, WorkWindow
    from collection.queries import tally
    from django.conf import settings
    from django.db import transaction
    from observations.models import QualityIssue, Source, StationIdentifier
    from observations.policy import RAW, REQUIRED
    from observations.quality_events import QUALITY_DETAIL, issue_linkage
    from s05_support import RETAIN, authorize, city, grant, product, site

    require(not settings.S03_FIXTURE_HTTP and settings.CNEMC_MODE == "off", "quality_fixture_mode")
    with transaction.atomic():
        source = Source.objects.create(
            namespace=f"s08.f.quality.{run_id.rsplit('-', 1)[-1]}", publisher=BASIS
        )
        cities = product("quality-city", shape="C", source=source)
        stations = product("quality-station", shape="S", source=source)
        origin = city(
            cities, "601", geography="quality-origin", start=START, end=END, name="Quality Origin"
        )
        destination = city(
            cities, "602", geography="quality-target", start=START, end=END, name="Quality Target"
        )
        authorize(curator, cities, detail_fields=export_fields("detail"), start=START, end=END)
        grant(
            curator,
            cities,
            "retain_normalized",
            fields=RETAIN | LEDGER | RAW | QUALITY_DETAIL,
            start=START,
            end=END,
        )
        reviews = {
            key: grant(
                curator,
                cities,
                "review",
                fields=LEDGER | RAW | QUALITY_DETAIL,
                geography=city_row.geography,
                start=START,
                end=END,
            )
            for key, city_row in (("origin", origin), ("target", destination))
        }
        executor = accounts["operator"]["id"]
        origin_job = job_for(cities, origin, executor, "601")
        target_job = job_for(cities, destination, executor, "602")

        close_window = window_for(origin_job, AT)
        record(curator, origin_job, close_window, 10, 1)
        close_run, close_receipt = record(curator, origin_job, close_window, 20, 1, attempt=2)
        close_event = QualityIssue.objects.get(receipt=close_receipt, code="same_version_conflict")

        source_window = window_for(origin_job, AT + HOUR)
        record(curator, origin_job, source_window, 10, 1)
        source_run, source_receipt = record(curator, origin_job, source_window, 20, 1, attempt=2)
        source_event = QualityIssue.objects.get(
            receipt=source_receipt, code="same_version_conflict"
        )
        record(curator, origin_job, source_window, 30, 3, attempt=3)
        sibling = window_for(origin_job, AT + 2 * HOUR)
        _, sibling_receipt = record(curator, origin_job, sibling, 40, 1)

        mapping_window = window_for(origin_job, AT + 3 * HOUR)
        # Both initial heads are valid; remapping the origin's raw 50 µg/m³
        # gives 0.05 mg/m³, which still conflicts with the target's 20 mg/m³.
        mapping_run, mapping_receipt = record(curator, origin_job, mapping_window, 50, 7)
        target_window = window_for(target_job, mapping_window.target_start)
        target_run, target_receipt = record(curator, target_job, target_window, 20, 7)
        mapping_event = QualityIssue.objects.create(
            product=cities,
            city_key=mapping_receipt.city_revision.key,
            batch=mapping_receipt.batch,
            receipt=mapping_receipt,
            run=mapping_run,
            run_item=mapping_receipt.collection_item,
            code="mapping_review_required",
            rule_version=1,
            severity="warning",
        )

        epoch = site(stations, "Q-S01", parent=origin, start=START, end=END, name="Quality Station")
        ident = StationIdentifier.objects.get(station=epoch.station)
        grant(
            curator,
            stations,
            "retain_normalized",
            fields=REQUIRED | {"catalog"},
            start=START,
            end=END,
        )
        grant(
            curator,
            stations,
            "review",
            fields={"catalog"},
            geography=origin.geography,
            start=START,
            end=END,
        )

        for event in (close_event, source_event, mapping_event):
            issue_linkage(event)
        for window, valid in (
            (close_window, 0),
            (source_window, 0),
            (sibling, 1),
            (mapping_window, 1),
            (target_window, 1),
        ):
            coverage, _ = tally(window)
            require(
                (coverage["E"], coverage["O"], coverage["V"]) == (7, 1, valid),
                "quality_fixture_coverage",
            )
        require(
            not Job.objects.filter(product=cities).exclude(enabled=False, next_due_at=None).exists()
            and WorkWindow.objects.filter(product=cities, owner=None).count() == 5
            and Run.objects.filter(
                job__product=cities, status="finished", owner=None, request_state="responded"
            ).count()
            == 8,
            "quality_fixture_terminal_history",
        )
        quality = {
            "source": source.namespace,
            "start": AT.date().isoformat(),
            "end": (AT + DAY).date().isoformat(),
            "close": case(close_window, close_run, close_event, close_receipt),
            "ledger": seed_ledger(curator, accounts, source),
            "correct_source": case(source_window, source_run, source_event, source_receipt)
            | {
                "sibling_at": sibling.target_start.isoformat(),
                "sibling_window": sibling.pk,
            },
            "correct_mapping": case(mapping_window, mapping_run, mapping_event, mapping_receipt)
            | {
                "target": "602",
                "target_city": destination.pk,
                "target_window": target_window.pk,
                "target_run": str(target_run.pk),
                "target_receipt": target_receipt.pk,
                "target_review_grant": reviews["target"].pk,
            },
            "station": {
                "product": stations.pk,
                "station": epoch.station_id,
                "site_epoch": epoch.pk,
                "identifier": ident.pk,
                "at": AT.isoformat(),
                "target": "Quality Station Renamed",
            },
        }
        return quality, seed_operations(curator, source, sibling, sibling_receipt)
