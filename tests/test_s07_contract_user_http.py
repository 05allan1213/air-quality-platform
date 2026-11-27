"""The S07 participant sample, executed over HTTP with disposable F inputs."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from accounts.models import ExportTicket
from accounts.research_exports import export_fields
from django.core.cache import cache
from django.db import OperationalError
from django.db.models import F
from s05_http import loopback
from s05_support import authorize, candidate, city, ingest, product, site
from s07_contract import PREFIX, Witness, check_response, specification
from test_s07_contract import exact_shape

pytestmark = pytest.mark.django_db(transaction=True)
F_DOMAIN = {"scope": "synthetic"}
CATALOG = PREFIX + "catalog/objects/"
NATIONAL = PREFIX + "overview/national/"
DETAIL = PREFIX + "observations/query/"
EXACT = PREFIX + "observations/current/"
STATISTICS = PREFIX + "analysis/statistics/"
COMPARE = PREFIX + "analysis/compare/"
DETAILS = PREFIX + "analysis/details/"
EXPORTS = PREFIX + "me/exports/"
DOWNLOAD = EXPORTS + "{item_id}/download/"
ME = PREFIX + "me/"
AT = datetime(2024, 2, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def user_model(users, uname):
    return users.objects.create_user(uname, password="s05-test-only")


def seed_products(users):
    curator = users.objects.create_superuser("contract-curator")
    nationally = product("contract-l", "L")
    target = city(nationally)
    readings = product("contract-c", "C", source=nationally.source)
    trend = product("contract-t", "T", source=nationally.source)
    stations = product("contract-s", "S", source=nationally.source)
    site(stations, parent=target)
    for item in (nationally, readings, trend, stations):
        authorize(curator, item, detail_fields=export_fields("detail") if item == readings else ())
        samples = (
            [
                candidate(item, at=AT + pos * HOUR, indicator="CO", value=str(10 + pos), token=1)
                for pos in range(3)
            ]
            if item.shape == "C"
            else [candidate(item, at=AT, indicator="CO" if item.shape == "S" else "AQI", token=1)]
        )
        ingest(curator, item, samples)


@pytest.fixture
def http_witness(request):
    observed = Witness()
    yield observed
    request.node.user_properties.append(("s07_http_contract", json.dumps(observed.receipt())))


def test_participant_f_sample(django_user_model, http_witness):
    seed_products(django_user_model)
    reader = user_model(django_user_model, "contract-participant")
    peer = user_model(django_user_model, "contract-peer")
    with loopback(reader) as (web, _), loopback(peer) as (other, _):
        for client in (web, other):
            client.event_hooks["response"].append(http_witness.inspect)
        profile = check_response(web.get(ME), ME, 200)
        assert profile["username"] == reader.username
        catalog = check_response(web.get(CATALOG, params=F_DOMAIN), CATALOG, 200)
        initial = {row["shape"]: row for row in catalog["results"]}
        assert set(initial) == {"L", "C", "T", "S"}
        overview = check_response(web.get(initial["L"]["link"]), NATIONAL, 200)
        (national_city,) = overview["results"]
        city_path = PREFIX + "cities/{city}/"
        city_info = check_response(
            web.get(national_city["links"]["city"][0]["href"]), city_path, 200
        )
        assert len(city_info["slots"]) == 7 and city_info["city"] == national_city["city"]
        trend_path = PREFIX + "cities/{city}/trend/"
        trend = check_response(web.get(city_info["links"]["trend"][0]["href"]), trend_path, 200)
        assert len(trend["points"]) == 24 and trend["product"] == initial["T"]["product"]
        station_path = PREFIX + "cities/{city}/stations/"
        stations = check_response(
            web.get(city_info["links"]["stations"][0]["href"]), station_path, 200
        )
        (epoch,) = stations["results"]
        station = check_response(web.get(epoch["link"]), PREFIX + "stations/{station}/", 200)
        assert station["site_epoch"] == epoch["site_epoch"]
        fav_path = PREFIX + "me/favorites/"
        favorite = check_response(
            web.post(
                fav_path,
                params=F_DOMAIN,
                json={
                    "kind": "city",
                    "target_id": city_info["city"],
                    "product": city_info["product"],
                },
            ),
            fav_path,
            201,
        )
        (favorite_again,) = check_response(web.get(fav_path, params=F_DOMAIN), fav_path, 200)[
            "results"
        ]
        for key in ("id", "kind", "state"):
            assert favorite_again[key] == favorite[key]
        favorite_path = fav_path + "{item_id}/"
        favorite_url = favorite_path.format(item_id=favorite["id"])
        check_response(
            other.get(favorite_url, params=F_DOMAIN), favorite_path, 404, code="not_found"
        )
        assert (
            check_response(web.get(favorite_url, params=F_DOMAIN), favorite_path, 200)["id"]
            == favorite["id"]
        )
        check_response(
            web.patch(favorite_url, params=F_DOMAIN, json={"product": city_info["product"]}),
            favorite_path,
            200,
        )
        check_response(web.delete(favorite_url, params=F_DOMAIN), favorite_path, 204)

        time_from = AT.isoformat()
        time_to = (AT + timedelta(days=1)).isoformat()
        selector = F_DOMAIN | {
            "product": city_info["product"],
            "cities": city_info["city"],
            "mode": "window",
            "indicators": "CO",
            "report_kind": "hourly",
            "window_definition": "hourly_label_unspecified",
            "start": time_from,
            "end": time_to,
        }
        selected = check_response(web.get(DETAIL, params=selector), DETAIL, 200)
        assert selected["selection"]["product"] == city_info["product"]
        assert [(r["unit"], r["value"]) for r in selected["results"]] == [
            ("mg/m³", "12.000000"),
            ("mg/m³", "11.000000"),
            ("mg/m³", "10.000000"),
        ]
        empty = check_response(
            web.get(
                DETAIL,
                params=selector
                | {
                    "start": time_to,
                    "end": (AT + timedelta(days=2)).isoformat(),
                },
            ),
            DETAIL,
            200,
        )
        assert empty["state"] == "no_data" and empty["results"] == []
        exact_params = F_DOMAIN | {
            "product": city_info["product"],
            "city": city_info["city"],
            "report_kind": "hourly",
            "window_definition": "hourly_label_unspecified",
            "indicator": "CO",
            "observed_at": time_from,
            "standard_series": "not_applicable",
        }
        exact = check_response(web.get(EXACT, params=exact_params), EXACT, 200)
        exact_shape(exact)
        assert exact["results"][0]["revision_id"] == selected["results"][-1]["revision"]
        intent = {
            "selection": F_DOMAIN
            | {
                "product": city_info["product"],
                "cities": [city_info["city"]],
                "indicators": ["CO"],
                "source": city_info["source"],
                "start": time_from,
                "end": time_to,
            },
            "method": "statistics",
            "method_version": "s05.v1",
            "grain": "day",
        }
        statistics = check_response(web.post(STATISTICS, json=intent), STATISTICS, 200)
        assert statistics["columns"][0]["O"] == statistics["columns"][0]["V"] == 3
        group = intent | {"input_version": statistics["input_version"]}
        page1 = check_response(web.post(DETAILS, json=group | {"page_size": 2}), DETAILS, 200)
        page2 = check_response(
            web.post(
                DETAILS,
                json=group
                | {
                    "page_size": 2,
                    "cursor": page1["page"]["next_cursor"],
                },
            ),
            DETAILS,
            200,
        )
        assert [row["key"] for row in page1["results"] + page2["results"]] == [
            row["key"] for row in reversed(selected["results"])
        ]
        check_response(
            web.post(
                DETAILS,
                json=group
                | {
                    "input_version": exact["results"][0]["input_version"],
                },
            ),
            DETAILS,
            409,
            code="input_changed",
        )
        check_response(
            web.post(
                STATISTICS,
                json=intent
                | {
                    "selection": intent["selection"] | {"units": {"CO": "µg/m³"}},
                },
            ),
            STATISTICS,
            422,
            code="incompatible_series",
        )
        saved_path = PREFIX + "me/saved-queries/"
        saved = check_response(
            web.post(saved_path, json=intent | {"title": "F check"}), saved_path, 201
        )
        saved_item = saved_path + "{item_id}/"
        saved_url = saved_item.format(item_id=saved["id"])
        assert check_response(web.get(saved_path, params=F_DOMAIN), saved_path, 200)["results"] == [
            saved
        ]
        assert check_response(web.get(saved_url, params=F_DOMAIN), saved_item, 200) == saved
        patch = {"title": "F revised", "intent_revision": saved["intent_revision"]}
        changed = check_response(web.patch(saved_url, params=F_DOMAIN, json=patch), saved_item, 200)
        assert changed["intent_revision"] == saved["intent_revision"] + 1
        check_response(
            web.patch(saved_url, params=F_DOMAIN, json=patch), saved_item, 409, code="input_changed"
        )
        run_path = saved_path + "{item_id}/run/"
        revisited = check_response(
            web.post(run_path.format(item_id=saved["id"]), params=F_DOMAIN), run_path, 200
        )
        assert revisited["state"] == "current"
        check_response(web.delete(saved_url, params=F_DOMAIN), saved_item, 204)
        check_response(web.get(saved_url, params=F_DOMAIN), saved_item, 404, code="not_found")
        ticket = check_response(
            web.post(EXPORTS, json={"kind": "detail", "groups": [group], "fields": []}),
            EXPORTS,
            201,
        )
        ticket_item = EXPORTS + "{item_id}/"
        ticket_url = ticket_item.format(item_id=ticket["id"])
        persisted = check_response(web.get(ticket_url, params=F_DOMAIN), ticket_item, 200)
        assert persisted["id"] == ticket["id"]
        assert check_response(web.get(EXPORTS, params=F_DOMAIN), EXPORTS, 200)["results"] == [
            persisted
        ]
        download = DOWNLOAD.format(item_id=ticket["id"])
        accepted = web.get(download, params=F_DOMAIN)
        check_response(accepted, DOWNLOAD, 200)
        assert accepted.headers["content-type"] == "text/csv; charset=utf-8"
        denied = other.get(download, params=F_DOMAIN)
        check_response(denied, DOWNLOAD, 404, code="not_found")
        assert denied.content == b'{"code":"not_found"}'
        ExportTicket.objects.filter(pk=ticket["id"]).update(
            expires_at=F("created_at") + timedelta(microseconds=1)
        )
        check_response(web.get(download, params=F_DOMAIN), DOWNLOAD, 410, code="expired")
        check_response(web.delete(ticket_url, params=F_DOMAIN), ticket_item, 204)
        check_response(web.get(ticket_url, params=F_DOMAIN), ticket_item, 404, code="not_found")
        logout = PREFIX + "auth/logout/"
        check_response(web.post(logout), logout, 204)
        check_response(web.get(ME), ME, 403, code="not_authenticated")


def test_compare_f_sample(django_user_model, http_witness):
    reader = user_model(django_user_model, "contract-comparator")
    curator = django_user_model.objects.create_superuser("comparison-curator")
    series = product("contract-compare", "C")
    a = city(series)
    b = city(series, code="002", geography="F-other", name="Other F city")
    authorize(curator, series)
    ingest(
        curator,
        series,
        [
            candidate(series, code, AT, value=str(pos + 1))
            for pos, code in enumerate(("001", "002"))
        ],
    )
    intent = {
        "selection": F_DOMAIN
        | {
            "product": series.pk,
            "source": series.source.namespace,
            "cities": [a.pk, b.pk],
            "indicators": ["CO"],
            "start": AT.isoformat(),
            "end": (AT + HOUR).isoformat(),
        },
        "method": "compare",
        "method_version": "s05.v1",
        "grain": "hour",
    }
    with loopback(reader) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        result = check_response(web.post(COMPARE, json=intent), COMPARE, 200)
        assert [(row["entity"], row["O"], row["V"]) for row in result["columns"]] == [
            (a.pk, 1, 1),
            (b.pk, 1, 1),
        ]


def test_actual_cookie_errors_and_framework_boundaries(django_user_model, http_witness):
    reader = user_model(django_user_model, "contract-boundary")
    csrf_path = PREFIX + "auth/csrf/"
    login = PREFIX + "auth/login/"
    register = PREFIX + "auth/register/"
    with (
        loopback(reader) as (signed, _),
        httpx.Client(
            base_url=signed.base_url,
            trust_env=False,
            timeout=12,
        ) as anonymous,
    ):
        anonymous.event_hooks["response"].append(http_witness.inspect)
        signed.event_hooks["response"].append(http_witness.inspect)
        cache.delete("s01:register:127.0.0.1")
        cache.delete("s01:login:127.0.0.1")
        for path, methods in specification()["paths"].items():
            if path in {login, register, csrf_path, PREFIX + "auth/logout/"}:
                continue
            query_path = path
            for name in ("item_id", "run_id", "attempt_id", "segment_id", "operation_id"):
                if "{" + name + "}" not in query_path:
                    continue
                path_params = {"{item_id}": "1" if "favorites/" in path else str(uuid4())}
                param = path_params.get("{" + name + "}", str(uuid4()))
                query_path = query_path.replace("{" + name + "}", param)
            for name in (
                "pk",
                "city",
                "station",
                "product_id",
                "grant_id",
                "station_id",
                "event_id",
            ):
                query_path = query_path.replace("{" + name + "}", "1")
            query_path = query_path.replace("{action}", "publish")
            for method in methods:
                check_response(
                    anonymous.request(method.upper(), query_path),
                    path,
                    403,
                    code="not_authenticated",
                )
        prepared = anonymous.get(csrf_path)
        token = check_response(prepared, csrf_path, 200)["csrf_token"]
        assert "SameSite=Lax" in prepared.headers["set-cookie"]
        csrf_secret = anonymous.cookies.get("csrftoken")
        headers = {"X-CSRFToken": token}
        credentials = {"username": "f-contract-register", "password": "unit-4-testing-6-fixture"}
        check_response(anonymous.post(register, headers=headers, json=credentials), register, 201)
        check_response(
            anonymous.post(register, headers=headers, json=credentials),
            register,
            400,
            code="invalid_request",
        )
        logged = anonymous.post(login, headers=headers, json=credentials)
        check_response(logged, login, 200)
        cookie_headers = logged.headers.get_list("set-cookie")
        assert any(
            "sessionid=" in line and "HttpOnly" in line and "SameSite=Lax" in line
            for line in cookie_headers
        )
        assert anonymous.cookies.get("csrftoken") != csrf_secret
        stale = anonymous.post(PREFIX + "auth/logout/", headers=headers)
        check_response(stale, PREFIX + "auth/logout/", 403, code="csrf_failed")
        check_response(
            signed.get(DETAIL, params={"scope": "synthetic", "mode": "unrecognized"}),
            DETAIL,
            400,
            code="invalid_request",
        )
        check_response(signed.put(ME), ME, 405, method="get", code="method_not_allowed")
        raw = signed.patch(ME, content="plain", headers={"Content-Type": "text/plain"})
        check_response(raw, ME, 415, code="unsupported_media_type")
        oversized = signed.patch(ME, json={"first_name": "F" * (16 * 1024)})
        check_response(oversized, ME, 400, code="invalid_request", framework_error=True)
        assert signed.options(ME).status_code == 200
        assert {part.strip() for part in signed.options(ME).headers["allow"].split(",")} == {
            "GET",
            "PATCH",
            "OPTIONS",
        }
        assert signed.head(ME).status_code == 405
        check_response(signed.options(csrf_path), csrf_path, 405, method="get")
        assert signed.get(PREFIX + "missing-path/").headers["content-type"].startswith("text/html")
        assert signed.get("/admin/").status_code == 302
        assert signed.get("/health/ready/").json() == {"web": "ok", "pg": "ok"}
        patched = check_response(signed.patch(ME, json={"first_name": "测试"}), ME, 200)
        assert patched["first_name"] == "测试"
        password = PREFIX + "auth/password/"
        body = {"old_password": "wrong-password", "new_password": uuid4().hex}
        check_response(signed.post(password, json=body), password, 403, code="invalid_credentials")
        body["old_password"] = "s05-test-only"
        check_response(signed.post(password, json=body), password, 204)
        check_response(signed.get(ME), ME, 403, code="not_authenticated")
        cache.set("s01:login:127.0.0.1", 12, 60)
        renewed = check_response(anonymous.get(csrf_path), csrf_path, 200)["csrf_token"]
        check_response(
            anonymous.post(login, headers={"X-CSRFToken": renewed}, json=credentials),
            login,
            429,
            code="rate_limited",
        )


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (OperationalError, 503, "database_unavailable"),
        (RuntimeError, 500, None),
    ],
)
def test_injected_database_and_unexpected_error(django_user_model, failure, status, code):
    reader = user_model(django_user_model, "contract-error")

    armed = False

    def inject(sql):
        # Arm after login to exercise the authenticated read boundary itself.
        if armed and 'FROM "auth_user"' in sql:
            raise failure("disposable I boundary check")

    with loopback(reader, before_sql=inject) as (web, _):
        armed = True
        check_response(web.get(ME), ME, status, code=code)


def test_pair_method_responses_decode_only_their_own_projection(django_user_model):
    from s07_contract import operation, schema_errors
    from test_s05_selection import parent

    curator = django_user_model.objects.create_superuser("schema-pairs-curator")
    reader = user_model(django_user_model, "schema-pairs-reader")
    observations = product("schema-pairs", "C")
    a, b = city(observations, "101"), city(observations, "102")
    authorize(curator, observations)
    ingest(
        curator,
        observations,
        [
            candidate(observations, "101", value=1),
            candidate(observations, "102", value=2),
            candidate(observations, "101", indicator="PM2.5", unit="µg/m³", value=3),
        ],
    )
    with loopback(reader) as (web, _):
        compare = check_response(
            web.post(COMPARE, json=parent(observations, [a, b], method="compare", hours=1)),
            COMPARE,
            200,
        )
        assert compare["P"] == 1 and compare["paired_points"][0]["difference"] == -1
        pearson = check_response(
            web.post(
                STATISTICS,
                json=parent(
                    observations, [a], method="pearson", indicators=("CO", "PM2.5"), hours=1
                ),
            ),
            STATISTICS,
            200,
        )
        assert pearson["P"] == 1 and pearson["pearson"] is None
        assert pearson["pearson_pairs"]["shown"] == 1
        schema = operation(STATISTICS, "post")["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert schema_errors(compare, schema)
        assert schema_errors(pearson | {"pearson": "0.0"}, schema)
        assert schema_errors({k: v for k, v in pearson.items() if k != "P"}, schema)
        assert schema_errors(pearson | {"paired_points": []}, schema)


@pytest.mark.parametrize("scope", ["formal", "synthetic"])
def test_quality_evidence_urls_match_declared_scope_and_return_original_key(
    django_user_model, scope
):
    from urllib.parse import parse_qs, urlsplit

    from observations.models import Product, QualityIssue
    from s05_support import grant
    from s07_contract import dereference, operation, validate_schema

    reviewer = django_user_model.objects.create_superuser(
        "schema-evidence-reviewer", password="s05-test-only"
    )
    source = product("schema-evidence", "C")
    city(source)
    authorize(reviewer, source)
    grant(reviewer, source, "review")
    for value in [1, 2]:
        ingest(reviewer, source, [candidate(source, value=value, token=1)])
    issue = QualityIssue.objects.get(product=source, code="same_version_conflict")
    if scope == "formal":
        # Exercise the formal HTTP branch on a disposable product of local F origin.
        Product.objects.filter(pk=source.pk).update(synthetic=False, family="C")

    evidence = PREFIX + "admin/observation-evidence/"
    detail = PREFIX + "admin/quality-issues/{event_id}/"
    parameters = [dereference(p) for p in operation(evidence, "get")["parameters"]]
    schemas = {p["name"]: p["schema"] for p in parameters if p["in"] == "query"}
    with loopback(reviewer) as (web, _):
        response = check_response(web.get(detail.format(event_id=issue.pk)), detail, 200)
        links = response["evidence_links"]
        assert set(links) == {"key", "batch"}
        for kind, href in links.items():
            parts = urlsplit(href)
            assert not parts.scheme and not parts.netloc and parts.path == evidence
            query = parse_qs(parts.query, strict_parsing=True)
            selector, selected = (
                ("city_key", issue.city_key_id) if kind == "key" else ("batch", issue.batch_id)
            )
            assert query == {
                "product": [str(source.pk)],
                "scope": [scope],
                selector: [str(selected)],
            }
            for name, (value,) in query.items():
                schema = schemas[name]
                validate_schema(int(value) if schema["type"] == "integer" else value, schema)
            body = check_response(web.get(href), evidence, 200)
            assert body["synthetic"] is (scope == "synthetic")
            if kind == "key":
                assert body["disputed"] and "same_version_conflict" in body["issues"]
            else:
                assert any(row["key_id"] == issue.city_key_id for row in body["receipts"])
