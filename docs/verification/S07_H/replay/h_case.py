"""S07 real H supplement. Runs only after the A/B/R guard identifies its disposable PG."""

import hashlib
import io
import os
import re
from datetime import timedelta
from pathlib import Path

from s01_runtime import wait_for
from s04_checks import call, check, sql
from s05_v16 import intent, window_result
from s05_v16_oracles import (
    DIGEST,
    DOI,
    LICENSE,
    METRICS,
    PROFILE,
    SIZE,
    SPAN,
    STARTS,
    STATIONS,
    csv_oracle,
    sql_rows,
)

VERSION = "CC BY 4.0 / S07 H supplement v1"
READER = "s07-h-reader"
OPERATOR = "s07-h-operator"
CITY_CODE = "Beijing"  # Local identifier for the city named by the dataset, within uci.501 only.
CAT = "/api/v1/catalog/objects/"


def guard(run):
    from django.conf import settings

    match = re.fullmatch(r"(s07a-|s07-b-|s06-)([a-f0-9]{12})", run)
    check(match and os.environ.get("CNEMC_MODE", "off") == "off", "disposable instance required")
    r = match[1] == "s06-"
    prefix = "s06" if r else "s07"
    key = "airquality.s06_run" if r else "airquality.s07_instance"
    row = sql("SELECT current_database(),current_user,current_setting(%s,true)", key)[0]
    check(row == (f"{prefix}_db_{match[2]}", f"{prefix}_app_{match[2]}", run), "wrong database")
    check(settings.CNEMC_MODE == "off", "unsafe source mode")


def load_inner(stream):
    raw = stream.read(SIZE + 1)
    check(len(raw) == SIZE and hashlib.sha256(raw).hexdigest() == DIGEST, "UCI inner ZIP")
    return raw


def terms(product):
    return product.attribution + (
        " Asia/Shanghai assumed, hourly label endpoints unspecified. Selected two 48-hour windows;"
        " research coverage, valid-hour means and common-hour station differences."
    )


def grant(actor, product, op, audience, fields, start, end, *, code="*", indicator="*", until):
    from observations.policy import clock, register_grant

    return register_grant(
        actor.pk,
        product.pk,
        operation=op,
        audience=audience,
        geography="beijing",
        object_code=code,
        indicator=indicator,
        data_from=start,
        data_until=end,
        fields=sorted(fields),
        valid_from=clock(),
        valid_until=until,
        expiry_mode="recoverable" if op.startswith("retain_") else "hard",
        terms=f"{DOI} | {LICENSE} | {PROFILE}",
        terms_version=VERSION,
        attribution=terms(product),
    )


def register(password, *, restore=False):
    from django.contrib.auth.models import Permission, User
    from django.db import transaction
    from observations import import_uci as uci
    from observations.disposition import PACKAGE_SCOPE
    from observations.models import (
        City,
        CityIdentifier,
        Product,
        SiteEpoch,
        Source,
        Station,
        StationIdentifier,
    )
    from observations.policy import actor, clock
    from psycopg.types.range import Range

    check(not Product.objects.exists(), "new empty product catalog required")
    with transaction.atomic():
        op = User.objects.create_user(OPERATOR, password=password)
        for app, names in [
            ("observations", ("manage_grants", "import_history")),
            ("accounts", ("manage_backups", "restore_backup") if restore else ()),
        ]:
            op.user_permissions.add(
                *Permission.objects.filter(content_type__app_label=app, codename__in=names)
            )
        actor(op.pk, "manage_grants")
        actor(op.pk, "import_history")
        reader = User.objects.create_user(READER, password=password)
        check(not reader.get_all_permissions() and not reader.is_staff, "ordinary user")
        source = Source.objects.create(
            namespace=uci.SOURCE, publisher="UCI Machine Learning Repository"
        )
        p = Product.objects.create(
            source=source,
            code=uci.PRODUCT,
            family="H",
            shape="H",
            level="station",
            source_timezone=uci.NORMALIZATION.source_timezone,
            evidence=DOI,
            attribution=uci.ATTRIBUTION,
        )
        start, end = PACKAGE_SCOPE.start, PACKAGE_SCOPE.end
        until = clock() + timedelta(hours=2)
        for op_name, field in (
            ("acquire_file", "archive"),
            ("process_local", "archive"),
            ("retain_raw", "archive"),
            ("retain_normalized", "batch_identity"),
        ):
            grant(op, p, op_name, "platform", {field}, start, end, until=until)
        for code in (CITY_CODE, *STATIONS):
            grant(
                op,
                p,
                "retain_normalized",
                "platform",
                {"catalog"},
                start,
                end,
                code=code,
                until=until,
            )
        city = City.objects.create(source=source, name="Beijing", geography="beijing")
        period = Range(STARTS[0], STARTS[-1] + SPAN, "[)")
        CityIdentifier.objects.create(
            source=source,
            city=city,
            code=CITY_CODE,
            active_period=period,
            evidence=f"{DOI} | UCI 501 dataset-local Beijing reporting identity",
        )
        for code in STATIONS:
            station = Station.objects.create(source=source, name=code)
            StationIdentifier.objects.create(
                source=source, station=station, code=code, active_period=period, evidence=DOI
            )
            SiteEpoch.objects.create(
                station=station,
                city=city,
                name=code,
                active_period=period,
                location_evidence=DOI,
                certainty="dataset_reporting_identity_only",
            )
            for date in STARTS:
                for metric in METRICS:
                    for operation, audience, fields in (
                        ("process_local", "platform", {"normalized"}),
                        (
                            "retain_normalized",
                            "platform",
                            {
                                "normalized",
                                "provenance",
                                "source_time",
                                "catalog",
                                "research_coverage",
                            },
                        ),
                        (
                            "analyze",
                            "local_authenticated",
                            {
                                "normalized",
                                "provenance",
                                "catalog",
                                "research_coverage",
                                "research_summary",
                            },
                        ),
                        (
                            "display",
                            "local_authenticated",
                            {
                                "normalized",
                                "provenance",
                                "catalog",
                                "research_coverage",
                                "research_summary",
                            },
                        ),
                    ):
                        grant(
                            op,
                            p,
                            operation,
                            audience,
                            fields,
                            date.date(),
                            (date + SPAN).date(),
                            code=code,
                            indicator=metric,
                            until=until,
                        )
    return p, op, until


