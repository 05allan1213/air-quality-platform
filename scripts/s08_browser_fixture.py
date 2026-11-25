"""Project-authored F inputs for the guarded S08 browser host only."""

import secrets
from datetime import UTC, date, datetime, timedelta

from s06_guard import require

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
SNAPSHOT = datetime(2024, 2, 2, tzinfo=UTC)
START, END = SNAPSHOT - 23 * HOUR, SNAPSHOT + HOUR
CATALOG_START, CATALOG_END = SNAPSHOT - 4 * DAY, SNAPSHOT + 2 * DAY
COLLECTION_END = datetime(9999, 1, 1, tzinfo=UTC)
FETCHED_AT = SNAPSHOT + HOUR
EVIDENCE = "S08 authored synthetic fixture v1"
PROFILE = "f-exact-v1"
LIMITS = {
    "min_interval_seconds": 1,
    "max_concurrent": 1,
    "period_seconds": 60,
    "max_requests": 4,
}
ROLE_PERMISSIONS = {
    "reader": (),
    "peer": (),
    "operator": ("observations.manage_collection", "observations.run_collection"),
    "schedule_editor": ("observations.configure_collection_schedule",),
    "reviewer": ("observations.review_observation",),
    "adjudicator": ("observations.review_observation", "accounts.adjudicate_quality"),
    "station_manager": ("observations.review_observation", "accounts.manage_catalog"),
    "diagnostics": ("accounts.diagnose_operations",),
    "ops_reviewer": ("observations.review_observation", "accounts.diagnose_operations"),
    "grant_manager": ("observations.manage_grants",),
    "admin": ("accounts.maintain_city_search_alias",),
    "account_admin": ("accounts.manage_accounts",),
    "schedule_manager": (
        "accounts.manage_accounts",
        "observations.configure_collection_schedule",
    ),
    "disabled": (),
    "downgraded": (),
}


def grant_permissions(user, permissions):
    from django.contrib.auth.models import Permission

    for name in permissions:
        app, codename = name.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=codename)
        )


def seed_accounts(run):
    from django.contrib.auth import get_user_model

    users = get_user_model().objects
    curator = users.create_user(f"{run}-curator")
    grant_permissions(
        curator,
        (
            "observations.manage_grants",
            "observations.import_history",
            "observations.manage_collection",
        ),
    )
    accounts = {}
    passwords = set()
    for role, permissions in ROLE_PERMISSIONS.items():
        password = secrets.token_urlsafe(32)
        require(password not in passwords, "fixture_account_password_collision")
        passwords.add(password)
        user = users.create_user(
            f"{run}-{role}",
            password=password,
            is_staff=role in {"admin", "account_admin"},
            is_active=role != "disabled",
        )
        grant_permissions(user, permissions)
        accounts[role] = {"id": user.pk, "username": user.username, "password": password}
    return curator, accounts


def row(
    product,
    code,
    at,
    indicator,
    value,
    *,
    standard=None,
    token=1,
    window="hourly_label_unspecified",
):
    from s05_support import candidate

    return candidate(
        product,
        code=code,
        at=at,
        value=value,
        indicator=indicator,
        token=token,
        window=window,
        fetched_at=FETCHED_AT if token == 1 else FETCHED_AT + HOUR,
        **(
            {
                "standard_series": standard,
                "standard_basis": "source_stated",
                "standard_evidence": EVIDENCE,
            }
            if standard
            else {}
        ),
    )


def seed_national(curator, products):
    from collection.admission import expected
    from collection.models import Job, WorkWindow
    from observations.normalization import LEVELS, STANDARD_2012, STANDARD_2026
    from observations.policy import REQUIRED
    from s05_support import city, grant, ingest

    product = products["L"]
    cities = []
    rows = []
    definitions = (
        ("101", "North Sample", "north", 31),
        ("202", "Central Sample", "central", 67),
        ("303", "South Sample", "south", 112),
        *(
            (f"{400 + i}", f"Fixture City {i:02}", ("north", "central", "south")[i % 3], 36 + 3 * i)
            for i in range(1, 49)
        ),
    )
    for code, name, geography, aqi in (
        *definitions,
        ("499", "Expected Only Sample", "south", None),
    ):
        entity = city(
            product, code=code, name=name, geography=geography, start=CATALOG_START, end=CATALOG_END
        )
        if aqi is None:
            continue
        cities.append({"id": entity.pk, "name": name, "code": code})
        rows.append(row(product, code, SNAPSHOT, "AQI", aqi, standard=STANDARD_2012))
        rows.extend(row(product, code, SNAPSHOT, indicator, 2) for indicator in LEVELS)
    rows.append(row(product, "101", SNAPSHOT, "AQI", 44, standard=STANDARD_2026))
    rows.append(row(product, "202", SNAPSHOT, "AQI", 80))  # Unknown remains unranked.
    grant(curator, product, "process_local", fields=REQUIRED | {"overview_summary"})
    grant(curator, product, "display", fields=REQUIRED | {"overview_summary", "collection_ledger"})
    grant(curator, product, "retain_normalized", fields=REQUIRED | {"collection_ledger"})
    grant(
        curator,
        product,
        "review",
        fields={"catalog"},
        geography="north",
        object_code="101",
        start=CATALOG_START,
        end=CATALOG_END,
    )
    ingest(curator, product, rows)
    # The expected-only city is registered in the offline roster without an observation.
    roster = Job(
        product=product,
        expected_codes=[*(item[0] for item in definitions), "499"],
        roster_basis=EVIDENCE,
    )
    WorkWindow.objects.create(
        product=product,
        scope_key="national",
        target_start=SNAPSHOT,
        target_end=END,
        profile=PROFILE,
        expected=expected(roster, SNAPSHOT, END),
        roster_basis=roster.roster_basis,
        result="partial",
    )
    return cities


