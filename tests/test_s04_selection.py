"""S04 F: scope/quality, original lineage, stable pages and real session HTTP."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Thread
from wsgiref.simple_server import make_server

import httpx
import pytest
from django.core.wsgi import get_wsgi_application
from django.db import transaction
from django.test import Client
from observations.detail import read_detail
from observations.models import Receipt, StationIdentifier, StationObservation, StationRevision
from observations.policy import Denied, revoke_grant
from observations.queries import read_current
from observations.selection import ReadContext
from s02_support import (
    BOUNDARY,
    END,
    START,
    allow,
    candidate,
    create_product,
    create_site,
    in_db_session,
    ingest_fixture,
    observe_db,
    selector,
    wait_for_block,
)

pytestmark = pytest.mark.django_db(transaction=True)
HOUR = timedelta(hours=1)
URL = "/api/v1/observations/query/"


@pytest.fixture
def catalog(django_user_model):
    operator = django_user_model.objects.create_superuser("curator")
    reader = django_user_model.objects.create_user("observer")
    product = create_product("s04-history")
    site = create_site(product)
    for op in ("process_local", "retain_normalized"):
        allow(operator, product, op, fields=["*"])
    query = dict(
        product=str(product.pk),
        scope="synthetic",
        site_epochs=str(site.pk),
        indicators="CO",
        mode="window",
        report_kind="hourly",
        window_definition="hourly_label_unspecified",
        start=START.isoformat(),
        end=END.isoformat(),
    )
    return operator, reader, product, site, query


@pytest.fixture
def loopback_http():
    with make_server("127.0.0.1", 0, get_wsgi_application()) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def test_window_pages_and_cookies_match_independent_sql(catalog, loopback_http):
    op, reader, p, site, q = catalog
    password = secrets.token_urlsafe(24)
    reader.set_password(password)
    reader.save()
    allow(op, p, "display")
    ingest_fixture(
        op.pk,
        p.pk,
        "s04-1",
        "fixture",
        [candidate(p, value="0", at=START), candidate(p, at=START + 2 * HOUR)],
    )
    with httpx.Client(base_url=loopback_http, trust_env=False) as web, observe_db() as db:
        assert web.get(URL, params=q).json() == {"code": "not_authenticated"}
        csrf = web.get("/api/v1/auth/csrf/").json()["csrf_token"]
        assert (
            web.post(
                "/api/v1/auth/login/",
                headers={"X-CSRFToken": csrf},
                json={"username": reader.username, "password": password},
            ).status_code
            == 200
        )
        expected = db.execute(
            "SELECT k.id,r.id FROM observations_stationobservation k JOIN observations_stationrevision r ON r.key_id=k.id WHERE k.product_id=%s AND r.is_current ORDER BY k.observed_at_utc DESC,k.id DESC",
            (p.pk,),
        ).fetchall()
        response = web.get(URL, params=q | {"page_size": "1"})
        assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
        first = response.json()
        tail = web.get(
            URL, params=q | {"page_size": "1", "cursor": first["page"]["next_cursor"]}
        ).json()
        assert [(r["key"], r["revision"]) for r in first["results"] + tail["results"]] == expected
        assert tail["page"]["next_cursor"] is None and tail["results"][0]["value"] == "0.000000"
        assert (
            read_current(reader.pk, selector(p, site))["results"][0]["revision_id"]
            == expected[-1][1]
        )
        gap = web.get(
            URL,
            params=q | {"start": (START + HOUR).isoformat(), "end": (START + 2 * HOUR).isoformat()},
        ).json()
        assert gap["results"] == [] and gap["state"] == "no_data"
        equiv = web.get(
            URL, params=q | {"start": "2024-01-01T08:00:00+08:00", "end": "2024-01-01T01:00:00Z"}
        ).json()
        assert [r["key"] for r in equiv["results"]] == [expected[-1][0]]


def test_batched_scope_matches_code_product_day_and_independent_fields(catalog):
    op, reader, p, site, q = catalog
    ident = StationIdentifier.objects.get(station=site.station)
    StationIdentifier.objects.create(
        source=p.source,
        station=site.station,
        active_period=ident.active_period,
        code="002",
        evidence="F",
    )
    other = create_product("other", "S", p.source)
    for prod in (p, other):
        allow(op, prod, "display", fields=["normalized"])
        allow(op, prod, "display", fields=["provenance"])
    allow(op, other, "display", fields=["*"])
    batch, _ = ingest_fixture(
        op.pk,
        p.pk,
        "s04-codes",
        "F",
        [
            candidate(p, at=START),
            candidate(p, code="002", at=START + HOUR),
            candidate(p, at=BOUNDARY),
        ],
    )
    with pytest.raises(Denied, match="use_denied"):
        read_detail(reader.pk, q)
    grant = allow(op, p, "display", object_code="001", fields=["*"])
    allow(op, p, "display", effect="deny", object_code="001", data_from=BOUNDARY.date())
    allow(op, p, "display", effect="deny", fields=["source_time", "source_description"])
    visible = read_detail(reader.pk, q)["results"]
    assert (
        len(visible) == 1
        and visible[0]["fetched_at"] == batch.fetched_at
        and visible[0]["source_time"] is None
    )
    exact = read_current(reader.pk, selector(p, site))["results"][0]
    assert (
        exact["revision_id"] == visible[0]["revision"]
        and exact["source_time"] == exact["source_description"] == ""
    )
    with pytest.raises(Denied, match="use_denied"):
        read_detail(reader.pk, q | {"start": BOUNDARY.isoformat()})
    revoke_grant(op.pk, grant.pk, "end F display")
    with pytest.raises(Denied, match="use_denied"):
        read_detail(reader.pk, q)


def test_quality_and_original_receipt(catalog):
    op, reader, p, site, q = catalog
    allow(op, p, "display", fields=["*"])
    first, _ = ingest_fixture(
        op.pk,
        p.pk,
        "s04-quality",
        "F",
        [
            candidate(p, 1, "0"),
            candidate(p, 1, "NA", at=START + HOUR),
            candidate(p, 1, "<3", at=START + 2 * HOUR),
            candidate(p, 1, "65", at=START + 3 * HOUR),
            candidate(p, value="6", at=START + 4 * HOUR),
        ],
    )
    ingest_fixture(op.pk, p.pk, "s04-conflict", "F", [candidate(p, value="7", at=START + 4 * HOUR)])
    duplicate, _ = ingest_fixture(op.pk, p.pk, "s04-repeat", "F", [candidate(p, 2, "0")])
    duplicate.receipts.update(collection_metadata={"source_description": "later text"})
    rows = read_detail(reader.pk, q | {"quality": "all"})["results"]
    assert [(r["quality"], r["value"]) for r in rows] == [
        ("disputed", None),
        ("suspect", "65.000000"),
        ("censored", None),
        ("missing", None),
        ("valid", "0.000000"),
    ]
    assert rows[2]["censor_limit"] == "3.000000" and rows[2]["censor_direction"] == "<"
    assert rows[-1]["fetched_at"] == first.fetched_at and not rows[-1]["source_description"]
    assert [r["key"] for r in read_detail(reader.pk, q)["results"]] == [rows[-1]["key"]]
    assert not any(r["comparable"] for r in rows[:-1])
    StationObservation.objects.filter(pk=rows[-1]["key"]).update(retired=True)
    assert read_detail(reader.pk, q)["results"] == []
    assert Receipt.objects.filter(batch=first).count() == 5


def test_cursor_rechecks_visible_set_and_request_limits(catalog):
    op, reader, p, site, q = catalog
    broad = allow(op, p, "display")
    ingest_fixture(
        op.pk, p.pk, "s04-cursor", "F", [candidate(p, at=START), candidate(p, at=BOUNDARY)]
    )
    q = q | {"page_size": "1"}
    token = read_detail(reader.pk, q)["page"]["next_cursor"]
    client = Client()
    client.force_login(reader)
    for args, code in [
        (q | {"cursor": token + "X"}, "invalid_cursor"),
        (q | {"page_size": "2", "cursor": token}, "invalid_cursor"),
        (q | {"end": START.isoformat()}, "invalid_request"),
        (q | {"start": "2024-01-01T00:00:00"}, "invalid_request"),
        (q | {"unit": "µg/m³"}, "invalid_request"),
        (q | {"standard_series": "unknown"}, "invalid_request"),
        (q | {"end": (START + timedelta(days=31)).isoformat()}, "query_too_large"),
        (q | {"page_size": "101"}, "query_too_large"),
        (q | {"cities": "1"}, "invalid_request"),
        (q | {"product": [p.pk, p.pk]}, "invalid_request"),
        (q | {"x": "1"}, "invalid_request"),
    ]:
        r = client.get(URL, args)
        assert (r.status_code, r.json()) == (400, {"code": code})
    allow(op, p, "display", effect="deny", data_from=BOUNDARY.date())
    assert client.get(URL, q | {"cursor": token}).status_code == 409
    revoke_grant(op.pk, broad.pk, "F no display")
    r = client.get(URL, q | {"cursor": token})
    assert (r.status_code, r.json()) == (403, {"code": "use_denied"}) and "private" in r[
        "Cache-Control"
    ]


def test_product_lock_and_end_clock_cover_empty_pages(catalog):
    from django.db import connection
    from django.utils import timezone

    op, reader, p, site, q = catalog
    grant = allow(op, p, "display")
    # Observe the second backend waiting for the transaction holding the policy lock.
    queue = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            ctx = ReadContext(reader.pk, [p.pk], True)
            assert ctx.scope(p, ["CO"], [site.pk]).permitted
            fut = pool.submit(
                in_db_session, queue, lambda: revoke_grant(op.pk, grant.pk, "F lock test")
            )
            wait_for_block(queue.get(timeout=5))
            ctx.finish()
        fut.result(timeout=5)
    with pytest.raises(Denied):
        read_detail(reader.pk, q)
    grant = allow(op, p, "display")
    with transaction.atomic():
        grant.valid_until = timezone.now() + timedelta(milliseconds=180)
        grant.save(update_fields=["valid_until"])
        ctx = ReadContext(reader.pk, [p.pk], True)
        assert ctx.scope(p, ["CO"], [site.pk]).permitted
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s - clock_timestamp())) + 0.01))",
                [grant.valid_until],
            )
        with pytest.raises(Denied, match="use_denied"):
            ctx.finish()
    assert not StationRevision.objects.filter(key__product=p).exists()


def test_explicit_outside_epoch_needs_independent_disclosable_catalog(catalog):
    op, reader, p, site, q = catalog
    # Both directory and normalized privileges span the request day, but the confirmed site ended.
    for operation in ("retain_normalized", "display"):
        allow(
            op, p, operation, fields=["*"], data_until=(END + 2 * HOUR).date() + timedelta(days=1)
        )
    outside = q | {"start": (END + HOUR).isoformat(), "end": (END + 2 * HOUR).isoformat()}
    assert read_detail(reader.pk, outside)["state"] == "no_coverage"
    allow(
        op,
        p,
        "display",
        effect="deny",
        fields=["catalog"],
        data_until=END.date() + timedelta(days=1),
    )
    with pytest.raises(Denied, match="use_denied"):
        read_detail(reader.pk, outside)


def test_source_local_day_and_separate_rolling_window(catalog):
    from dataclasses import replace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from django.utils import timezone
    from observations.normalization import BatchContext, ProductProfile, normalize

    op, reader, p, site, q = catalog
    p.source_timezone = "Asia/Shanghai"
    p.save(update_fields=["source_timezone"])
    allow(op, p, "display", data_until=BOUNDARY.date())
    zone = ZoneInfo(p.source_timezone)
    local_midnight = datetime(2024, 1, 2, tzinfo=zone)
    inputs = []
    for window in ("hourly_right_label", "rolling_8h_right_label"):
        for at in (local_midnight - HOUR, local_midnight):
            sample = normalize(
                ProductProfile("S", p.source_timezone, "F", window),
                {
                    "indicator": "O3",
                    "object_code": "001",
                    "source_time": at.isoformat(),
                    "raw_value": "31",
                    "raw_unit": "µg/m³",
                    "row_locator": "F",
                },
                BatchContext(timezone.now()),
            )
            inputs.append(replace(candidate(p), data=sample))
    ingest_fixture(op.pk, p.pk, "local-day", "F", inputs)
    for mode, window in [
        ("hourly", "hourly_right_label"),
        ("rolling_8h", "rolling_8h_right_label"),
    ]:
        options = q | {
            "indicators": "O3",
            "report_kind": mode,
            "window_definition": window,
            "start": (local_midnight - HOUR).isoformat(),
            "end": (local_midnight + HOUR).isoformat(),
        }
        rows = read_detail(reader.pk, options)["results"]
        assert len(rows) == 1
        assert (
            rows[0]["observed_at"] == local_midnight - HOUR
            and rows[0]["window_definition"] == window
        )
        with pytest.raises(Denied, match="use_denied"):
            read_detail(reader.pk, options | {"start": local_midnight.isoformat()})