def grants(p):
    from observations.models import UseGrant

    rows = list(
        UseGrant.objects.filter(product=p)
        .order_by("pk")
        .values(
            "operation",
            "audience",
            "geography",
            "object_code",
            "indicator",
            "fields",
            "data_from",
            "data_until",
            "valid_from",
            "valid_until",
            "registered_at",
            "expiry_mode",
            "effect",
            "terms_version",
            "terms",
            "confirmed_by__username",
            "attribution",
        )
    )
    check(
        all(
            r["registered_at"] and r["valid_from"] <= r["registered_at"] < r["valid_until"]
            for r in rows
        ),
        "bounded registrations",
    )
    return rows


def stage(raw, op):
    from observations.history import stage_uci, submit
    from observations.models import ImportAttempt

    stage_id = stage_uci(op.pk, io.BytesIO(raw))
    attempt, dispatch = submit(
        op.pk, {"stage_id": str(stage_id), "profile": PROFILE, "reason": "historical_verification"}
    )
    check(dispatch == "sent", "import dispatch")
    wait_for(
        "UCI worker",
        lambda: ImportAttempt.objects.filter(pk=attempt).exclude(state="pending").exists(),
        seconds=120,
    )
    # consume() commits the business outcome before its finally block clears the staged file.
    wait_for(
        "UCI stage disposition",
        lambda: ImportAttempt.objects.filter(pk=attempt, stage__state="cleared").exists(),
        seconds=30,
    )
    row = ImportAttempt.objects.select_related("stage").get(pk=attempt)
    check(
        row.state == "succeeded" and row.deliveries == 1 and row.stage.state == "cleared",
        f"import {row.state}/{row.error_code}/{row.stage.state}",
    )
    from django.conf import settings

    check(not (Path(settings.S02_STAGE_DIR) / str(stage_id)).exists(), "staged file still present")
    return {
        "dispatch": dispatch,
        "worker_deliveries": row.deliveries,
        "state": row.state,
        "stage": row.stage.state,
    }


def discover(client):
    # Start from the public catalog. Neither product nor station IDs come from PG.
    body = call(client, CAT + "?page_size=1", uncached=True)
    rows = list(body["results"])
    while cursor := body["page"]["next_cursor"]:
        from urllib.parse import urlencode

        body = call(
            client, CAT + "?" + urlencode({"page_size": 1, "cursor": cursor}), uncached=True
        )
        rows.extend(body["results"])
        check(len(rows) <= 2, "unexpected extra catalog entries")
    check(
        len(rows) == 2 and {r["name"] for r in rows} == set(STATIONS), "two named H reporting sites"
    )
    for r in rows:
        check(
            r["source"] == "uci.501"
            and r["shape"] == "H"
            and r["synthetic"] is False
            and DOI in r["attribution"],
            "formal H attribution",
        )
        for link in ("link", "history_link"):
            check(r[link].startswith("/api/v1/"), "local navigation")
            dest = call(client, r[link], uncached=True)
            check(
                bool(dest["results"])
                and all(item["site_epoch"] == r["site_epoch"] for item in dest["results"]),
                "same station navigation",
            )
        r["station"] = call(client, r["link"])["station"]
    check(len({r["product"] for r in rows}) == 1, "single UCI dataset")
    return rows