def seed_monitoring(curator, products, cities):
    from observations.models import City, QualityIssue, SiteEpoch
    from observations.normalization import CONCENTRATIONS, STANDARD_2012
    from psycopg.types.range import TimestamptzRange
    from s05_support import ingest, site

    primary, central, south = cities[:3]
    first = City.objects.get(pk=primary["id"])
    hours = [START + i * HOUR for i in range(24)]
    c, t, s, d = (products[shape] for shape in "CTSD")
    # This three-hour NO2 window is the standalone detail/export sample.
    detail_values = {
        (primary["code"], at, "NO2"): value
        for at, value in zip(hours[8:11], (0, "<3", "NA"), strict=True)
    }
    c_rows = [
        reading
        for spec, aqi, slots in (
            (primary, 41, hours),
            (central, 77, [SNAPSHOT]),
            (south, 122, [SNAPSHOT]),
        )
        for i, at in enumerate(slots)
        for reading in (
            row(c, spec["code"], at, "AQI", aqi + i % 5, standard=STANDARD_2012),
            *(
                row(
                    c,
                    spec["code"],
                    at,
                    indicator,
                    detail_values.get((spec["code"], at, indicator), 12 + j + i % 4),
                )
                for j, indicator in enumerate(CONCENTRATIONS)
            ),
        )
    ]
    # A confirmed revision and a separately disputed same-version value.
    ingest(curator, c, c_rows)
    ingest(
        curator,
        c,
        [
            row(c, primary["code"], hours[2], "NO2", 7, token=2),
            row(c, primary["code"], hours[4], "PM10", 19),
        ],
    )
    ingest(
        curator,
        t,
        [
            row(t, primary["code"], at, "AQI", 25 + i, standard=STANDARD_2012)
            for i, at in enumerate(hours)
            if i not in (3, 10, 23)
        ],
    )
    ingest(
        curator,
        d,
        [
            reading
            for offset in (0, 1, 3)
            for at in (SNAPSHOT - offset * DAY,)
            for reading in (
                row(
                    d,
                    primary["code"],
                    at,
                    "AQI",
                    45 + offset,
                    standard=STANDARD_2012,
                    window="local_daily",
                ),
                *(
                    row(d, primary["code"], at, indicator, 12 + j, window="local_daily")
                    for j, indicator in enumerate(CONCENTRATIONS)
                ),
            )
        ],
    )

    move_at = hours[6]
    old = site(
        s, code="S08-01", parent=first, start=CATALOG_START, end=CATALOG_END, name="North Sample 01"
    )
    old.active_period = TimestamptzRange(CATALOG_START, move_at)
    old.save(update_fields=["active_period"])
    current = SiteEpoch.objects.create(
        station=old.station,
        city=first,
        name=old.name,
        certainty=old.certainty,
        location_evidence=old.location_evidence,
        active_period=TimestamptzRange(move_at, CATALOG_END),
    )
    comparison = site(
        s, code="S08-02", parent=first, start=CATALOG_START, end=CATALOG_END, name="North Sample 02"
    )
    paired = site(
        s, code="S08-03", parent=first, start=CATALOG_START, end=CATALOG_END, name="North Sample 03"
    )
    ingest(
        curator,
        s,
        [
            row(s, "S08-01", START - HOUR, "CO", "2"),
            *(
                row(s, "S08-01", at, "CO", 1 + i % 4)
                for i, at in enumerate(hours)
                if i not in (1, 5)
            ),
            *(
                row(s, "S08-01", at, "PM2.5", "<3" if i == 6 else 10 + i % 7)
                for i, at in enumerate(hours)
            ),
            *(row(s, "S08-02", at, "CO", i % 5) for i, at in enumerate(hours[:18])),
            *(row(s, "S08-03", at, "CO", 1 + i % 5) for i, at in enumerate(hours[6:], 6)),
            *(
                row(s, "S08-02", at, "PM2.5", "NA" if i == 7 else 11 + i)
                for i, at in enumerate(hours[:18])
            ),
        ],
    )
    issue = QualityIssue.objects.get(product=c, code="same_version_conflict")
    return {
        "station": current.station_id,
        "site_epoch": current.pk,
        "previous_site_epoch": old.pk,
        "paired_site_epochs": [comparison.pk, paired.pk],
        "quality_issue": issue.pk,
    }


