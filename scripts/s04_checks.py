"""Assertions executed on loopback with disposable PostgreSQL and real Django HTTP."""

import argparse
import csv
import hashlib
import io
import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import psycopg
from s01_runtime import AUTH, OBS, Client, wait_for

BASE = "/api/v1/"
DETAIL = BASE + "observations/query/"
EXACT = BASE + "observations/current/"
ME = BASE + "me/"
FAVORITES = ME + "favorites/"
T0 = datetime(2024, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
PLAN_END = T0 + timedelta(days=30)
WINDOW = "hourly_label_unspecified"
SYNTHETIC = "synthetic"


class Probe(Client):
    def request(self, path, *, method="GET", body=None, csrf=True, headers=None):
        extra = {"Accept": "application/json", **(headers or {})}
        if csrf and self.token:
            extra["X-CSRFToken"] = self.token if csrf is True else csrf
        if body is not None:
            extra["Content-Type"] = "application/json"
        request = Request(
            self.base + path,
            method=method,
            headers=extra,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = self.http.open(request, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            payload = (
                json.loads(content)
                if content and response.headers.get_content_type() == "application/json"
                else None
            )
            return response.status, payload, response.headers


def check(condition, label):
    if not condition:
        raise AssertionError(label)


def call(client, path, *, status=200, code=None, uncached=False, **options):
    actual, payload, headers = client.request(path, **options)
    check(actual == status, f"HTTP status expected {status} got {actual} ({path.split('?')[0]})")
    if code:
        check(payload == {"code": code}, f"bounded error {code} ({path.split('?')[0]})")
    if uncached:
        directives = {s.strip() for s in headers.get("cache-control", "").split(",")}
        check({"private", "no-store"} <= directives, "private no-store")
    return payload


def url(path, **filters):
    return path + "?" + urlencode(filters)


def key_signature(row):
    return row["key"], row["revision"], datetime.fromisoformat(row["observed_at"]), row["unit"]


def sql(statement, *parameters):
    from django.db import connection

    with psycopg.connect(**connection.get_connection_params()) as db, db.cursor() as cur:
        cur.execute(statement, parameters or None)
        return cur.fetchall()


@contextmanager
def web():
    from django.core.servers.basehttp import ThreadedWSGIServer, WSGIRequestHandler
    from django.core.wsgi import get_wsgi_application
    from django.db import connection

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *_):
            pass

    class ThreadedServer(ThreadedWSGIServer):
        # Local DB connections belong to the request thread, never the verifier.
        def get_request(self):
            conn, address = super().get_request()
            conn.settimeout(20)
            return conn, address

    with ThreadedServer(("127.0.0.1", 0), QuietHandler) as server:
        app = get_wsgi_application()
        queries = []

        def instrumented(environ, responder):
            def capture(execute, statement, params, many, context):
                queries.append((statement, params))
                return execute(statement, params, many, context)

            with connection.execute_wrapper(capture):
                return app(environ, responder)

        server.set_app(instrumented)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", queries
        finally:
            server.shutdown()
            thread.join(timeout=10)


def django_check(*args, **options):
    from django.core.management import call_command

    call_command(*args, stdout=io.StringIO(), stderr=io.StringIO(), **options)


def old_leaf():
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    # Install standard Django permissions via post_migrate and then remove only
    # the empty task-owned Favorite table before constructing the upgrade facts.
    loader = MigrationLoader(connection)
    if loader.graph.leaf_nodes("accounts"):
        django_check("migrate", "accounts", "zero", interactive=False)
    check(sql("SELECT to_regclass('accounts_favorite')")[0][0] is None, "old accounts schema")
    django_check("migrate", "observations", "0010", check_unapplied=True)
    django_check("migrate", "collection", "0008", check_unapplied=True)


def seed(password):
    from collection.models import Job, Run, WorkWindow
    from django.contrib.auth.models import Permission, User
    from django.utils import timezone
    from observations.models import City, CityIdentifier, Source, StationIdentifier
    from observations.normalization import CONCENTRATIONS, STANDARD_2012
    from psycopg.types.range import TimestamptzRange
    from s02_support import allow, candidate, create_product, create_site, ingest_fixture
    from samples.models import FixtureGrant
    from samples.normalization import SOURCE, load_fixture, normalize
    from samples.services import store_observation

    operator = User.objects.create_user("s04-operator", password=password, is_staff=True)
    operator.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="observations",
            codename__in=("import_history", "manage_grants", "review_observation"),
        )
    )
    reader = User.objects.create_user("s04-old-reader", password=password)
    admin = User.objects.create_superuser("s04-admin", password=password)
    source = Source.objects.create(namespace="test.s04.integration", publisher="S04 integration")
    products = {shape: create_product(shape, shape=shape, source=source) for shape in "LCST"}
    attrs = "S04 synthetic fixture"
    period = TimestamptzRange(T0, T0 + 48 * HOUR)
    epoch = create_site(products["S"], code="S4-001", geography="north")
    epoch.active_period = TimestamptzRange(T0, PLAN_END)
    epoch.save(update_fields=["active_period"])
    StationIdentifier.objects.filter(station_id=epoch.station_id).update(
        active_period=epoch.active_period
    )
    main = epoch.city
    other = City.objects.create(source=source, name="Test Annex", geography="south")
    for city, code in ((main, "C4-001"), (other, "C4-002")):
        CityIdentifier.objects.create(
            source=source, city=city, code=code, active_period=period, evidence="F"
        )
    fields = [
        "normalized",
        "provenance",
        "catalog",
        "raw_field",
        "collection_ledger",
        "source_time",
    ]
    display_grants = {}
    for shape, product in products.items():
        product.attribution = attrs
        product.save(update_fields=["attribution"])
        rights = {"data_until": PLAN_END.date()} if shape == "S" else {}
        for operation in ("retain_normalized", "process_local", "review", "display"):
            grant = allow(operator, product, operation, fields=fields, attribution=attrs, **rights)
            if operation == "display":
                display_grants[shape] = grant.pk
    for op in ("process_local", "display"):
        allow(
            operator,
            products["L"],
            op,
            fields=["normalized", "provenance", "overview_summary"],
            geography="north",
            attribution=attrs,
        )

    def aqi(product, value, code="C4-001", at=T0):
        return candidate(
            product,
            token=1,
            value=value,
            at=at,
            code=code,
            indicator="AQI",
            unit="1",
            standard_series=STANDARD_2012,
            standard_basis="source_stated",
            standard_evidence="F",
        )

    def pollution(product, indicator, value, at, code):
        return candidate(
            product,
            token=1,
            value=value,
            at=at,
            code=code,
            indicator=indicator,
            unit="mg/m³" if indicator == "CO" else "µg/m³",
        )

    ingest_fixture(
        operator.pk,
        products["L"].pk,
        "s04-L",
        "ignored",
        [
            aqi(products["L"], "40"),
            aqi(products["L"], "40", "C4-002"),
        ],
    )
    ingest_fixture(
        operator.pk,
        products["C"].pk,
        "s04-C",
        "ignored",
        [
            aqi(products["C"], "70"),
            *[
                pollution(products["C"], indicator, "3", T0, "C4-001")
                for indicator in CONCENTRATIONS
            ],
        ],
    )
    ingest_fixture(operator.pk, products["T"].pk, "s04-T", "ignored", [aqi(products["T"], "90")])
    ingest_fixture(
        operator.pk,
        products["S"].pk,
        "s04-S",
        "ignored",
        [
            pollution(products["S"], "CO", "0", T0, "S4-001"),
            pollution(products["S"], "CO", "NA", T0 + HOUR, "S4-001"),
            pollution(products["S"], "PM2.5", "17", T0 + HOUR, "S4-001"),
            pollution(products["S"], "CO", "2", T0 + 2 * HOUR, "S4-001"),
        ],
    )
    FixtureGrant.objects.get_or_create(
        source=SOURCE,
        product="F",
        geography="synthetic",
        audience="local_authenticated",
        operation="display",
        defaults={"valid_from": T0, "basis": "S04 migration test"},
    )
    store_observation(normalize(load_fixture()), timezone.now())
    job = Job.objects.create(
        product=products["C"],
        city=main,
        executor=operator,
        enabled=False,
        profile="f-exact-v1",
        expected_codes=["C4-001"],
    )
    window = WorkWindow.objects.create(
        product=products["C"],
        target_start=T0,
        target_end=T0 + HOUR,
        scope_key="s04-F",
        profile=job.profile,
        result="responded",
    )
    Run.objects.create(
        job=job,
        window=window,
        actor=operator,
        executor=operator,
        reason="manual",
        status="finished",
        dispatch="sent",
        result="responded",
    )
    return {
        "operator": operator.pk,
        "reader": reader.pk,
        "admin": admin.pk,
        "products": {k: p.pk for k, p in products.items()},
        "display_grants": display_grants,
        "city": main.pk,
        "other_city": other.pk,
        "epoch": epoch.pk,
        "station": epoch.station_id,
        "window": window.pk,
    }


