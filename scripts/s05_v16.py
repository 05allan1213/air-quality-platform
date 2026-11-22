"""Run with verify_s05.py and the official UCI inner ZIP on stdin. Emit only aggregates."""

import hashlib
import json
import re
import secrets
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

import django
from s04_checks import Probe, call, check, django_check, sql
from s05_runtime_checks import owned_database, restarted_web
from s05_v16_import import run_import
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
    ZONE,
    csv_oracle,
    csv_summary,
    sql_rows,
    sql_summary,
    unit,
)

API = "/api/v1/analysis/"
PAGE = 25
RESEARCH = "CC BY 4.0 / S05 V16 research v1"
FIELDS = {
    "retain_normalized": ("platform", ["normalized", "provenance", "catalog", "research_coverage"]),
    "analyze": (
        "local_authenticated",
        ["normalized", "provenance", "catalog", "research_coverage", "research_summary"],
    ),
    "display": (
        "local_authenticated",
        ["normalized", "provenance", "catalog", "research_coverage", "research_summary"],
    ),
}


def register_research(operator, product):
    from django.db import transaction
    from observations.policy import clock, register_grant

    attribution = product.attribution + (
        " Asia/Shanghai assumed, hourly label endpoints unspecified. "
        "Research coverage, reliable-hour concentration means and common-hour station differences."
    )
    now = clock()
    with transaction.atomic():
        for station in STATIONS:
            for start in STARTS:
                for metric in METRICS:
                    for op, (audience, fields) in FIELDS.items():
                        register_grant(
                            operator.pk,
                            product.pk,
                            operation=op,
                            audience=audience,
                            fields=fields,
                            geography="beijing",
                            object_code=station,
                            indicator=metric,
                            data_from=start.date(),
                            data_until=(start + SPAN).date(),
                            valid_from=now,
                            valid_until=now + timedelta(hours=2),
                            expiry_mode="recoverable" if op == "retain_normalized" else "hard",
                            terms=f"UCI dataset 501 {DOI} | {LICENSE} | {PROFILE}",
                            terms_version=RESEARCH,
                            attribution=attribution,
                        )
    return attribution


def intent(product, epochs, metric, start):
    return dict(
        selection=dict(
            product=product,
            source="uci.501",
            scope="formal",
            site_epochs=epochs,
            indicators=[metric],
            units={metric: unit(metric)},
            standard_series={metric: "not_applicable"},
            window_definition="hourly_label_unspecified",
            start=start.isoformat(),
            end=(start + SPAN).isoformat(),
        ),
        method="compare" if len(epochs) > 1 else "statistics",
        method_version="s05.v1",
        grain="day",
    )


def near(left, right, label):
    # The HTTP renderer uses JSON numbers; at this scale this is below 1e-9 in the indicated unit.
    check(abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("1e-9"), label)


def check_metadata(body, attributions, query):
    check(
        body["method_version"] == "s05.v1" and re.fullmatch("[a-f0-9]{64}", body["input_version"]),
        "method/input versions",
    )
    check(
        body["selection"]["source"] == "uci.501"
        and body["selection"]["scope"] == "formal"
        and body["selection"]["product"] == query["selection"]["product"]
        and body["selection"]["site_epochs"] == query["selection"]["site_epochs"],
        "HTTP research product and identities",
    )
    check(
        body["source_timezone"] == ZONE.key
        and body["timezone_basis"] == "timezone_assumed"
        and body["label_position"] == "unspecified"
        and body["calendar_versions"] == [PROFILE],
        "research calendar and time semantics",
    )
    check(
        set(body["attribution"]) == set(attributions),
        "dataset attribution, license and transformations",
    )
    check(
        all(
            datetime.fromisoformat(body["requested_window"][k])
            == datetime.fromisoformat(query["selection"][k])
            for k in ("start", "end")
        ),
        "requested historical window",
    )


def check_coverage(col, slots, metric):
    received = [s for s in slots if s is not None]
    good = [v for q, v in received if q == "valid"]
    counts = Counter(q for q, _ in received)
    check((col["E"], col["O"], col["V"]) == (len(slots), len(received), len(good)), "E/O/V")
    reasons = dict.fromkeys(
        ("gap", "missing", "censored", "invalid", "suspect", "disputed", "unknown"), 0
    )
    reasons.update(
        {q: n for q, n in counts.items() if q != "valid"}, gap=len(slots) - len(received)
    )
    check(
        col["exclusions"] == reasons
        and col["denominator_state"] == "known"
        and col["outside_schedule"] == 0,
        "quality exclusions and gaps",
    )
    near(col["received_ratio"], len(received) / len(slots), "received coverage")
    near(col["valid_ratio"], len(good) / len(slots), "valid coverage")
    if "unit" in col:
        check(
            col["indicator"] == metric
            and col["unit"] == unit(metric)
            and col["standard_series"] == "not_applicable",
            "column metric and unit",
        )
    return sum(good) / len(good)