def seed_collection(curator, operator, run):
    from collection.admission import LEDGER
    from collection.models import Run, ScheduleTemplate
    from collection.s08_schedule import register_template
    from collection.services import register_job
    from django.conf import settings
    from django.test import override_settings
    from observations.models import Source
    from s05_support import city, grant, product

    source = Source.objects.create(
        namespace="s03.fixture", publisher=EVIDENCE, collection_limits=LIMITS
    )
    collection = product("C", shape="C", source=source, known=False)
    collection.contract_version = "s03.f.v1"
    collection.save(update_fields=["contract_version"])
    target = city(
        collection, code="101", name="Collection Sample", start=CATALOG_START, end=COLLECTION_END
    )
    grant(
        curator,
        collection,
        "acquire_online",
        fields={"*"},
        end=COLLECTION_END,
        collection_limits=LIMITS,
    )
    grant(curator, collection, "process_local", end=COLLECTION_END)
    for operation in ("retain_normalized", "review"):
        grant(
            curator, collection, operation, fields=LEDGER, data_from=date.min, data_until=date.max
        )
    require(not settings.S03_FIXTURE_HTTP and settings.CNEMC_MODE == "off", "fixture_source_mode")
    # Admission checks the adapter registry on registration. This seed-only setting
    # enables no service and is restored before the host starts Web or Worker.
    with override_settings(S03_FIXTURE_HTTP=True):
        job = register_job(
            curator.pk,
            operator["id"],
            collection.pk,
            target.pk,
            PROFILE,
            3600,
            None,
            ["101"],
            f"{EVIDENCE} / {run}",
        )
        template = register_template(curator.pk, job.pk, [3600, 7200, 10800], 0)
    job.refresh_from_db()
    require(
        not job.enabled
        and job.plan_state == "legacy"
        and job.next_due_at is None
        and job.schedule_template_id is None
        and ScheduleTemplate.objects.filter(
            pk=template.pk, basis_job=job, version=template.version
        ).exists()
        and not Run.objects.filter(job=job).exists()
        and not settings.S03_FIXTURE_HTTP
        and settings.CNEMC_MODE == "off",
        "fixture_job_not_isolated",
    )
    return {
        "product": collection.pk,
        "job": job.pk,
        "schedule_template": template.pk,
        "schedule_template_version": template.version,
        "city": target.pk,
        "target": None,
    }


def seed_favorite_rights(curator, run):
    from accounts.research_exports import export_fields
    from observations.models import Source
    from observations.policy import REQUIRED
    from s05_support import DISPLAY, RETAIN, city, grant, ingest, product

    # A separate product lets this browser case withdraw display without changing
    # the products read by monitoring, query, or the operations handoff case.
    source = Source.objects.create(
        namespace=f"s08.f.favorite.{run.rsplit('-', 1)[-1]}", publisher=EVIDENCE
    )
    fixture = product("favorite-display", shape="C", source=source)
    target = city(
        fixture, "701", start=CATALOG_START, end=CATALOG_END, name="Favorite Rights Sample"
    )
    grant(curator, fixture, "process_local", fields=REQUIRED, start=SNAPSHOT, end=END)
    grant(curator, fixture, "retain_normalized", fields=RETAIN, start=SNAPSHOT, end=END)
    display = grant(curator, fixture, "display", fields=DISPLAY, start=SNAPSHOT, end=END)
    export_grant = grant(
        curator, fixture, "export", fields=export_fields("detail"), start=SNAPSHOT, end=END
    )
    ingest(curator, fixture, [row(fixture, "701", SNAPSHOT, "CO", 8)])
    return {
        "product": fixture.pk,
        "grant": display.pk,
        "export_grant": export_grant.pk,
        "city": target.pk,
        "name": target.name,
        "source": source.namespace,
        "start": SNAPSHOT.isoformat(),
        "end": END.isoformat(),
    }


