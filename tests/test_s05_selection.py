"""Official Session/CSRF routes and independent SQL against project-authored F."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.db import connection
from django.test import Client
from observations.models import CityRevision
from observations.policy import revoke_grant
from s05_support import END, HOUR, START, authorize, candidate, city, grant, ingest, product

pytestmark = pytest.mark.django_db(transaction=True)
DETAIL = "/api/v1/analysis/details/"


def parent(p, entities, *, method="detail", hours=24, indicators=("CO",), grain="day"):
    return dict(
        selection=dict(
            product=p.pk,
            source=p.source.namespace,
            scope="synthetic",
            **{"cities" if p.level == "city" else "site_epochs": [x.pk for x in entities]},
            indicators=list(indicators),
            start=START.isoformat(),
            end=(START + hours * HOUR).isoformat(),
        ),
        method=method,
        method_version="s05.v1",
        grain=grain,
    )


def session(user):
    c = Client(enforce_csrf_checks=True)
    c.force_login(user)
    c.defaults["HTTP_X_CSRFTOKEN"] = c.get("/api/v1/auth/csrf/").json()["csrf_token"]
    return c


def post(c, url, body):
    return c.post(url, body, content_type="application/json")


def test_pages_rights_hidden_rows_and_parent_version(django_user_model):
    op = django_user_model.objects.create_superuser("s05curator")
    user = django_user_model.objects.create_user("s05reader")
    web = session(user)
    p = product("reading", "C")
    a, b = city(p, "101"), city(p, "102")
    grants = authorize(op, p)
    ingest(op, p, [candidate(p, "101", at=START + i * HOUR, value=i, token=1) for i in range(3)])
    req = parent(p, [a], hours=8784, grain="month")
    r = post(web, DETAIL, req | {"page_size": 1})
    assert r.status_code == 200, r.content
    first = r.json()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT k.id,r.id,b.fetched_at FROM observations_cityobservation k JOIN observations_cityrevision r ON r.key_id=k.id JOIN observations_receipt t ON t.city_revision_id=r.id JOIN observations_batch b ON b.id=t.batch_id WHERE k.city_id=%s ORDER BY k.observed_at_utc",
            (a.pk,),
        )
        expected = cur.fetchall()
    assert [first["results"][0][k] for k in ("key", "revision")] == list(expected[0][:2])
    assert (
        first["results"][0]["fetched_at"]
        and first["results"][0]["fetched_at_basis"] == "contributing_batch"
    )
    assert "private" in r["Cache-Control"] and "no-store" in r["Cache-Control"]
    ingest(op, p, [candidate(p, "102", at=START, value="50", token=1)])
    grant(op, p, "display", object_code="102", fields={"provenance"})
    second = post(web, DETAIL, req | {"page_size": 1}).json()
    assert first["input_version"] == second["input_version"]
    base = req | {"input_version": first["input_version"], "page_size": 1}
    params = base | {"cursor": first["page"]["next_cursor"]}
    page2 = post(web, DETAIL, params).json()
    page3 = post(web, DETAIL, base | {"cursor": page2["page"]["next_cursor"]}).json()
    assert [
        (item["results"][0]["key"], item["results"][0]["revision"])
        for item in (first, page2, page3)
    ] == [pair[:2] for pair in expected]
    assert page3["page"]["next_cursor"] is None
    assert post(session(op), DETAIL, params).json() == {"code": "invalid_cursor"}
    ingest(op, p, [candidate(p, "101", at=START + HOUR, value="49", token=3)])
    assert post(web, DETAIL, params).json() == {"code": "input_changed"}
    assert CityRevision.objects.filter(key__city=a, is_current=True).count() == 3
    revoke_grant(op.pk, grants["display"].pk, "selected object display test")
    grant(op, p, "display", object_code="101", fields={"catalog", "normalized", "provenance"})
    for ids in ([a.pk, b.pk], [a.pk, 960001]):
        bad = deepcopy(req)
        bad["selection"]["cities"] = ids
        bad["selection"]["end"] = (START + HOUR).isoformat()
        response = post(web, DETAIL, bad)
        assert (response.status_code, response.json()) == (403, {"code": "use_denied"})


def test_csrf_shape_cost_and_authorization_precedence(django_user_model):
    user = django_user_model.objects.create_user("budget")
    web = session(user)
    p = product("budget", "C")
    a = city(p, "101")
    req = parent(p, [a])
    no_csrf = Client(enforce_csrf_checks=True)
    no_csrf.force_login(user)
    assert post(no_csrf, DETAIL, req).json() == {"code": "csrf_failed"}
    assert post(Client(), DETAIL, req).json() == {"code": "not_authenticated"}
    for bad in (
        req | {"owner": user.pk},
        req | {"audience": "operator"},
        req | {"selection": req["selection"] | {"cities": [True]}},
        req | {"selection": req["selection"] | {"end": "2024-01-02T00:00:00"}},
    ):
        assert post(web, DETAIL, bad).json() == {"code": "invalid_request"}
    too_big = req | {"selection": req["selection"] | {"end": END.isoformat()}}
    assert post(web, DETAIL, too_big).json() == {"code": "query_too_large"}
    bad_unit = req | {"selection": req["selection"] | {"units": {"CO": "1"}}}
    assert post(web, DETAIL, bad_unit).json() == {"code": "use_denied"}


def test_authorized_aqi_source_description_requires_both_uses(django_user_model):
    from observations.policy import REQUIRED

    op = django_user_model.objects.create_superuser("s05aqi-curator")
    web = session(django_user_model.objects.create_user("s05aqi-reader"))
    p = product("optional-description", "C")
    a = city(p)
    authorize(op, p)
    # Only the description covered at ingestion can later be displayed. A
    # minimal field-specific allow is insufficient for the read projection.
    c = candidate(p, indicator="AQI")
    other = candidate(p, indicator="AQI", at=START + HOUR)
    ingest(op, p, [replace(other, data=replace(other.data, source_description="F unlicensed"))])
    assert CityRevision.objects.get(key__observed_at_utc=START + HOUR).source_description == ""
    grant(op, p, "retain_normalized", fields={"source_description"})
    ingest(op, p, [replace(c, data=replace(c.data, source_description="F source wording"))])
    query = parent(p, [a], indicators=("AQI",))

    def row():
        result = post(web, DETAIL, query)
        assert result.status_code == 200, result.content
        return result.json()["results"][0]

    assert "source_description" not in row()
    grant(op, p, "display", fields=REQUIRED | {"source_description"})
    assert "source_description" not in row()
    grant(op, p, "retain_normalized", fields=REQUIRED | {"source_description"})
    entry = row()
    assert (
        entry["standard_series"] == "unknown" and entry["source_description"] == "F source wording"
    )
    assert "grade" not in entry and "color" not in entry


def test_daily_details_bind_one_whole_source_local_day(django_user_model):
    from accounts.research_exports import export_fields

    op = django_user_model.objects.create_superuser("s05daily-curator")
    web = session(django_user_model.objects.create_user("s05daily-reader"))
    p = product("dated-report", "D", timezone="Asia/Shanghai")
    a = city(p)
    authorize(op, p, detail_fields=export_fields("detail"))
    at = START + 16 * HOUR
    ingest(op, p, [candidate(p, at=at, window="local_daily")])
    query = parent(p, [a])
    query["selection"].update(
        start=at.isoformat(), end=(at + 24 * HOUR).isoformat(), window_definition="local_daily"
    )
    result = post(web, DETAIL, query)
    assert result.status_code == 200, result.content
    doc = result.json()
    assert doc["results"][0]["local_report_date"] == "2024-01-02"
    assert doc["results"][0]["observed_at"] is None
    ticket = post(
        web,
        "/api/v1/me/exports/",
        {"kind": "detail", "groups": [query | {"input_version": doc["input_version"]}]},
    )
    assert ticket.status_code == 201, ticket.content
    download = web.get("/api/v1/me/exports/" + ticket.json()["id"] + "/download/?scope=synthetic")
    import csv
    from io import StringIO

    assert (
        list(csv.DictReader(StringIO(download.content.decode())))[0]["observed_at"] == "2024-01-02"
    )
    for start, end in ((at + HOUR, at + 24 * HOUR), (at, at + 48 * HOUR)):
        bad = query | {
            "selection": query["selection"] | {"start": start.isoformat(), "end": end.isoformat()}
        }
        rejected = post(web, DETAIL, bad)
        assert (rejected.status_code, rejected.json()) == (422, {"code": "incompatible_series"})


@pytest.mark.parametrize("alias_hour", [48, 24], ids=["sequential-codes", "overlapping-codes"])
def test_mixed_calendar_keeps_known_unscheduled_observations_out_of_effective_rows(
    django_user_model,
    alias_hour,
):
    from observations.models import CityIdentifier
    from psycopg.types.range import TimestamptzRange

    op = django_user_model.objects.create_superuser("s05calendar-curator")
    web = session(django_user_model.objects.create_user("s05calendar-reader"))
    p = product("changing-code", "C")
    split = START + 48 * HOUR
    a = city(p, "first-day", end=split)
    CityIdentifier.objects.create(
        source=p.source,
        city=a,
        code="unknown",
        active_period=TimestamptzRange(START + alias_hour * HOUR, END),
        evidence="F code change",
    )
    authorize(op, p)
    ingest(
        op,
        p,
        [
            candidate(p, "first-day", START, 10),
            candidate(p, "first-day", START + 24 * HOUR, 20),
            candidate(p, "unknown", split, 30),
        ],
    )
    req = parent(p, [a], method="statistics", hours=72)
    result = post(web, "/api/v1/analysis/statistics/", req)
    assert result.status_code == 200, result.content
    document = result.json()
    column = document["columns"][0]
    assert (column["E"], column["O"], column["V"], column["outside_schedule"]) == (None, 2, 2, 1)
    assert [(b["E"], b["O"], b["V"], b["outside_schedule"]) for b in document["buckets"]] == [
        (24, 1, 1, 0),
        (0 if alias_hour == 48 else None, 0, 0, 1),
        (None, 1, 1, 0),
    ]
    assert document["buckets"][1]["mean"] is None
    details = req | {"input_version": document["input_version"]}
    assert len(post(web, DETAIL, details | {"support": "observed"}).json()["results"]) == 3
    assert [
        int(float(r["value"]))
        for r in post(web, DETAIL, details | {"support": "effective"}).json()["results"]
    ] == [10, 30]


def test_hidden_product_while_source_locked_has_same_read_denial_as_missing(django_user_model):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connections, transaction
    from observations.models import Source

    web = session(django_user_model.objects.create_user("s05-contention-reader"))
    p = product("no-research", "C")
    a = city(p)
    query = parent(p, [a], method="statistics", hours=1)
    ready, release = Event(), Event()

    def hold_source():
        try:
            with transaction.atomic():
                Source.objects.select_for_update().get(pk=p.source_id)
                ready.set()
                assert release.wait(5)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(1) as workers:
        writer = workers.submit(hold_source)
        try:
            assert ready.wait(3)
            for id_ in (p.pk, p.pk + 1000000):
                result = post(
                    web,
                    "/api/v1/analysis/statistics/",
                    query | {"selection": query["selection"] | {"product": id_}},
                )
                assert (result.status_code, result.json()) == (403, {"code": "use_denied"})
        finally:
            release.set()
        writer.result()