def details(reader, query, version, support, expected, values, indexed, epochs, attribution):
    metric = query["selection"]["indicators"][0]
    targets = query["selection"]["site_epochs"]
    by_epoch = {v: k for k, v in epochs.items()}
    request = query | dict(input_version=version, support=support, page_size=PAGE)
    seen = []
    pages = max(1, (len(expected) + PAGE - 1) // PAGE)
    for _ in range(pages):
        body = call(reader, API + "details/", method="POST", body=request, uncached=True)
        check(
            body["input_version"] == version and body["method_version"] == "s05.v1",
            "parent version on every page",
        )
        rows = body["results"]
        check(len(rows) == min(PAGE, len(expected) - len(seen)), "detail page size")
        for row in rows:
            stamp = datetime.fromisoformat(row["observed_at"])
            entity = row["entity"]
            key = by_epoch[entity], metric, stamp
            quality, value = values[key]
            db_key, revision, qualification, fetched = indexed[key]
            check(
                (row["key"], row["revision"], row["qualification_version"])
                == (db_key, revision, qualification),
                "SQL/HTTP key, revision and qualification",
            )
            check(
                row["pos"] == targets.index(entity)
                and row["indicator"] == metric
                and row["quality"] == quality
                and (None if row["value"] is None else Decimal(row["value"])) == value,
                "CSV/SQL/HTTP support row",
            )
            check(
                row["product"] == query["selection"]["product"]
                and row["source"] == "uci.501"
                and row["synthetic"] is False
                and row["unit"] == unit(metric)
                and row["source_timezone"] == ZONE.key
                and row["standard_series"] == "not_applicable"
                and row["window_definition"] == "hourly_label_unspecified"
                and row["report_kind"] == "hourly"
                and row["attribution"] == attribution
                and row["fetched_at_basis"] == "contributing_batch"
                and datetime.fromisoformat(row["fetched_at"]) == fetched,
                "support row provenance",
            )
            seen.append((stamp, targets.index(entity)))
        cursor = body["page"]["next_cursor"]
        check(bool(cursor) == (len(seen) < len(expected)), "cursor ends exactly at last page")
        request = request | {"cursor": cursor}
    check(
        seen == sorted(expected), "all supporting members, stable order, no omissions or duplicates"
    )
    return pages


def window_result(reader, product, epochs, start, metric, values, indexed, attributions):
    targets = [epochs[s] for s in STATIONS]
    query = intent(product, targets, metric, start)
    csv_cols, csv_pair, delta = csv_summary(values, start, metric)
    pg_cols, pg_pair, pg_delta = sql_summary(product, start, metric)
    for a, b in zip([*csv_cols, csv_pair], [*pg_cols, pg_pair], strict=True):
        check(a[:-1] == b[:-1], "CSV/SQL denominators and samples")
        near(a[-1], b[-1], "CSV/SQL weighted mean or paired difference")
    check(delta == pg_delta, "CSV/SQL coincident deltas")
    cmp = call(reader, API + "compare/", method="POST", body=query, uncached=True)
    check_metadata(cmp, attributions, query)
    check(
        (cmp["E_common"], cmp["P"], cmp["actual_pairs"]) == (csv_pair[0], csv_pair[1], len(delta))
        and cmp["conclusion"]["state"] == "sufficient",
        "common coverage and conclusion",
    )
    near(cmp["paired_ratio"], csv_pair[1] / csv_pair[0], "common coverage ratio")
    near(cmp["mean_difference"], csv_pair[-1], "common-hour mean A minus B")
    points = cmp["paired_points"]
    check(
        [datetime.fromisoformat(pt["at"]) for pt in points] == sorted(delta),
        "every joint hour in order",
    )
    for pt in points:
        t = datetime.fromisoformat(pt["at"])
        near(pt["difference"], delta[t], "same-hour difference")
        base = values[STATIONS[1], metric, t][1]
        if base:
            near(pt["percent_change"], 100 * delta[t] / base, "same-hour relative change")
        else:
            check(pt["percent_change"] is None, "zero comparison base")
    ranking = {r["entity"]: r["mean"] for r in cmp["ranking"]}
    for station in STATIONS:
        near(
            ranking[epochs[station]],
            sum(values[station, metric, t][1] for t in delta) / len(delta),
            "paired-only station mean",
        )
    paired = [(t, pos) for t in delta for pos in (0, 1)]
    pages = Counter(
        paired=details(
            reader,
            query,
            cmp["input_version"],
            "paired",
            paired,
            values,
            indexed,
            epochs,
            attributions[0],
        )
    )
    result = dict(
        window_start=start.isoformat(),
        window_end=(start + SPAN).isoformat(),
        indicator=metric,
        unit=unit(metric),
        E_common=cmp["E_common"],
        P=cmp["P"],
        mean_difference=cmp["mean_difference"],
        compare_version=cmp["input_version"],
        stations={},
    )
    for pos, station in enumerate(STATIONS):
        single = intent(product, [targets[pos]], metric, start)
        stats = call(reader, API + "statistics/", method="POST", body=single, uncached=True)
        check_metadata(stats, attributions, single)
        times = [start + timedelta(hours=i) for i in range(48)]
        slots = [values.get((station, metric, t)) for t in times]
        c = stats["columns"][0]
        for col in (c, cmp["columns"][pos]):
            check(col["entity"] == targets[pos], "column station")
            check_coverage(col, slots, metric)
        near(
            stats["whole_period_mean"],
            csv_cols[pos][-1],
            "HTTP whole-window hourly weighted concentration",
        )
        check(
            stats["conclusion"]["state"] == "sufficient" and len(stats["buckets"]) == 2,
            "two source-local daily buckets",
        )
        for day, bucket in enumerate(stats["buckets"]):
            check(
                datetime.fromisoformat(bucket["start"]) == times[24 * day]
                and datetime.fromisoformat(bucket["end"]) == times[24 * day] + timedelta(days=1),
                "source-local daily boundaries",
            )
            near(
                bucket["mean"],
                check_coverage(bucket, slots[24 * day : 24 * (day + 1)], metric),
                "source-local daily concentration",
            )
        for kind in ("observed", "effective"):
            members = [
                (t, 0)
                for t, s in zip(times, slots, strict=True)
                if s and (kind == "observed" or s[0] == "valid")
            ]
            pages[kind] += details(
                reader,
                single,
                stats["input_version"],
                kind,
                members,
                values,
                indexed,
                epochs,
                attributions[0],
            )
        result["stations"][station] = dict(
            E=c["E"],
            O=c["O"],
            V=c["V"],
            missing=c["exclusions"]["missing"],
            weighted_mean=stats["whole_period_mean"],
            statistics_version=stats["input_version"],
        )

    # Preserve independently computed aggregate results (and their numeric precision), never rows.
    def aggregated(columns, pair):
        return {
            "stations": [dict(E=e, O=o, V=v, weighted_mean=str(mean)) for e, o, v, mean in columns],
            "E_common": pair[0],
            "P": pair[1],
            "mean_difference": str(pair[2]),
        }

    return result | {
        "support_pages": dict(pages),
        "csv": aggregated(csv_cols, csv_pair),
        "postgresql": aggregated(pg_cols, pg_pair),
    }


def verify(evidence, raw):
    from django.contrib.auth.models import Permission, User
    from django.db import transaction
    from observations.models import ImportAttempt, SiteEpoch, UseGrant
    from observations.policy import Denied, Scope, applicable, clock, require_use
    from observations.uci_catalog import register_uci

    django_check("migrate", interactive=False)
    operator = User.objects.create_user("v16-operator")
    operator.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="observations", codename__in=["manage_grants", "import_history"]
        )
    )
    password = secrets.token_urlsafe(28)
    reader = User.objects.create_user("v16-researcher", password=password)
    check(
        not reader.get_all_permissions() and not reader.is_staff and not reader.is_superuser,
        "ordinary session account",
    )
    product = register_uci(operator.pk)
    sites = list(SiteEpoch.objects.filter(station__source=product.source).select_related("city"))
    epochs = {s.name: s.pk for s in sites}
    check(
        product.family == product.shape == "H"
        and not product.synthetic
        and product.evidence == DOI
        and all(
            t in product.attribution
            for t in ("Chen, S. (2017)", DOI, "CC BY 4.0", LICENSE, "µg/m³ to mg/m³")
        ),
        "product and attribution",
    )
    check(
        set(epochs) == set(STATIONS)
        and len({s.city_id for s in sites}) == 1
        and all(
            s.city.name == "Beijing"
            and s.location_evidence == DOI
            and s.certainty == "dataset_reporting_identity_only"
            for s in sites
        ),
        "same-city dataset reporting identities",
    )
    first = intent(product.pk, [epochs[s] for s in STATIONS], "CO", STARTS[0])
    with restarted_web() as base:
        http = Probe(base).login(reader.username, password)
        call(
            http,
            API + "compare/",
            method="POST",
            body=first,
            csrf=False,
            status=403,
            code="csrf_failed",
        )
        call(http, API + "compare/", method="POST", body=first, status=403, code="use_denied")
        research = register_research(operator, product)
        attributions = (product.attribution, research)
        grants = list(
            UseGrant.objects.filter(product=product)
            .order_by("pk")
            .values(
                "id",
                "operation",
                "audience",
                "geography",
                "object_code",
                "indicator",
                "data_from",
                "data_until",
                "fields",
                "terms",
                "terms_version",
                "attribution",
                "valid_from",
                "valid_until",
                "expiry_mode",
                "confirmed_by__username",
                "effect",
            )
        )
        new = [g for g in grants if g["terms_version"] == RESEARCH]
        check(
            len(new) == len(STATIONS) * len(STARTS) * len(METRICS) * len(FIELDS),
            "explicit scoped research grants persisted",
        )
        check(
            not any(g["operation"] in ("export", "export_aggregate") for g in grants),
            "no CSV uses registered",
        )
        with transaction.atomic():
            scope = Scope("beijing", STATIONS[0], "CO", STARTS[0].date(), (STARTS[0] + SPAN).date())
            for run, code in [
                (
                    lambda: applicable(product, "analyze", scope, {"normalized"}, clock()),
                    "use_denied",
                ),
                (
                    lambda: require_use(reader.pk, product, "analyze", scope, {"normalized"}),
                    "permission_denied",
                ),
            ]:
                try:
                    run()
                except Denied as exc:
                    check(exc.code == code, "old operator permission and grant semantics")
                else:
                    raise AssertionError("local research grant authorized legacy operator path")
        gap = intent(product.pk, [epochs[s] for s in STATIONS], "CO", STARTS[0] + SPAN)
        call(http, API + "compare/", method="POST", body=gap, status=403, code="use_denied")
        values = csv_oracle(raw)
        image = run_import(evidence.parent, operator.pk, raw)
        indexed = sql_rows(product.pk, epochs, values)
        attempt = ImportAttempt.objects.get(product=product)
        counts = sql(
            """SELECT (SELECT count(*) FROM observations_batch WHERE product_id=%s),
               count(DISTINCT (k.site_epoch_id,k.observed_at_utc)),count(DISTINCT k.id),
               count(DISTINCT r.id),count(rc.id),count(*) FILTER (WHERE r.quality='valid'),
               count(*) FILTER (WHERE r.quality='missing') FROM observations_stationobservation k
               LEFT JOIN observations_stationrevision r ON r.key_id=k.id
               LEFT JOIN observations_receipt rc ON rc.station_revision_id=r.id WHERE k.product_id=%s""",
            product.pk,
            product.pk,
        )[0]
        check(
            counts[:5]
            == (1, len({(s, t) for s, _, t in values}), len(values), len(indexed), len(values))
            and counts[5:]
            == tuple(
                sum(q == target for q, _ in values.values()) for target in ("valid", "missing")
            ),
            "whole import cardinality",
        )
        results = [
            window_result(http, product.pk, epochs, start, m, values, indexed, attributions)
            for start in STARTS
            for m in METRICS
        ]
        valid_parent = first | {
            "input_version": next(r["compare_version"] for r in results if r["indicator"] == "CO")
        }
        for kind in ("detail", "aggregate"):
            call(
                http,
                "/api/v1/me/exports/",
                method="POST",
                body={"kind": kind, "groups": [valid_parent]},
                status=403,
                code="use_denied",
            )
        check(sql("SELECT count(*) FROM accounts_exportticket") == [(0,)], "no export ticket")
        stale = first | {"support": "paired", "input_version": "0" * 64}
        call(http, API + "details/", method="POST", body=stale, status=409, code="input_changed")
    (evidence / "use-grants.json").write_text(
        json.dumps(grants, default=str, indent=2, ensure_ascii=False) + "\n"
    )
    return dict(
        result="PASS",
        run=evidence.parent.name,
        archive_sha256=DIGEST,
        archive_bytes=len(raw),
        profile=PROFILE,
        source="uci.501/uci501",
        attribution=attributions,
        importer_image=image,
        grant_counts=dict(base=len(grants) - len(new), research=len(new)),
        grant_from=str(new[0]["valid_from"]),
        grant_until=str(new[0]["valid_until"]),
        attempt=str(attempt.pk),
        dispatch=attempt.dispatch,
        stage=attempt.stage.state,
        import_counts=dict(
            zip(
                ("batches", "station_hours", "keys", "revisions", "receipts", "valid", "missing"),
                counts,
                strict=True,
            )
        ),
        comparison_order=STATIONS,
        windows=results,
    )


def main():
    django.setup()
    evidence = owned_database() / "V16"
    evidence.mkdir(mode=0o700)
    raw = sys.stdin.buffer.read(SIZE + 1)
    result = {"result": "FAIL"}
    try:
        check(
            len(raw) == SIZE and hashlib.sha256(raw).hexdigest() == DIGEST,
            "official inner ZIP before registration",
        )
        result = verify(evidence, raw)
    finally:
        (evidence / "h-reconcile.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        )
    print(f"V16 {result['result']} ({len(result['windows'])} two-station comparisons)", flush=True)


if __name__ == "__main__":
    main()