def detail(product, epoch, *, start=T0, end=T0 + 3 * HOUR, scope=SYNTHETIC, **extra):
    filters = dict(
        product=product,
        site_epochs=epoch,
        report_kind="hourly",
        window_definition=WINDOW,
        indicators="CO",
        standard_series="not_applicable",
        mode="window",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    return url(DETAIL, **(filters | ({"scope": scope} if scope else {}) | extra))


def exact(product, epoch):
    return url(
        EXACT,
        product=product,
        site_epoch=epoch,
        scope=SYNTHETIC,
        indicator="CO",
        report_kind="hourly",
        window_definition=WINDOW,
        standard_series="not_applicable",
        observed_at=T0.isoformat(),
    )


def station_sql(product, epoch, start, end):
    return sql(
        """
        SELECT k.id, r.id, k.observed_at_utc, r.unit, r.value
          FROM observations_stationobservation k
          JOIN observations_stationrevision r ON r.key_id = k.id
         WHERE k.product_id = %s AND k.site_epoch_id = %s AND k.indicator = 'CO'
           AND k.observed_at_utc >= %s AND k.observed_at_utc < %s
           AND r.is_current AND NOT r.candidate AND r.quality = 'valid'
           AND r.value IS NOT NULL AND NOT k.disputed AND NOT k.retired
         ORDER BY k.observed_at_utc DESC, k.id DESC
    """,
        product,
        epoch,
        start,
        end,
    )


def snapshot():
    return tuple(
        sql(
            f"SELECT count(*), md5(COALESCE(string_agg(x::text, ',' ORDER BY x::text), '')) FROM {name} x"
        )
        for name in (
            "auth_user",
            "django_session",
            "samples_fixtureobservation",
            "collection_workwindow",
            "collection_run",
            "observations_stationobservation",
            "observations_stationrevision",
            "observations_cityobservation",
            "observations_cityrevision",
            "observations_receipt",
            "observations_usegrant",
        )
    )


def check_exact_upgrade(before, after):
    """The public value, revision and attributions survive the pre-S07 wire-format change."""
    old_rows = before["results"]
    new_rows = after["results"]
    check(len(old_rows) == len(new_rows) == 1, "one authorized exact row after upgrade")
    old, new = old_rows[0], new_rows[0]
    for row in (old, new):
        check(
            all(
                k in row
                for k in ("product", "revision_id", "observed_at", "unit", "value", "attribution")
            ),
            "exact business fields present",
        )
    check(
        {k: v for k, v in before.items() if k != "results"}
        == {k: v for k, v in after.items() if k != "results"},
        "exact envelope and attributions survived",
    )
    check(
        {k: v for k, v in old.items() if k != "input_version"}
        == {k: v for k, v in new.items() if k != "input_version"},
        "exact public revision survived",
    )
    mark = new["input_version"]
    check(
        isinstance(mark, str) and len(mark) == 64 and all(c in "0123456789abcdef" for c in mark),
        "opaque exact token",
    )
    if isinstance(old["input_version"], str):
        check(old["input_version"] == mark, "same-format exact token stable")


def migration(base, ids, password, phase):
    reader = Probe(base).login("s04-old-reader", password)
    operator = Probe(base).login("s04-operator", password)
    saved = snapshot()
    window = BASE + f"admin/collection-windows/{ids['window']}/"
    old_s01 = call(reader, OBS)
    old_s02 = call(reader, exact(ids["products"]["S"], ids["epoch"]))
    old_s03 = call(operator, window)
    check(old_s01["count"] == old_s02["count"] == 1, "baseline readers see seeded values")
    check(old_s03["runs"][0]["result"] == "responded", "baseline management window response")
    if phase == "baseline":
        return reader, operator
    django_check("migrate", interactive=False)
    check(
        sql("SELECT to_regclass('accounts_favorite')")[0][0] is not None,
        "Favorite migration applied",
    )
    check(snapshot() == saved, "existing users, sessions, grants and observations unchanged")
    post_s01 = call(reader, OBS)
    check(
        post_s01["results"][0]["value"] == old_s01["results"][0]["value"],
        "old S01 cookie reads same value",
    )
    check_exact_upgrade(old_s02, call(reader, exact(ids["products"]["S"], ids["epoch"])))
    check(call(operator, window) == old_s03, "old S03 cookie reads same ledger")
    django_check("migrate", check_unapplied=True, interactive=False)
    django_check("makemigrations", check=True, dry_run=True, interactive=False)
    django_check("check")
    return reader, operator


def account_http(base, ids, password):
    reader = Probe(base)
    creds = {"username": "s04-fresh", "password": password}
    call(reader, ME, status=403, code="not_authenticated", uncached=True)
    seen = sql("SELECT count(*) FROM auth_user")
    reader.csrf()
    for csrf in (False, "incorrect"):
        call(
            reader,
            AUTH + "register/",
            method="POST",
            body=creds,
            csrf=csrf,
            status=403,
            code="csrf_failed",
        )
        call(
            reader,
            AUTH + "login/",
            method="POST",
            body=creds,
            csrf=csrf,
            status=403,
            code="csrf_failed",
        )
    call(
        reader,
        AUTH + "register/",
        method="POST",
        body=creds | {"is_superuser": True},
        status=400,
        code="invalid_request",
    )
    check(sql("SELECT count(*) FROM auth_user") == seen, "rejected register leaves users unchanged")
    call(reader, AUTH + "register/", method="POST", body=creds, status=201)
    check(
        sql("""SELECT u.is_staff, u.is_superuser, count(p.permission_id)
                  FROM auth_user u LEFT JOIN auth_user_user_permissions p ON p.user_id = u.id
                 WHERE username = 's04-fresh' GROUP BY u.id""")
        == [(False, False, 0)],
        "registration grants USER only",
    )
    old_csrf = next(c.value for c in reader.cookies if c.name == "csrftoken")
    reader.login(creds["username"], password)
    check(
        next(c.value for c in reader.cookies if c.name == "csrftoken") != old_csrf,
        "login rotates CSRF secret",
    )
    call(reader, ME, method="PATCH", body={"first_name": "S04"}, uncached=True)
    check(
        call(reader, ME, uncached=True)["first_name"] == "S04",
        "same-origin authorized profile write",
    )
    return reader


def detail_http(reader, ids):
    product, epoch = ids["products"]["S"], ids["epoch"]
    empty = call(reader, detail(product, epoch, start=T0 + HOUR, end=T0 + 2 * HOUR), uncached=True)
    check(
        empty["state"] == "no_data" and empty["results"] == [], "valid history does not fill gaps"
    )
    exact_response = call(reader, exact(product, epoch))
    exact_row = exact_response["results"][0]
    single = call(reader, detail(product, epoch, end=T0 + HOUR), uncached=True)["results"]
    check(
        len(single) == 1
        and single[0]["revision"] == exact_row["revision_id"]
        and all(
            single[0][f] == exact_row[f]
            for f in ("product", "site_epoch", "indicator", "observed_at")
        )
        and key_signature(single[0]) == station_sql(product, epoch, T0, T0 + HOUR)[0][:4],
        "normalized key and current revision agree with exact API",
    )
    equivalent = detail(product, epoch, end=T0 + HOUR).replace(
        "2024-01-01T00%3A00%3A00%2B00%3A00", "2024-01-01T08%3A00%3A00%2B08%3A00"
    )
    adjusted = call(reader, equivalent)["results"]
    check(
        [key_signature(row) for row in adjusted] == [key_signature(row) for row in single],
        "equivalent offsets select identical facts",
    )
    call(
        reader, detail(product, epoch).replace("%2B00%3A00", ""), status=400, code="invalid_request"
    )
    call(reader, detail(product, epoch) + "&product=1", status=400, code="invalid_request")
    call(reader, detail(product, epoch, scope=None), status=403, code="use_denied", uncached=True)
    rows = []
    pagination = {}
    for _ in range(3):
        page = call(reader, detail(product, epoch, page_size=1, **pagination))
        rows.extend(page["results"])
        cursor = page["page"]["next_cursor"]
        if not cursor:
            break
        pagination = {"cursor": cursor}
    check(
        [key_signature(row) + (Decimal(row["value"]),) for row in rows]
        == station_sql(product, epoch, T0, T0 + 3 * HOUR),
        "paginated rows match independent ordered SQL",
    )
    return len(rows)


def replay(client, base):
    secret = next(cookie.value for cookie in client.cookies if cookie.name == "sessionid")
    return Probe(base), {"Cookie": "sessionid=" + secret}


def private_http(base, reader, ids, password):
    signin = AUTH + "login/"
    username = call(reader, ME)["username"]
    second = Probe(base).login(username, password)
    peer = Probe(base).login("s04-old-reader", password)
    operator = Probe(base).login("s04-operator", password)
    credentials = {"old_password": password, "new_password": secrets.token_urlsafe(32)}
    invalid = {"old_password": "incorrect", "new_password": credentials["new_password"]}
    pwd = AUTH + "password/"
    call(reader, ME + str(ids["reader"]) + "/", status=404)
    call(
        reader,
        ME,
        method="PATCH",
        body={"owner": ids["reader"]},
        status=400,
        code="invalid_request",
    )
    call(
        reader,
        ME,
        method="PATCH",
        body={"first_name": "W"},
        csrf=False,
        status=403,
        code="csrf_failed",
    )
    call(reader, pwd, method="POST", body=credentials, csrf=False, status=403, code="csrf_failed")
    call(reader, pwd, method="POST", body=invalid, status=403, code="invalid_credentials")
    call(
        reader,
        pwd,
        method="POST",
        body=credentials | {"new_password": "12345678"},
        status=400,
        code="invalid_request",
    )
    check(call(peer, ME)["first_name"] == "", "profile belongs only to authenticated user")
    for saved, headers in [replay(reader, base), replay(second, base)]:
        call(saved, ME, headers=headers)
    replayed = [replay(session, base) for session in (reader, second)]
    call(reader, pwd, method="POST", body=credentials, status=204)
    for saved, headers in replayed:
        call(saved, ME, status=403, code="not_authenticated", headers=headers)
    reader.csrf()
    call(
        reader,
        signin,
        method="POST",
        body={"username": username, "password": password},
        status=403,
        code="invalid_credentials",
    )
    reader.login(username, credentials["new_password"])
    second.login(username, credentials["new_password"])
    saved, headers = replay(second, base)
    call(second, AUTH + "logout/", method="POST", status=204)
    call(saved, ME, status=403, code="not_authenticated", headers=headers)
    call(reader, ME)
    sql("UPDATE auth_user SET is_active=false WHERE id = %s RETURNING id", ids["reader"])
    call(peer, ME, status=403, code="not_authenticated")
    path = BASE + f"admin/collection-windows/{ids['window']}/"
    call(operator, path)
    sql(
        """DELETE FROM auth_user_user_permissions p USING auth_permission a
           WHERE p.user_id=%s AND p.permission_id=a.id AND a.codename='review_observation'
           RETURNING p.permission_id""",
        ids["operator"],
    )
    call(operator, path, status=403, code="permission_denied")
    call(operator, ME)
    return credentials["new_password"]


def co_fingerprint(rows):
    normal = sorted(
        (stamp.astimezone(UTC).isoformat(), str(value.quantize(Decimal("0.000001"))))
        for stamp, value in rows
    )
    return hashlib.sha256(json.dumps(normal, separators=(",", ":")).encode()).hexdigest()


def h_import(stream):
    """Run in a short-lived container with stdin as its only UCI archive input."""
    from django.contrib.auth import get_user_model
    from observations.history import check_import, consume, stage_uci
    from observations.import_uci import ARCHIVE_BYTES, SHA256, member_name, validate_archive
    from observations.models import ImportAttempt, SiteEpoch, StagedInput
    from observations.policy import REQUIRED, Scope, applicable, clock
    from observations.uci_catalog import register_uci

    django_check("migrate", interactive=False)
    operator = get_user_model().objects.create(username="s04-h-importer", is_superuser=True)
    operator.set_unusable_password()
    operator.save(update_fields=["password"])
    product = register_uci(operator.pk)
    check_import(product, operator.pk)
    epochs = {
        epoch.name: epoch for epoch in SiteEpoch.objects.filter(station__source=product.source)
    }
    check(set(epochs) == {"Aotizhongxin", "Changping"}, "two independently identified sites")
    archive = {"product": product.pk, "sha256": SHA256, "sites": []}
    zone = ZoneInfo("Asia/Shanghai")
    days = (datetime(2013, 3, 1, tzinfo=zone), datetime(2017, 2, 27, tzinfo=zone))
    now = clock()
    for station in epochs:
        for day in days:
            end = day + 48 * HOUR
            basis = Scope("beijing", station, "CO", day.date(), end.date())
            applicable(product, "retain_normalized", basis, REQUIRED, now)
            applicable(product, "display", basis, REQUIRED, now)
    raw = stream.read(ARCHIVE_BYTES + 1)
    check(
        len(raw) == ARCHIVE_BYTES and hashlib.sha256(raw).hexdigest() == SHA256,
        "frozen UCI archive size and SHA-256",
    )
    check(validate_archive(io.BytesIO(raw)) == SHA256, "immutable archive accepted by S02")
    with ZipFile(io.BytesIO(raw)) as source:
        for station, epoch in sorted(epochs.items()):
            fixture = {"epoch": epoch.pk, "station": epoch.station_id, "windows": []}
            with source.open(member_name(station)) as member:
                samples = {}
                for row in csv.DictReader(io.TextIOWrapper(member, encoding="utf-8", newline="")):
                    stamp = datetime(
                        *(int(row[k]) for k in ("year", "month", "day", "hour")), tzinfo=zone
                    )
                    if row["CO"] != "NA" and any(day <= stamp < day + 48 * HOUR for day in days):
                        samples[stamp] = Decimal(row["CO"]) / 1000
            for day in days:
                end = day + 48 * HOUR
                window = [(stamp, co) for stamp, co in samples.items() if day <= stamp < end]
                fixture["windows"].append(
                    {
                        "start": day.isoformat(),
                        "end": end.isoformat(),
                        "count": len(window),
                        "digest": co_fingerprint(window),
                    }
                )
            archive["sites"].append(fixture)
    stage = stage_uci(operator.pk, io.BytesIO(raw))
    StagedInput.objects.filter(pk=stage).update(state="claimed")
    attempt = ImportAttempt.objects.create(
        product=product, stage_id=stage, actor=operator, reason="historical_verification"
    )
    check(consume(attempt.pk) == "succeeded", "S02 staged importer completed")
    check(not list(Path("/run/s02-history").iterdir()), "private staged ZIP removed")
    return archive


def h_http(reader, archive):
    from observations.import_uci import ATTRIBUTION

    product = archive["product"]
    no_data = datetime(2015, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    checked = 0
    for site in archive["sites"]:
        for window in site["windows"]:
            start, end = (datetime.fromisoformat(window[bound]) for bound in ("start", "end"))
            stored = station_sql(product, site["epoch"], start, end)
            response = call(
                reader,
                detail(product, site["epoch"], start=start, end=end, scope=None),
                uncached=True,
            )
            projected = response["results"]
            check(response["synthetic"] is False, "H retains its real provenance")
            check(
                len(stored) == len(projected) == window["count"] > 0,
                "all authoritative CO positions match",
            )
            check(
                [key_signature(row) + (Decimal(row["value"]),) for row in projected] == stored,
                "H history key/versions/units/instants/values agree with PG",
            )
            check(
                co_fingerprint((item[2], item[4]) for item in stored) == window["digest"],
                "H CO readings agree with independently converted CSV values",
            )
            check(all(item[3] == "mg/m³" for item in stored), "H CO normalized unit")
            check(
                all(row["attribution"] == ATTRIBUTION for row in projected),
                "H history attribution and terms",
            )
            latest = call(
                reader,
                url(
                    BASE + f"stations/{site['station']}/",
                    product=product,
                    site_epoch=site["epoch"],
                    indicators="CO",
                    as_of=(end - HOUR).isoformat(),
                ),
                uncached=True,
            )
            check(len(latest["results"]) == 1, "H latest has a CO value")
            recent = latest["results"][0]
            check(
                key_signature(recent) + (Decimal(recent["value"]),) == stored[0],
                "H latest key/revision/value matches latest permitted CSV position",
            )
            check(recent["attribution"] == ATTRIBUTION, "H latest attribution and terms")
            identity = latest["identity"]
            for field in (
                "station_name",
                "site_name",
                "city_name",
                "period_start",
                "period_end",
                "codes",
            ):
                check(not identity[field], "known H epoch does not grant directory disclosure")
            checked += len(stored)
        call(
            reader,
            detail(product, site["epoch"], start=no_data, end=no_data + 24 * HOUR, scope=None),
            status=403,
            code="use_denied",
            uncached=True,
        )
    return {
        "archive_sha256": archive["sha256"],
        "verified_sites": len(archive["sites"]),
        "windows_per_site": 2,
        "verified_co_rows": checked,
        "negative_display_gap_status": 403,
        "worker_dispatch": "NOT RUN (actual S02 consume service ran in isolated importer)",
    }


def mixed_domain(reader, ids, archive):
    product = archive["product"]
    site = archive["sites"][0]
    formal = call(
        reader,
        FAVORITES,
        method="POST",
        status=201,
        body={"kind": "station", "target_id": site["station"], "product": product},
    )
    synthetic = call(reader, url(FAVORITES, scope=SYNTHETIC))
    check(
        len(synthetic["results"]) == 1 and synthetic["results"][0]["kind"] == "city",
        "explicit F domain remains isolated while formal H favorite exists",
    )
    default = call(reader, FAVORITES)
    check([item["id"] for item in default["results"]] == [formal["id"]], "default domain only H")
    call(reader, url(FAVORITES + f"{formal['id']}/", scope=SYNTHETIC), status=404, code="not_found")
    from observations.models import UseGrant
    from observations.policy import revoke_grant

    # Withdraw the platform-local display grants while leaving source retention intact.
    for grant in UseGrant.objects.filter(product=product, operation="display").values_list(
        "pk", flat=True
    ):
        revoke_grant(ids["operator"], grant, "isolated display withdrawal verification")
    placeholder = {"id": formal["id"], "kind": "station", "state": "unavailable"}
    check(
        call(reader, FAVORITES + f"{formal['id']}/") == placeholder,
        "H holder sees sparse placeholder",
    )
    check(call(reader, FAVORITES)["results"] == [placeholder], "formal domain still owns H intent")
    call(reader, FAVORITES + f"{formal['id']}/", method="DELETE", status=204)
    return {"both_domains": True, "display_revocation_keeps_intent": True}


def linked(value, route):
    """Locate an advertised relative API navigation path for this route."""
    if isinstance(value, str):
        target = urlsplit(value)
        if not target.scheme and not target.netloc and target.path == route:
            return value
    if isinstance(value, (dict, list)):
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            if target := linked(child, route):
                return target
    return None


def observation_rows(value, indicator=None):
    if isinstance(value, (dict, list)):
        if isinstance(value, dict) and {"key", "revision", "indicator"} <= value.keys():
            if indicator is None or value["indicator"] == indicator:
                yield value
        else:
            children = value.values() if isinstance(value, dict) else value
            for child in children:
                yield from observation_rows(child, indicator)


def card_values(card, product, indicator):
    return {
        Decimal(row["value"])
        for row in observation_rows(card, indicator)
        if row["product"] == product and row["value"] is not None
    }


def journey(reader, base, ids, password, peer_password):
    products = ids["products"]
    saved_runs = sql("SELECT count(*) FROM collection_run")
    snapshot = call(
        reader,
        url(BASE + "overview/national/", product=products["L"], scope=SYNTHETIC, at=T0.isoformat()),
        uncached=True,
    )
    check(datetime.fromisoformat(snapshot["snapshot_at"]) == T0, "fixed nationwide timestamp")
    check(card_values(snapshot, products["L"], "AQI") == {Decimal(40)}, "nationwide uses L AQI")
    check(len(snapshot["results"]) == 2, "both display-allowed cities listed")
    summary = snapshot["summary"]
    check(
        summary["covered_city_count"] is None and summary["summary_eligible_city_count"] == 1,
        "partial summary discloses only eligible input",
    )
    city_route = linked(snapshot, BASE + f"cities/{ids['city']}/")
    check(city_route, "national response advertises authorized C navigation")
    city = call(reader, city_route, uncached=True)
    city_rows = list(observation_rows(city))
    check(
        len(city_rows) == 7 and {row["product"] for row in city_rows} == {products["C"]},
        "seven C values from one product",
    )
    check(card_values(city, products["C"], "AQI") == {Decimal(70)}, "city uses C AQI")
    trend_route = linked(city, BASE + f"cities/{ids['city']}/trend/")
    check(trend_route, "city advertises compatible T navigation")
    trend = call(reader, trend_route, uncached=True)
    check(card_values(trend, products["T"], "AQI") == {Decimal(90)}, "trend uses T AQI")
    directory_route = linked(city, BASE + f"cities/{ids['city']}/stations/")
    check(directory_route, "city advertises authorized S directory")
    directory = call(reader, directory_route, uncached=True)
    station_route = linked(directory, BASE + f"stations/{ids['station']}/")
    check(station_route, "directory advertises authorized station detail")
    station = call(reader, station_route, uncached=True)
    check(station["site_epoch"] == ids["epoch"], "site ID propagated through navigation")
    historical_route = linked(station, DETAIL)
    check(historical_route, "station advertises explicit history")
    historical = call(reader, historical_route, uncached=True)
    check(bool(historical["results"]), "history linked to an authorized window")
    recent = list(observation_rows(station, "CO"))
    check(len(recent) == 1, "station has a CO series")
    # The drill-down preserves the chosen timestamp. Compare its CO exactly with committed PG.
    stamped = datetime.fromisoformat(recent[0]["observed_at"])
    stored = station_sql(products["S"], ids["epoch"], stamped, stamped + HOUR)
    check(
        [key_signature(recent[0]) + (Decimal(recent[0]["value"]),)] == stored,
        "station card independently agrees with committed SQL",
    )
    check(
        key_signature(recent[0]) in {key_signature(row) for row in historical["results"]},
        "latest row matches its historical detail",
    )
    payload = {
        "kind": "city",
        "target_id": ids["city"],
        "product": products["C"],
        "scope": SYNTHETIC,
    }
    peer = Probe(base).login("s04-admin", peer_password)
    duplicate_session = Probe(base).login("s04-fresh", password)
    pre = sql("SELECT count(*) FROM accounts_favorite")
    call(reader, FAVORITES, method="POST", body=payload, csrf=False, status=403, code="csrf_failed")
    check(
        sql("SELECT count(*) FROM accounts_favorite") == pre,
        "missing CSRF leaves favorites unchanged",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda client: client.request(FAVORITES, method="POST", body=payload),
                (reader, duplicate_session),
            )
        )
    check(
        sorted(status for status, _, _ in responses) == [200, 201],
        "concurrent favorite first/retry status",
    )
    saved_id = responses[0][1]["id"]
    check(responses[1][1]["id"] == saved_id, "duplicate requests reference one intent")
    check(
        sql("SELECT count(*) FROM accounts_favorite WHERE city_id=%s", ids["city"]) == [(1,)],
        "committed favorite uniqueness",
    )
    item = url(FAVORITES + f"{saved_id}/", scope=SYNTHETIC)
    for method, body in (("GET", None), ("PATCH", {"product": products["T"]}), ("DELETE", None)):
        call(peer, item, method=method, body=body, status=404, code="not_found", uncached=True)
    check(call(reader, FAVORITES)["results"] == [], "F intent hidden from default domain")
    saved, headers = replay(reader, base)
    call(reader, AUTH + "logout/", method="POST", status=204)
    call(saved, item, headers=headers, status=403, code="not_authenticated")
    reader.login("s04-fresh", password)
    favorite = call(reader, item, uncached=True)
    check(
        {key_signature(row) for row in observation_rows(favorite)}
        == {key_signature(row) for row in city_rows},
        "favorite and city use the same key/revision/instant",
    )
    check(
        sql("SELECT count(*) FROM collection_run") == saved_runs, "read chain starts no collection"
    )
    return {
        "L_cities": 2,
        "C_values": 7,
        "T_aqi_points": 1,
        "favorite_rows": 1,
        "independent_station_db_match": True,
        "collection_run_delta": 0,
    }


def expand_plan_fixture(ids):
    """Populate only the task-owned F series using the standard S02 writer."""
    from itertools import batched

    from observations.models import City, CityIdentifier, Product
    from observations.normalization import STANDARD_2012
    from psycopg.types.range import TimestamptzRange
    from s02_support import candidate, ingest_fixture

    nationwide, station = (
        Product.objects.get(pk=ids["products"][shape], family="F", synthetic=True)
        for shape in ("L", "S")
    )
    cities = City.objects.bulk_create(
        [
            City(source_id=nationwide.source_id, name=f"Plan {i:03d}", geography="north")
            for i in range(318)
        ]
    )
    codes = [f"PLAN-{i:03d}" for i in range(len(cities))]
    CityIdentifier.objects.bulk_create(
        [
            CityIdentifier(
                source_id=nationwide.source_id,
                city=city,
                code=code,
                active_period=TimestamptzRange(T0, T0 + 48 * HOUR),
                evidence="S04 F query-plan fixture",
            )
            for city, code in zip(cities, codes, strict=True)
        ]
    )
    ingest_fixture(
        ids["operator"],
        nationwide.pk,
        "s04-plan-L",
        "S04 F query-plan fixture",
        [
            candidate(
                nationwide,
                at=T0 + hour * HOUR,
                code=code,
                indicator="AQI",
                value=str(25 + i % 100),
                unit="1",
                standard_series=STANDARD_2012,
                standard_basis="source_stated",
                standard_evidence="F",
            )
            for hour in (0, 1, 2)
            for i, code in enumerate(codes)
        ],
    )
    candidates = (
        candidate(
            station, at=T0 + hour * HOUR, code="S4-001", indicator=indicator, value=value, unit=unit
        )
        for hour in range(3, (PLAN_END - T0) // HOUR)
        for indicator, value, unit in (("CO", "2", "mg/m³"), ("PM2.5", "18", "µg/m³"))
    )
    for index, batch in enumerate(batched(candidates, 512, strict=False)):
        ingest_fixture(
            ids["operator"], station.pk, f"s04-plan-S-{index}", "S04 F query-plan fixture", batch
        )


def execution_plan(node):
    return {
        "node": node["Node Type"],
        "rows": node["Actual Rows"],
        "loops": node["Actual Loops"],
        **({"index": node["Index Name"]} if "Index Name" in node else {}),
        **({"children": [execution_plan(p) for p in node["Plans"]]} if node.get("Plans") else {}),
    }


def plans(reader, ids, queries):
    from time import perf_counter

    from django.db import connection
    from observations.normalization import STANDARD_2012

    products = ids["products"]
    routes = {
        "history": detail(products["S"], ids["epoch"], end=PLAN_END, page_size=50),
        "station_latest": url(
            BASE + f"stations/{ids['station']}/",
            product=products["S"],
            scope=SYNTHETIC,
            site_epoch=ids["epoch"],
            as_of=(PLAN_END - HOUR).isoformat(),
        ),
        "national": url(
            BASE + "overview/national/",
            product=products["L"],
            scope=SYNTHETIC,
            at=T0.isoformat(),
            standard_series=STANDARD_2012,
            order="aqi_desc",
            page_size=50,
        ),
    }
    result = {}
    for size in ("small", "expanded"):
        if size == "expanded":
            expand_plan_fixture(ids)
        # Recompute planner statistics after each controlled F population.
        with connection.cursor() as cursor:
            cursor.execute("""ANALYZE observations_cityobservation, observations_cityrevision,
                observations_stationobservation, observations_stationrevision""")
        sizes = {
            "station_keys": sql(
                "SELECT count(*) FROM observations_stationobservation WHERE product_id=%s AND site_epoch_id=%s",
                products["S"],
                ids["epoch"],
            )[0][0],
            "l_keys": sql(
                "SELECT count(*) FROM observations_cityobservation WHERE product_id=%s",
                products["L"],
            )[0][0],
            "l_snapshot_cities": sql(
                """SELECT count(DISTINCT city_id) FROM observations_cityobservation
                   WHERE product_id=%s AND observed_at_utc=%s AND standard_series=%s""",
                products["L"],
                T0,
                STANDARD_2012,
            )[0][0],
        }
        sample = {"sizes": sizes, "requests": {}}
        for name, route in routes.items():
            queries.clear()
            start = perf_counter()
            response = call(reader, route)
            elapsed = perf_counter() - start
            captured = list(queries)
            explain = {}
            for statement, bound in captured:
                # Persist only plan metadata. SQL text, literals and bound data remain local.
                if "jsonb_array_elements" not in statement or (statement, repr(bound)) in explain:
                    continue
                report = sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, *(bound or ())
                )[0][0][0]
                plan = report["Plan"]
                explain[(statement, repr(bound))] = {
                    "shape": execution_plan(plan),
                    "shared_hits": plan.get("Shared Hit Blocks", 0),
                    "shared_reads": plan.get("Shared Read Blocks", 0),
                    "db_ms": report["Execution Time"],
                }
            check(bool(explain), "actual authorized SQL captured for " + name)
            sample["requests"][name] = {
                "sql_count": len(captured),
                "http_ms": round(1000 * elapsed, 3),
                "returned_rows": len(response["results"]),
                "plans": list(explain.values()),
            }
        result[size] = sample
    check(
        result["expanded"]["sizes"]
        == {"station_keys": 1438, "l_keys": 956, "l_snapshot_cities": 320},
        "expanded F observations committed before HTTP measurement",
    )
    measured = result["expanded"]["requests"]
    check(
        {name: r["returned_rows"] for name, r in measured.items()}
        == {"history": 50, "station_latest": 2, "national": 50},
        "expanded F reads remain bounded by page size and series count",
    )
    queries.clear()
    return result


def db_failure(reader, ids):
    from django.conf import settings
    from django.db import connections

    previous = settings.DATABASES["default"]["PORT"]
    # Change only this process's connection endpoint. PostgreSQL and other HTTP services remain untouched.
    settings.DATABASES["default"]["PORT"] = "1"
    try:
        call(
            reader,
            detail(ids["products"]["S"], ids["epoch"]),
            status=503,
            code="database_unavailable",
        )
    finally:
        settings.DATABASES["default"]["PORT"] = previous
        connections.close_all()
    call(reader, detail(ids["products"]["S"], ids["epoch"]))


def record(receipt, slug, label, function):
    entry = {"check": slug, "input_expected": label}
    receipt["cases"].append(entry)
    try:
        result = function()
    except Exception as exc:
        label = str(exc) if type(exc) is AssertionError and exc.args else type(exc).__name__
        entry.update(result="FAIL", observed=label)
        raise
    entry.update(result="PASS", observed=result)
    return result


def verify(phase, archive, receipt):
    import django
    from django.db import connection

    password = secrets.token_urlsafe(32)
    receipt["runtime"] = {
        "django": django.get_version(),
        "postgresql": sql("SHOW server_version")[0][0],
    }
    django_check("migrate", interactive=False)
    old_leaf()
    ids = seed(password)
    with web() as (base, queries):

        def ready():
            try:
                return Probe(base).request("/health/ready/")[0] == 200
            except URLError:
                return False

        wait_for("isolated Web and PG readiness", ready)
        reader, operator = migration(base, ids, password, phase)
        receipt["cases"].append(
            {
                "check": "V15" if phase == "s04" else "baseline",
                "input_expected": "real old Cookie and seeded S01–S03 facts survive forward migration",
                "result": "PASS",
                "observed": "comparison of old Cookie and independent SQL/API snapshots succeeded"
                if phase == "s04"
                else "pre-S04 HTTP and SQL fixture verified",
            }
        )
        if phase == "baseline":
            return
        if phase == "h":
            record(
                receipt,
                "V16/H",
                "official UCI archive, two sites and two licensed windows, independent CSV/SQL/HTTP reconciliation",
                lambda: h_http(reader, archive),
            )
            return
        reader = account_http(base, ids, password)
        receipt["cases"].append(
            {
                "check": "V01/HTTP",
                "input_expected": "anonymous, invalid CSRF, privilege injection rejected; legitimate register/login and PATCH succeeds",
                "result": "PASS",
                "observed": "USER flags and permission rows verified by independent SQL",
            }
        )
        record(
            receipt,
            "V02/V07/V11/HTTP",
            "exact and explicit history, offset equivalence, empty gap, F isolation, bounded pagination",
            lambda: {"co_rows_match_independent_sql": detail_http(reader, ids)},
        )
        new_password = private_http(base, reader, ids, password)
        receipt["cases"].append(
            {
                "check": "V12/HTTP",
                "input_expected": "profile isolation, CSRF, wrong/weak password, password change, old Cookie replay, disable, permission revoke",
                "result": "PASS",
                "observed": "real HTTP and independent PG permissions passed",
            }
        )
        # Restore the controlled import permission fixture after the V12 negative case.
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        get_user_model().objects.get(pk=ids["operator"]).user_permissions.add(
            Permission.objects.get(
                content_type__app_label="observations", codename="review_observation"
            )
        )
        if archive is None:
            receipt["cases"].append(
                {
                    "check": "V16/H",
                    "input_expected": "authorized official local UCI inner ZIP",
                    "result": "NOT RUN",
                    "observed": "no archive supplied",
                }
            )
        else:
            record(
                receipt,
                "V16/H",
                "exact archive bytes, independent raw CO conversion, SQL key/revision/unit, HTTP station/latest and history, uncovered gap",
                lambda: h_http(reader, archive),
            )
        record(
            receipt,
            "V17/F journey",
            "genuine authorized L → C/T → directory → station → window → favorites → new Session; independent SQL",
            lambda: journey(reader, base, ids, new_password, password),
        )
        if archive:
            record(
                receipt,
                "V14/H and F",
                "H and F private intents coexist in isolated domains; display withdrawal hides H summary",
                lambda: mixed_domain(reader, ids, archive),
            )
        record(
            receipt,
            "V11/SQL plans",
            "compare authorized SQL, page sizes and execution plans on small and expanded F datasets",
            lambda: plans(reader, ids, queries),
        )
        record(
            receipt,
            "V17/PG outage",
            "connection endpoint unavailable then restored",
            lambda: db_failure(reader, ids),
        )
        connection.close()


def main():
    import sys
    import traceback

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("baseline", "s04", "h", "h-import"), required=True)
    args = parser.parse_args()
    run_id = os.environ.get("S04_VERIFY_RUN")
    if not run_id:
        parser.error("use verify_s04.py to create a disposable verification database")
    import django

    django.setup()
    marker = sql("SELECT current_setting('airquality.s04_verify_run', true)")[0][0]
    if marker != run_id:
        parser.error("the connected database does not belong to this verification run")
    if args.phase == "h-import":
        print(json.dumps(h_import(sys.stdin.buffer)))
        return 0
    receipt = {"cases": []}
    status = 0
    try:
        verify(args.phase, json.load(sys.stdin), receipt)
    except Exception as exc:
        receipt["failure"] = type(exc).__name__
        traceback.print_exc(file=sys.stderr)
        status = 1
    finally:
        print(json.dumps(receipt, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
