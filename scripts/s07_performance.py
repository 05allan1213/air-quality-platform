"""Same-origin HTTP load for V13. The caller owns the V01-approved instance."""

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from time import monotonic_ns
from urllib.parse import urlsplit

import httpx
from s07_performance_oracle import (
    check,
    iso,
    matched_hours,
    matches_row,
    number,
    verify_compare,
    verify_detail,
    verify_exact,
    verify_year,
)
from s07_performance_seed import COMPARE_START, USERS, guard, hour_at, setup_django

API = "/api/v1/"
DETAIL = API + "observations/query/"
COMPARE = API + "analysis/compare/"
STATISTICS = API + "analysis/statistics/"
DRILLDOWN = API + "analysis/details/"
EXPORT = API + "me/exports/"
BUDGET = {"R1": 1_000, "R2": 1_000, "R3": 2_000, "R4": 2_000}
WARMUP = 10
HOT_SAMPLES = 100
STABLE_SECONDS = 20 * 60
TIMEOUT = 20.0
NS_PER_SECOND = 10**9


@dataclass(frozen=True)
class Case:
    endpoint: str
    params: dict | None = None
    payload: dict | None = None

    def request(self, client):
        if self.payload is None:
            return client.get(self.endpoint, params=self.params)
        return client.post(self.endpoint, json=self.payload)


@dataclass
class Reader:
    name: str
    client: httpx.Client
    cases: dict
    signatures: dict


def utc_hour(h):
    return hour_at(h).isoformat().replace("+00:00", "Z")


def cases_for(manifest):
    product = manifest["cities"]["product"]
    source = manifest["cities"]["source"]
    ids = [s["id"] for s in manifest["cities"]["series"] if s["indicator"] == "CO"]
    common = {
        "product": product,
        "source": source,
        "scope": "synthetic",
        "report_kind": "hourly",
        "window_definition": "hourly_label_unspecified",
    }
    detail = common | {"cities": ids[0], "indicators": "CO", "mode": "window", "start": utc_hour(0)}
    selection = common | {
        "cities": ids,
        "indicators": ["CO"],
        "units": {"CO": "mg/m³"},
        "standard_series": {"CO": "not_applicable"},
    }
    return {
        "R1": Case(DETAIL, params=detail | {"end": utc_hour(24), "page_size": 50}),
        "R2.first": Case(DETAIL, params=detail | {"end": utc_hour(200), "page_size": 100}),
        "R3": Case(
            COMPARE,
            payload={
                "selection": selection
                | {"start": utc_hour(COMPARE_START), "end": utc_hour(COMPARE_START + 720)},
                "method": "compare",
                "grain": "day",
            },
        ),
        "R4": Case(
            STATISTICS,
            payload={
                "selection": selection
                | {"cities": ids[:1], "start": utc_hour(0), "end": utc_hour(8784)},
                "method": "statistics",
                "grain": "month",
            },
        ),
    }


def windowed(payload, *, start=None, end=None, **selection):
    if start is not None:
        selection["start"] = utc_hour(start)
    if end is not None:
        selection["end"] = utc_hour(end)
    return payload | {"selection": payload["selection"] | selection}


def successful(response, code):
    check(response.status_code == 200, f"{code}_status_{response.status_code}")
    check("no-store" in response.headers.get("cache-control", ""), f"{code}_cache_policy")
    return response.json()


@contextmanager
def login(base, manifest, password):
    parsed = urlsplit(base)
    check(
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port
        and not (
            parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ),
        "non_loopback_origin",
    )
    check(
        len(password) >= 20 and set(manifest["users"]) == set(USERS), "invalid_fixture_credentials"
    )
    client_list = []
    try:
        identities = set()
        login_records = []
        for user in USERS:
            begin = monotonic_ns()
            client = httpx.Client(
                base_url=base,
                timeout=TIMEOUT,
                trust_env=False,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=1, max_keepalive_connections=1, keepalive_expiry=30
                ),
                headers={"Accept-Encoding": "identity", "Accept": "application/json"},
            )
            client_list.append(Reader(user, client, cases_for(manifest), {}))
            token = successful(client.get(API + "auth/csrf/"), "csrf")["csrf_token"]
            signed_in = client.post(
                API + "auth/login/",
                json={"username": user, "password": password},
                headers={"X-CSRFToken": token},
            )
            check(
                signed_in.status_code == 200 and signed_in.json()["username"] == user, "login_owner"
            )
            client.headers["X-CSRFToken"] = successful(
                client.get(API + "auth/csrf/"), "csrf_rotated"
            )["csrf_token"]
            check(successful(client.get(API + "me/"), "self")["username"] == user, "session_owner")
            sid = client.cookies.get("sessionid")
            check(sid and sid not in identities, "distinct_session")
            identities.add(sid)
            login_records.append(
                {
                    "user": user,
                    "authenticated": True,
                    "login_validation_ms": (monotonic_ns() - begin) / 1e6,
                }
            )
        yield client_list, login_records
    finally:
        for reader in client_list:
            reader.client.close()


