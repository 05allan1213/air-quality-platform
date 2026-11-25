"""Measure the F national page inside s08_browser_host's guarded namespace."""

import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier
from time import perf_counter

import httpx
from django.db import connection, transaction
from django.test import Client
from s06_guard import require

PATH = "/api/v1/overview/national/"


def login(client, account, *, http=False):
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    body = {key: account[key] for key in ("username", "password")}
    payload = {"json": body} if http else {"data": body, "content_type": "application/json"}
    response = client.post("/api/v1/auth/login/", headers={"X-CSRFToken": token}, **payload)
    require(response.status_code == 200, "profile_login")


def verified(response, size, expected_ids):
    require(response.status_code == 200, "profile_national_response")
    data = response.json()
    require(
        data["synthetic"]
        and data["state"] == "ok"
        and [row["city"] for row in data["results"]] == expected_ids[:size]
        and data["summary"]["covered_city_count"] == len(expected_ids),
        "profile_national_identity",
    )
    return len(response.content)


def diagnostic(client, params, ids):
    # Keep SQL text, bindings and the complete response in memory only. The receipt
    # identifies query sites by project filename/line and stores timings and counts.
    by_site = defaultdict(list)
    slow = None

    def measure(execute, sql, binds, many, context):
        nonlocal slow
        frame = sys._getframe(1)
        while frame and "/backend/" not in frame.f_code.co_filename:
            frame = frame.f_back
        site = f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}" if frame else "framework"
        before = perf_counter()
        try:
            return execute(sql, binds, many, context)
        finally:
            ms = (perf_counter() - before) * 1_000
            by_site[site].append(ms)
            if str(sql).lstrip().upper().startswith(("SELECT", "WITH")) and (
                not slow or ms > slow[0]
            ):
                slow = ms, site, sql, binds

    start = perf_counter()
    with connection.execute_wrapper(measure):
        response = client.get(PATH, params)
    elapsed = (perf_counter() - start) * 1_000
    size = verified(response, int(params.get("page_size", 50)), ids)
    sites = sorted(by_site.items(), key=lambda pair: sum(pair[1]), reverse=True)
    output = {
        "wall_ms": round(elapsed, 2),
        "bytes": size,
        "sql_ms": round(sum(map(sum, by_site.values())), 2),
        "sql_count": sum(map(len, by_site.values())),
        "sql_sites": [
            {"site": site, "n": len(times), "ms": round(sum(times), 2)} for site, times in sites[:8]
        ],
    }
    if slow is not None:
        from s07_performance_diagnostics import sanitize_plan

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + slow[2], slow[3])
            raw = cursor.fetchone()[0]
        plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]
        output["slow_query"] = {
            "site": slow[1],
            "ms": round(slow[0], 2),
            "plan": sanitize_plan(plan["Plan"]),
            "execution_ms": plan["Execution Time"],
            "jit_ms": plan.get("JIT", {}).get("Timing", {}).get("Total"),
        }
    return output


def profile(base, seed, evidence):
    from s08_browser_host import write_seed

    require(base == "http://127.0.0.1:8000" and seed["origin"] == "F", "national_profile_scope")
    params = {"product": seed["products"]["L"], "scope": "synthetic", "at": seed["snapshot_at"]}
    home = {**params, "home": 1, "page_size": 25, "map_limit": 400, "standard_series": "HJ633-2012"}
    ids = sorted(city["id"] for city in seed["cities"])
    web = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1")
    login(web, seed["accounts"]["reader"])
    for case, conditions, rows in (
        ("legacy", params, ids),
        ("home", home, ids),
        ("filtered", {**home, "q": seed["cities"][0]["name"]}, ids[:1]),
    ):
        result = diagnostic(web, conditions, rows)
        write_seed(evidence / f"national-{case}.json", result)

    # Five authenticated sessions share one Gunicorn 1w/8t process. Each wave
    # measures the complete HTTP response before parsing and verifying its rows.
    with ExitStack() as stack, ThreadPoolExecutor(max_workers=5) as workers:
        clients = []
        for role in ("reader", "peer", "operator", "reviewer", "diagnostics"):
            client = stack.enter_context(httpx.Client(base_url=base, trust_env=False, timeout=90))
            login(client, seed["accounts"][role], http=True)
            clients.append(client)
        barrier = Barrier(5, timeout=20)

        def request(client):
            barrier.wait()
            start = perf_counter()
            response = client.get(PATH, params=home)
            elapsed = (perf_counter() - start) * 1_000
            return {"ms": round(elapsed, 2), "bytes": verified(response, 25, ids)}

        waves = [list(workers.map(request, clients)) for _ in range(2)]
        write_seed(evidence / "national-http.json", {"origin": "F", "sessions": 5, "waves": waves})
