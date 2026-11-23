"""V05/V07 on A. Follow published links from new accounts and independently check the F result."""

import csv
from io import StringIO

import httpx
from s06_guard import require

API = "/api/v1/"
SCOPE = {"scope": "synthetic"}


def exercise(project, apps):
    project.shell("""import sys
sys.path.insert(0,"/app/tests")
from django.contrib.auth import get_user_model
from test_s07_contract_user_http import seed_products
seed_products(get_user_model())""")
    result = {"input": "F", "run": project.name, "http": []}

    def exchange(web, method, path, expected=200, **kwargs):
        require(path.startswith(API) and not path.startswith("//"), "off_origin_link")
        response = web.request(method, path, **kwargs)
        result["http"].append([method, path.split("?")[0], response.status_code])
        require(
            response.status_code == expected,
            f"flow_http_{response.status_code}_expected_{expected}",
        )
        return response

    def register(web, name):
        auth = {"username": name, "password": project.password}
        web.headers["X-CSRFToken"] = exchange(web, "GET", API + "auth/csrf/").json()["csrf_token"]
        require(
            exchange(web, "POST", API + "auth/register/", 201, json=auth).json()["role"] == "USER",
            "new_account_role",
        )
        require(
            exchange(web, "POST", API + "auth/login/", json=auth).json()["username"] == name,
            "session_owner",
        )
        web.headers["X-CSRFToken"] = exchange(web, "GET", API + "auth/csrf/").json()["csrf_token"]
        require(exchange(web, "GET", API + "me/").json()["username"] == name, "me_owner")

    with (
        httpx.Client(base_url=apps.http, trust_env=False) as reader,
        httpx.Client(base_url=apps.http, trust_env=False) as peer,
    ):
        register(reader, "s07-f-participant")
        register(peer, "s07-f-peer")
        catalog = exchange(reader, "GET", API + "catalog/objects/", params=SCOPE).json()
        by_shape = {item["shape"]: item for item in catalog["results"]}
        require(set(by_shape) == {"L", "C", "T", "S"}, "authorized_product_set")
        (national,) = exchange(reader, "GET", by_shape["L"]["link"]).json()["results"]
        card = exchange(reader, "GET", national["links"]["city"][0]["href"]).json()
        trend = exchange(reader, "GET", card["links"]["trend"][0]["href"]).json()
        (station,) = exchange(reader, "GET", card["links"]["stations"][0]["href"]).json()["results"]
        site = exchange(reader, "GET", station["link"]).json()
        require(
            len(card["slots"]) == 7
            and len(trend["points"]) == 24
            and site["site_epoch"] == station["site_epoch"],
            "independent_products",
        )
        fav = exchange(
            reader,
            "POST",
            API + "me/favorites/",
            201,
            params=SCOPE,
            json={"kind": "city", "target_id": card["city"], "product": card["product"]},
        ).json()
        fav_path = API + f"me/favorites/{fav['id']}/"
        require(
            exchange(reader, "GET", fav_path, params=SCOPE).json()["id"] == fav["id"],
            "favorite_revisit",
        )
        require(
            exchange(peer, "GET", fav_path, 404, params=SCOPE).json() == {"code": "not_found"},
            "private_favorite",
        )
        from datetime import datetime, timedelta

        last = datetime.fromisoformat(by_shape["C"]["observed_at"])
        query = {
            "selection": SCOPE
            | {
                "source": card["source"],
                "product": card["product"],
                "cities": [card["city"]],
                "indicators": ["CO"],
                "units": {"CO": "mg/m³"},
                "standard_series": {"CO": "not_applicable"},
                "report_kind": "hourly",
                "window_definition": "hourly_label_unspecified",
                "start": (last - timedelta(hours=2)).isoformat(),
                "end": (last + timedelta(hours=1)).isoformat(),
            },
            "grain": "day",
            "method": "statistics",
        }
        calculated = exchange(reader, "POST", API + "analysis/statistics/", json=query).json()
        group = query | {"input_version": calculated["input_version"]}
        (column,) = calculated["columns"]
        sql = project.sql(f"""SELECT count(*),avg(r.value)::numeric(12,6) FROM observations_cityrevision r
            JOIN observations_cityobservation k ON k.id=r.key_id WHERE k.product_id={int(card["product"])}
            AND k.city_id={int(card["city"])} AND k.indicator='CO' AND r.is_current AND r.quality='valid'""")
        require(
            sql == "3|11.000000"
            and (column["E"], column["O"], column["V"]) == (3, 3, 3)
            and float(calculated["whole_period_mean"]) == 11,
            "http_pg_statistics",
        )
        observed, cursor = [], None
        while True:
            page = exchange(
                reader,
                "POST",
                API + "analysis/details/",
                json=group | {"page_size": 2} | ({"cursor": cursor} if cursor else {}),
            ).json()
            observed.extend(row["key"] for row in page["results"])
            cursor = page["page"]["next_cursor"]
            require(len(observed) <= 3, "detail_overflow")
            if not cursor:
                break
        saved = exchange(
            reader, "POST", API + "me/saved-queries/", 201, json=query | {"title": "S07 F result"}
        ).json()
        require(
            exchange(
                reader, "POST", API + f"me/saved-queries/{saved['id']}/run/", params=SCOPE, json={}
            ).json()["state"]
            == "current",
            "saved_query_revisit",
        )
        ticket = exchange(
            reader, "POST", API + "me/exports/", 201, json={"kind": "detail", "groups": [group]}
        ).json()
        url = API + f"me/exports/{ticket['id']}/download/"
        denied = exchange(peer, "GET", url, 404, params=SCOPE)
        require(
            denied.json() == {"code": "not_found"} and "content-disposition" not in denied.headers,
            "private_ticket",
        )
        response = exchange(reader, "GET", url, params=SCOPE)
        rows = list(csv.DictReader(StringIO(response.text)))
        require(
            len(rows) == 3
            and len(set(observed)) == 3
            and [float(r["value"]) for r in rows] == [10, 11, 12],
            "csv_detail_values",
        )
        exchange(reader, "POST", API + "auth/logout/", 204)
        exchange(reader, "GET", API + "me/", 403)
        result.update(
            status="PASS",
            shapes=sorted(by_shape),
            E=3,
            O=3,
            V=3,
            mean=11,
            independent_sql=sql,
            distinct_detail_keys=3,
            csv_rows=len(rows),
        )
        return result