def compact(payload):
    """Normalize moving HTTP-only evaluation clocks and signed cursor timestamps."""
    if isinstance(payload, dict):
        return {
            key: (bool(value) if key == "next_cursor" else compact(value))
            for key, value in payload.items()
            if key not in {"evaluated_at", "age_seconds"}
        }
    if isinstance(payload, list):
        return [compact(item) for item in payload]
    return payload


def digest(payload):
    stable = json.dumps(compact(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(stable.encode()).hexdigest()


def simple_request(reader, shape):
    return successful(reader.cases[shape].request(reader.client), shape)


def parent_request(reader, route, payload):
    return successful(reader.client.post(route, json=payload), "parent")


def verify_drilldown(reader, parent, version, rows):
    _, paired = matched_hours(rows)
    base = parent | {"input_version": version, "page_size": 100}
    taken = {}
    for label, expected in (
        ("observed", [row for row in rows if row["key"] is not None]),
        ("effective", [row for row in rows if row["quality"] == "valid"]),
        ("paired", [row for row in rows if row["at"] in paired]),
    ):
        request = base | {"support": label}
        count = 0
        cursor = None
        seen = set()
        while True:
            group = parent_request(
                reader, DRILLDOWN, request | ({"cursor": cursor} if cursor else {})
            )
            check(group["input_version"] == version, "detail_version")
            page = group["page"]
            check(
                page["page_size"] == 100 and 0 < len(group["results"]) <= 100, "support_count_page"
            )
            for row in group["results"]:
                check(
                    count < len(expected) and row["pos"] == expected[count]["pos"], "support_order"
                )
                matches_row(row, expected[count])
                count += 1
            cursor = page["next_cursor"]
            if not cursor:
                break
            check(cursor not in seen and count < len(expected), "support_cursor")
            seen.add(cursor)
        check(count == len(expected), "support_count_total")
        taken[label] = {"rows": count, "pages": len(seen) + 1}
    return taken


def verify_export(reader, parent, version, rows):
    preparation = reader.client.post(
        EXPORT, json={"kind": "detail", "groups": [parent | {"input_version": version}]}
    )
    check(preparation.status_code == 201, f"compare_csv_prepare_status_{preparation.status_code}")
    ticket = preparation.json()["id"]
    download = reader.client.get(EXPORT + ticket + "/download/", params={"scope": "synthetic"})
    check(download.status_code == 200, f"compare_csv_download_status_{download.status_code}")
    check(download.headers.get("content-type", "").startswith("text/csv"), "compare_csv_media")
    check(0 < len(download.content) <= 4 * 1024 * 1024, "compare_csv_size")
    parsed = csv.DictReader(StringIO(download.content.decode(), newline=""))
    expected = [cell for cell in rows if cell["key"] is not None]
    seen = 0
    for entry in parsed:
        check(seen < len(expected), "compare_csv_extra_row")
        cell = expected[seen]
        check(
            (int(entry["key"]), int(entry["revision"]), int(entry["entity"]))
            == (cell["key"], cell["revision"], cell["entity"]),
            "compare_csv_identity",
        )
        check(
            iso(entry["observed_at"]) == cell["at"]
            and iso(entry["fetched_at"]) == cell["fetched_at"]
            and entry["input_version"] == version
            and entry["quality"] == cell["quality"]
            and entry["unit"] == cell["unit"]
            and entry["standard_series"] == cell["standard_series"]
            and number(entry["value"] or None) == number(cell["value"]),
            "compare_csv_content",
        )
        seen += 1
    check(seen == len(expected), "compare_csv_missing_row")
    return {
        "prepare": 201,
        "download": 200,
        "rows": seen,
        "bytes": len(download.content),
        "f_sha256": hashlib.sha256(download.content).hexdigest(),
    }


def boundary_cases(reader, grid):
    compare = reader.cases["R3"].payload
    year = reader.cases["R4"].payload
    check(compare is not None and year is not None, "missing_payload")
    result = {
        "annual_365": verify_year(
            parent_request(reader, STATISTICS, windowed(year, end=8760)), grid["year"][:8760]
        )
    }
    sixth = max(compare["selection"]["cities"]) + 1
    variations = (
        (
            "six_objects",
            Case(
                COMPARE, payload=windowed(compare, cities=compare["selection"]["cities"] + [sixth])
            ),
        ),
        ("nonannual_31_days", Case(COMPARE, payload=windowed(compare, end=COMPARE_START + 744))),
        ("annual_367_days", Case(STATISTICS, payload=windowed(year, end=8808))),
        ("page_101", Case(DETAIL, params=reader.cases["R1"].params | {"page_size": 101})),
    )
    for label, variant in variations:
        response = variant.request(reader.client)
        check(response.status_code == 400 and response.json() == {"code": "query_too_large"}, label)
        result[label] = {"status": response.status_code, "code": "query_too_large"}
    return result


def verify(readers, manifest, sql):
    check(
        sql["run"] == manifest["run"] and sql["f_fingerprint"] == manifest["data_fingerprint"],
        "oracle_identity",
    )
    begin = monotonic_ns()
    grid = sql["grid"]
    results = {}
    for reader in readers:
        r1 = simple_request(reader, "R1")
        verify_detail(r1, list(reversed(grid["detail"][:24])), page_size=50, next_page=False)
        first = simple_request(reader, "R2.first")
        verify_detail(first, list(reversed(grid["detail"][100:200])), page_size=100, next_page=True)
        reader.cases["R2.next"] = Case(
            DETAIL,
            params=reader.cases["R2.first"].params | {"cursor": first["page"]["next_cursor"]},
        )
        second = simple_request(reader, "R2.next")
        verify_detail(second, list(reversed(grid["detail"][:100])), page_size=100, next_page=False)
        r3 = simple_request(reader, "R3")
        r4 = simple_request(reader, "R4")
        results[reader.name] = {
            "R1": 24,
            "R2_first": 100,
            "R2_next": 100,
            "R3": verify_compare(r3, grid["compare"]),
            "R4": verify_year(r4, grid["year"]),
        }
        reader.signatures.update(
            {
                k: digest(v)
                for k, v in {
                    "R1": r1,
                    "R2.first": first,
                    "R2.next": second,
                    "R3": r3,
                    "R4": r4,
                }.items()
            }
        )
    reader = readers[0]
    base = reader.cases["R3"].payload
    check(base is not None, "missing_compare")
    # This 120-hour window includes peaks, missing values and true gaps.
    start = COMPARE_START + 202
    exact = parent_request(reader, COMPARE, windowed(base, start=start, end=start + 120))
    exact_count = verify_exact(exact["chart"], grid["compare"][202 * 5 : 322 * 5])
    report = simple_request(reader, "R3")
    support = verify_drilldown(reader, base, report["input_version"], grid["compare"])
    export = verify_export(reader, base, report["input_version"], grid["compare"])
    return {
        "users": results,
        "R3_exact_points": exact_count,
        "R3_details": support,
        "R3_csv": export,
        "boundaries": boundary_cases(reader, grid),
        "elapsed_s": (monotonic_ns() - begin) / NS_PER_SECOND,
    }


def probe(reader, page, ordinal):
    response = None
    start = end = monotonic_ns()
    error = None
    status = code = None
    size, encoding, protocol = 0, None, None
    try:
        response = reader.cases[page].request(reader.client)
        end = monotonic_ns()
        status, size = response.status_code, len(response.content)
        encoding = response.headers.get("content-encoding")
        protocol = response.http_version
        if status != 200:
            error = "http_status"
            try:
                candidate = response.json().get("code")
                if (
                    isinstance(candidate, str)
                    and candidate.isascii()
                    and candidate.replace("_", "").isalnum()
                    and len(candidate) <= 48
                ):
                    code = candidate
            except (ValueError, AttributeError):
                pass
        elif "no-store" not in response.headers.get("cache-control", ""):
            error = "cache_policy"
        elif encoding not in (None, "identity"):
            error = "unexpected_compression"
        elif digest(response.json()) != reader.signatures[page]:
            error = "unexpected_content"
    except (httpx.HTTPError, ValueError) as exc:
        end = monotonic_ns() if response is None else end
        error = type(exc).__name__
    return {
        "user": reader.name,
        "layer": page,
        "ordinal": ordinal,
        "start_ns": start,
        "end_ns": end,
        "status": status,
        "error": error,
        "code": code,
        "bytes": size,
        "encoding": encoding,
        "protocol": protocol,
    }


def duration_ms(sample):
    return (sample["end_ns"] - sample["start_ns"]) / 1e6


def quantile(matrix, q):
    return matrix[math.ceil(q * len(matrix)) - 1] if matrix else None


def stats(samples):
    ordered = sorted(duration_ms(entry) for entry in samples)
    errors = Counter(s["error"] for s in samples if s["error"] is not None)
    return {
        "N": len(ordered),
        "P50_ms": quantile(ordered, 0.50),
        "P95_ms": quantile(ordered, 0.95),
        "P99_ms": quantile(ordered, 0.99),
        "max_ms": ordered[-1] if ordered else None,
        "correct": len(ordered) - errors.total(),
        "errors": errors,
        "error_rate": errors.total() / len(ordered) if ordered else None,
    }


def overlap(samples):
    sweep = sorted(
        (t, change)
        for sample in samples
        for t, change in ((sample["start_ns"], 1), (sample["end_ns"], -1))
    )
    time_by_concurrency = Counter()
    concurrent = 0
    previous = sweep[0][0]
    for current, change in sweep:
        if current != previous:
            time_by_concurrency[concurrent] += current - previous
        concurrent += change
        previous = current
    return {
        "max": max(time_by_concurrency),
        "ms_at_concurrency": {
            str(key): value / 1e6 for key, value in sorted(time_by_concurrency.items())
        },
    }


def summary(samples):
    start = min(row["start_ns"] for row in samples)
    end = max(row["end_ns"] for row in samples)
    by_user, by_layer = defaultdict(list), defaultdict(list)
    for sample in samples:
        by_user[sample["user"]].append(sample)
        by_layer[sample["layer"]].append(sample)
    return stats(samples) | {
        "elapsed_s": (end - start) / NS_PER_SECOND,
        "requests_per_second": len(samples) / ((end - start) / NS_PER_SECOND),
        "overlap": overlap(samples),
        "users": {
            name: stats(rows) | {"start_ns": rows[0]["start_ns"], "end_ns": rows[-1]["end_ns"]}
            for name, rows in sorted(by_user.items())
        },
        "layers": {name: stats(rows) for name, rows in sorted(by_layer.items())},
    }


def layer(shape, ordinal):
    return ("R2.first" if ordinal % 2 == 0 else "R2.next") if shape == "R2" else shape


def batch(readers, shape, *, n, warm=WARMUP, cold=False):
    check(len(readers) == 5, "session_count")
    go = threading.Barrier(5, timeout=240)

    def lane(reader):
        go.wait()
        warmup = [probe(reader, layer(shape, i), i) for i in range(warm)]
        go.wait()
        samples = [
            probe(reader, "R2.first" if cold and shape == "R2" else layer(shape, i), i)
            for i in range(n)
        ]
        return warmup, samples

    with ThreadPoolExecutor(max_workers=5) as pool:
        paired = list(pool.map(lane, readers))
    warmup = [sample for group, _ in paired for sample in group]
    samples = [sample for _, group in paired for sample in group]
    aggregated = summary(samples)
    return {
        "shape": shape,
        "warmup": warmup,
        "samples": samples,
        "summary": aggregated,
        "budget_ms": None if cold else BUDGET[shape],
        "status": "PASS"
        if (cold or aggregated["P95_ms"] <= BUDGET[shape])
        and aggregated["overlap"]["max"] == 5
        and not any(s["error"] for s in warmup + samples)
        else "FAIL",
    }


def stability(readers, *, seconds=STABLE_SECONDS):
    go = threading.Barrier(5, timeout=30)
    begin = monotonic_ns() + NS_PER_SECOND
    end = begin + seconds * NS_PER_SECOND
    rotate = 15 * NS_PER_SECOND
    keys = list(BUDGET)

    def lane(reader):
        from time import sleep

        go.wait()
        sleep(max(0, begin - monotonic_ns()) / NS_PER_SECOND)
        samples = []
        while (now := monotonic_ns()) < end:
            scene = (now - begin) // rotate
            shape = keys[scene % len(keys)]
            entry = probe(reader, layer(shape, len(samples)), len(samples))
            entry["rotation"] = scene
            samples.append(entry)
        return samples

    with ThreadPoolExecutor(max_workers=5) as pool:
        samples = [sample for items in pool.map(lane, readers) for sample in items]
    rotations = defaultdict(list)
    for entry in samples:
        rotations[entry["rotation"]].append(entry)
    measured = {}
    for shape in BUDGET:
        shape_samples = [entry for entry in samples if entry["layer"].startswith(shape)]
        if shape_samples:
            measured[shape] = stats(shape_samples)
    return {
        "requested_s": seconds,
        "summary": summary(samples),
        "shapes": measured,
        "rotations": {
            key: {"shape": keys[key % 4]} | summary(rows) for key, rows in sorted(rotations.items())
        },
        "samples": samples,
        "status": "PASS"
        if len(measured) == 4 and not any(sample["error"] for sample in samples)
        else "FAIL",
    }


def run(base, manifest, sql, *, mode, emit, password):
    with login(base, manifest, password) as (readers, authentication):
        emit(
            "authentication",
            {
                "users": authentication,
                "keepalive_limit": 1,
                "keepalive_expiry_s": 30,
                "request_timeout_s": TIMEOUT,
                "compression": "identity",
                "cache_policy": "no-store",
            },
        )
        emit("correctness", verify(readers, manifest, sql))
        if mode == "check":
            return
        if mode == "stable":
            emit("stability", stability(readers))
            return
        for i, shape in enumerate(BUDGET if mode != "reverse" else reversed(BUDGET)):
            full = mode != "B"
            result = batch(readers, shape, n=HOT_SAMPLES if full else 10)
            emit(f"{i + 1}-{shape}", result)


def cold_batches(readers, *, restarts, emit):
    """Measure the first five requests with the same authenticated jars after each restart."""
    batches = Counter()
    failed = []
    for shape, restart in restarts:
        check(shape in BUDGET and batches[shape] < 3, "cold_command")
        batches[shape] += 1
        step = f"cold-{shape}-{batches[shape]}"
        result = batch(readers, shape, n=1, warm=0, cold=True)
        if result["status"] != "PASS":
            failed.append(step)
        emit(step, result | {"restart": restart})
    passed = not failed and all(batches[shape] == 3 for shape in BUDGET)
    emit(
        "cold_batches",
        {"counts": batches, "failures": failed, "status": "PASS" if passed else "FAIL"},
    )
    return passed


def cold_session(base, manifest, sql, *, password, commands, emit):
    with login(base, manifest, password) as (readers, authentication):
        emit("authentication", {"users": authentication})
        emit("cold_ready", verify(readers, manifest, sql))

        def restarts():
            for line in commands:
                shape = line.strip()
                if shape == "stop":
                    break
                yield shape, None

        check(cold_batches(readers, restarts=restarts(), emit=emit), "cold_batches_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("manifest", type=Path, help="Manifest file, or '-' to read one JSON line")
    parser.add_argument("oracle", type=Path, help="SQL oracle file, or '-' to read one JSON line")
    parser.add_argument(
        "--mode", choices=("A", "B", "reverse", "stable", "check", "cold"), required=True
    )
    args = parser.parse_args()
    setup_django()

    def read(path):
        return json.loads(sys.stdin.readline() if path == Path("-") else path.read_text())

    def emit(step, data):
        print(json.dumps({"step": step, "evidence": data}), flush=True)

    manifest = read(args.manifest)
    check(guard() == manifest["run"], "guarded_manifest_identity")
    sql = read(args.oracle)
    password = os.environ.get("S07_PERF_PASSWORD", "")
    if args.mode == "cold":
        cold_session(args.base, manifest, sql, password=password, commands=sys.stdin, emit=emit)
    else:
        run(args.base, manifest, sql, mode=args.mode, password=password, emit=emit)


if __name__ == "__main__":
    main()