def favorite(client, rows):
    r = rows[0]
    body = call(
        client,
        "/api/v1/me/favorites/",
        method="POST",
        status=201,
        body={"kind": "station", "target_id": r["station"], "product": r["product"]},
    )
    path = f"/api/v1/me/favorites/{body['id']}/"
    check(call(client, path)["id"] == body["id"], "favorite revisit")
    return path


def reconcile(client, rows, raw, *, full):
    from observations.models import Product

    product = Product.objects.get(pk=rows[0]["product"])
    epochs = {r["name"]: r["site_epoch"] for r in rows}
    values = csv_oracle(raw)
    indexed = sql_rows(product.pk, epochs, values)
    results = [
        window_result(
            client, product.pk, epochs, d, m, values, indexed, (product.attribution, terms(product))
        )
        for d in (STARTS if full else STARTS[:1])
        for m in (METRICS if full else ("CO",))
    ]
    first = intent(product.pk, [epochs[s] for s in STATIONS], results[0]["indicator"], STARTS[0])
    gap = intent(product.pk, [epochs[s] for s in STATIONS], "CO", STARTS[0] + SPAN)
    call(
        client, "/api/v1/analysis/compare/", method="POST", body=gap, status=403, code="use_denied"
    )
    for kind in ("detail", "aggregate"):
        call(
            client,
            "/api/v1/me/exports/",
            method="POST",
            body={
                "kind": kind,
                "groups": [first | {"input_version": results[0]["compare_version"]}],
            },
            status=403,
            code="use_denied",
        )
    check(sql("SELECT count(*) FROM accounts_exportticket") == [(0,)], "zero tickets")
    return {
        "results": results,
        "keys": len(indexed),
        "valid": sum(v[0] == "valid" for v in values.values()),
        "missing": sum(v[0] == "missing" for v in values.values()),
        "gap": 403,
        "export_detail": 403,
        "export_aggregate": 403,
        "tickets": 0,
    }


def withdrawal(client, rows):
    from django.contrib.auth.models import User
    from observations.disposition import reconcile_product
    from observations.models import Product, StationObservation, UseGrant
    from observations.policy import clock, register_grant, revoke_grant

    p = Product.objects.get(pk=rows[0]["product"])
    op = User.objects.get(username=OPERATOR)
    epochs = {r["name"]: r["site_epoch"] for r in rows}

    def sample(station):
        return StationObservation.objects.filter(
            product=p,
            object_code=station,
            indicator="CO",
            source_local_date__gte=STARTS[0].date(),
            source_local_date__lt=(STARTS[0] + SPAN).date(),
        ).count()

    query = intent(p.pk, [epochs[STATIONS[0]]], "CO", STARTS[0])
    sibling = intent(p.pk, [epochs[STATIONS[1]]], "CO", STARTS[0])
    endpoint = "/api/v1/analysis/statistics/"
    check(sample(STATIONS[0]) == sample(STATIONS[1]) == 48, "withdrawal baseline")
    for use in ("display", "retain_normalized"):
        g = UseGrant.objects.get(
            product=p,
            operation=use,
            object_code=STATIONS[0],
            indicator="CO",
            data_from=STARTS[0].date(),
        )
        revoke_grant(op.pk, g.pk, "S07 isolated H scope withdrawal")
        call(client, endpoint, method="POST", body=query, status=403, code="use_denied")
        reconcile_product(p.pk)
        check(
            sample(STATIONS[0]) == (48 if use == "display" else 0), "display/retention disposition"
        )
        check(
            sample(STATIONS[1]) == 48
            and call(client, endpoint, method="POST", body=sibling)["columns"][0]["O"] == 48,
            "unaffected authorized station",
        )
    # An explicit deny intersects both stations. Compare the actual hidden catalog to an absent one.
    now = clock()
    register_grant(
        op.pk,
        p.pk,
        operation="display",
        audience="local_authenticated",
        effect="deny",
        geography="beijing",
        data_from=STARTS[0].date(),
        data_until=(STARTS[-1] + SPAN).date(),
        valid_from=now,
        valid_until=now + timedelta(minutes=10),
        fields=["normalized"],
        terms=LICENSE,
        terms_version=VERSION,
        attribution=terms(p),
    )
    hidden = call(client, CAT + f"?product={p.pk}&page_size=1")
    check(
        hidden == call(client, CAT + "?product=2147483647&page_size=1")
        and not hidden["results"]
        and hidden["page"]["next_cursor"] is None,
        "hidden and absent catalog",
    )
    return {
        "display_revoked": {"read": 403, "PG_keys_retained": 48},
        "retain_revoked": {"read": 403, "PG_keys_after_disposition": 0},
        "sibling": {"HTTP": 200, "PG_keys": 48},
        "hidden_equals_absent": True,
    }