def seed_research(curator, run):
    from observations.models import Source
    from observations.policy import REQUIRED
    from s05_support import ANALYZE, DISPLAY, RETAIN, authorize, city, grant, ingest, product

    source = Source.objects.create(
        namespace=f"s08.research.f.{run.rsplit('-', 1)[-1]}", publisher=EVIDENCE
    )
    annual_start = datetime(2023, 11, 30, 16, tzinfo=UTC)
    annual_end = annual_start + 366 * DAY
    january = datetime(2023, 12, 31, 16, tzinfo=UTC)
    february = datetime(2024, 1, 31, 16, tzinfo=UTC)
    calendar = product("research-calendar", "C", source=source, timezone="Asia/Shanghai")
    calendar_city = city(
        calendar, "CALENDAR", start=annual_start, end=annual_end, name="F research calendar"
    )
    for operation, fields in (
        ("process_local", REQUIRED),
        ("retain_normalized", RETAIN),
        ("analyze", ANALYZE),
        ("display", DISPLAY - REQUIRED),
    ):
        grant(curator, calendar, operation, fields=fields, start=annual_start, end=annual_end)
    # Keep the first 30 days empty and give January and February unequal sample counts.
    ingest(
        curator,
        calendar,
        [
            row(calendar, "CALENDAR", at, "CO", value)
            for at, value in ((january, 2), (january + HOUR, 4), (february, 20))
        ],
    )

    pearson_start = datetime(2024, 1, 20, tzinfo=UTC)
    pearson_end = pearson_start + 30 * HOUR
    pearson = product("research-pearson", "C", source=source)
    pearson_city = city(
        pearson, "PAIR", start=pearson_start, end=pearson_end, name="F research correlation"
    )
    authorize(curator, pearson, start=pearson_start, end=pearson_end)
    ingest(
        curator,
        pearson,
        [
            row(pearson, "PAIR", pearson_start + i * HOUR, indicator, value)
            for i in range(30)
            for indicator, value in (("CO", i), ("PM2.5", 3 * i + 5), ("NO2", 20))
        ],
    )
    # A sparse 60-day city series exercises the server's range buckets and empty intervals.
    range_start = datetime(2024, 2, 1, tzinfo=UTC)
    range_end = datetime(2024, 4, 1, tzinfo=UTC)
    chart = product("research-range", "C", source=source)
    chart_city = city(chart, "RANGE", start=range_start, end=range_end, name="F research range")
    authorize(curator, chart, start=range_start, end=range_end)
    ingest(
        curator,
        chart,
        [
            row(chart, "RANGE", range_start + i * HOUR, "CO", value)
            for i, value in enumerate((2, 4, 3))
        ],
    )
    return {
        "range": {
            "product": chart.pk,
            "city": chart_city.pk,
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
        },
        "calendar": {
            "product": calendar.pk,
            "city": calendar_city.pk,
            "start": annual_start.isoformat(),
            "end": annual_end.isoformat(),
            "discovery_start": january.isoformat(),
        },
        "pearson": {
            "product": pearson.pk,
            "city": pearson_city.pk,
            "start": pearson_start.isoformat(),
            "end": pearson_end.isoformat(),
        },
    }


def seed_f(run):
    import django
    from django.core.management import call_command

    django.setup()
    from accounts.research_exports import export_fields
    from observations.models import Source
    from observations.policy import REQUIRED
    from s05_support import authorize, grant, product
    from s08_quality_fixture import seed_quality

    call_command("migrate", interactive=False, verbosity=0)
    curator, accounts = seed_accounts(run)
    source = Source.objects.create(namespace=f"s08.f.{run.rsplit('-', 1)[-1]}", publisher=EVIDENCE)
    products = {
        shape: product(
            shape, shape=shape, source=source, timezone="Asia/Shanghai" if shape == "D" else "UTC"
        )
        for shape in "LCSTD"
    }
    for shape, fixture in products.items():
        export = shape in {"C", "S"}
        authorize(
            curator,
            fixture,
            detail_fields=export_fields("detail") if export else (),
            aggregate_fields=export_fields("aggregate") if export else (),
        )
    grant(curator, products["C"], "review", fields=REQUIRED)
    cities = seed_national(curator, products)
    monitoring = seed_monitoring(curator, products, cities)
    collection = seed_collection(curator, accounts["operator"], run)
    quality, operations = seed_quality(curator, accounts, run)
    favorite_rights = seed_favorite_rights(curator, run)
    research = seed_research(curator, run)
    curator.is_active = False
    curator.save(update_fields=["is_active"])
    return {
        "origin": "F",
        "fixture_version": "s08-browser-fixture-v1",
        "scope": "synthetic",
        "source": source.namespace,
        "snapshot_at": SNAPSHOT.isoformat(),
        "start": START.isoformat(),
        "end": END.isoformat(),
        "products": {shape: fixture.pk for shape, fixture in products.items()},
        "cities": cities,
        "city": cities[0]["id"],
        "accounts": accounts,
        "collection": collection,
        "quality": quality,
        "operations": operations,
        "favorite_rights": favorite_rights,
        "research": research,
        **monitoring,
    }
