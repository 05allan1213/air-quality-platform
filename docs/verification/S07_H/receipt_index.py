"""Rebuild the public H audit projection from the specified private receipts, offline."""

import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
A = "tmp/s07-evidence/s07a-b517464d7343/"
H = "tmp/s07-h-supplement/"
B = H + "B-bw6cwibr/result.json"
R = H + "R-s06-91ab1e38511a/restore.json"
STATIONS = ("Aotizhongxin", "Changping")
BASE = "tmp/s07-handoff/1ddb3ed3bf15-xb5pscgj/S07.zip"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads((ROOT / path).read_bytes())


def pick(row, *keys):
    return {k: row[k] for k in keys}


def bind(path):
    return {"path": path, "sha256": digest(ROOT / path)}


def check(ok, label):
    if not ok:
        raise ValueError(label)


def coverage(row):
    # Only publish coverage, units, page counts and the agreement check, never the measured series.
    counts = {s: pick(row["stations"][s], "E", "O", "V") for s in STATIONS}
    for oracle in ("csv", "postgresql"):
        expected = row[oracle]
        check(pick(row, "E_common", "P") == pick(expected, "E_common", "P"), "paired coverage")
        for s, station in zip(STATIONS, expected["stations"], strict=True):
            check(counts[s] == pick(station, "E", "O", "V"), "station coverage")
        for http, reference in [
            (row["mean_difference"], expected["mean_difference"]),
            *(
                (row["stations"][s]["weighted_mean"], v["weighted_mean"])
                for s, v in zip(STATIONS, expected["stations"], strict=True)
            ),
        ]:
            check(abs(Decimal(str(http)) - Decimal(str(reference))) <= Decimal("1e-9"), "means")
    return pick(
        row, "window_start", "window_end", "indicator", "unit", "E_common", "P", "support_pages"
    ) | {
        "stations": counts,
        "csv_pg_http_agree": True,
    }


def index():
    source = json.loads((HERE / "source.json").read_bytes())
    files = {}
    for snap in source["snapshots"]:
        p = ROOT / snap["private_file"]
        check(p.stat().st_size == snap["bytes"] and digest(p) == snap["sha256"], snap["kind"])
        files[snap["kind"]] = p
    with ZipFile(files["publisher_archive"]) as z:
        inner = files["selected_inner_member"]
        check(
            z.namelist().count(inner.name) == 1 and z.read(inner.name) == inner.read_bytes(),
            "nested copy",
        )
    ap, ag, ae = (A + name for name in ("h-compose.json", "h-grants.json", "environment.json"))
    a, g, env, b, r = map(read, (ap, ag, ae, B, R))
    bs, ra = b["sandbox"], r["application"]
    check(
        a["status"]
        == env["status"]
        == b["status"]
        == bs["status"]
        == r["guard"]
        == r["result"]
        == ra["result"]
        == "PASS",
        "run status",
    )
    check(a["run"] == env["run"] and a["image"] == env["application_image"], "A environment")
    check(
        a["input_sha256"]
        == bs["archive"]["sha256"]
        == r["input_material"]["source_sha256"]
        == digest(inner),
        "input binding",
    )
    bindings = [a["replay_sha256"], b["replay_sha256"], r["replay_sources"]]
    for mapping in bindings:
        for name, value in mapping.items():
            check(
                name
                in (
                    "h_case.py",
                    "h_compose.py",
                    "h_host.py",
                    "h_restore.py",
                    "h_restore_case.py",
                    "h_restore_custody.py",
                )
                and digest(HERE / "replay" / name) == value,
                "replay source",
            )
    check(env["source_sha256"] == b["source_sha256"] == r["source_before"], "application source")
    base = bind(BASE)
    check(
        base["sha256"] == "d9c2de122bda5751070708846852ad08256877b080ad7a2906e14c7e88763075",
        "F/I package",
    )
    with ZipFile(ROOT / BASE) as zip_base:
        for member in ("S07_evidence.json", "S07_T09.json", "S07_V04.json"):
            name = "docs/verification/" + member
            check(zip_base.read(name) == (ROOT / name).read_bytes(), name)
    results = [coverage(v) for v in a["research"]["results"]]
    check(
        len({(v["indicator"], v["window_start"]) for v in results}) == 12,
        "two windows / six indicators",
    )
    co = coverage(a["cold_research"]["results"][0])
    check(co in results, "cold CO")
    for session in ("before_cold", "original_session", "new_session"):
        detail = bs[session] if session == "before_cold" else bs[session]["analysis"]
        check(coverage(detail) == co, "B cold coverage")
    cold_charts = {}
    for session in ("original_session", "new_session"):
        chart = bs[session]["chart"]
        check(
            chart
            == {
                "resolution": "exact",
                "http": 200,
                "csv_pg_http_match": True,
                "stations": {
                    station: {"points": counts["E"], "nulls": counts["E"] - counts["V"]}
                    for station, counts in co["stations"].items()
                },
            },
            f"{session} chart vs source, PG and HTTP coverage",
        )
        cold_charts[session] = chart
    g_from = [row["valid_from"] for row in g]
    (until,) = {row["valid_until"] for row in g}
    check(all(row["valid_from"] <= row["registered_at"] < until for row in g), "A grant clock")
    prior = []
    for path, reason in [
        ("tmp/s07-evidence/s07a-35af5eb1863f/h-compose.json", "old loopback address after restart"),
        (H + "B-5v38wvyy/result.json", "stale PG connection after restart"),
        (H + "R-s06-d37b08adbba8/restore.json", "custody message size"),
        (H + "R-s06-719977e9bf45/restore.json", "null filter in disposition check"),
        (H + "R-s06-0d312c2a8e60/restore.json", "stage clearance race after worker commit"),
        (
            "tmp/s07-evidence/s07a-8c2a2ba7c6dd/h-compose.json",
            "prior successful A before recording replay digests",
        ),
        (H + "B-dx0o5wdb/result.json", "prior successful B with earlier shared helper"),
        (H + "B-cynkze4u/result.json", "prior successful B before per-point cold chart checks"),
        (
            H + "R-s06-d2b4536e4ecb/restore.json",
            "post-run annotated copy; only the fresh R is used for signoff",
        ),
    ]:
        d = read(path)
        prior.append(
            bind(path)
            | {"run": d["run"], "result": d.get("result", d.get("status")), "note": reason}
        )
    return {
        "input": "H",
        "profile": "uci501-edges-v1",
        "source_record": bind(str((HERE / "source.json").relative_to(ROOT))),
        "previous_delivery": base
        | {
            "commit": b["code_commit"],
            "historical_index": bind("docs/verification/S07_evidence.json"),
            "T09": bind("docs/verification/S07_T09.json"),
            "L22": bind("docs/verification/S07_V04.json"),
        },
        "application_source_sha256": env["source_sha256"],
        "previous_runs": prior,
        "A": {
            "run": a["run"],
            "status": a["status"],
            "cases": ["V05", "V07", "V11", "V14", "T10"],
            "receipts": list(map(bind, (ap, ae, ag))),
            "replay_sha256": bindings[0],
            "registration": {
                "count": len(g),
                "operations": dict(sorted(Counter(row["operation"] for row in g).items())),
                "terms_versions": sorted({row["terms_version"] for row in g}),
                "first_valid_from": min(g_from),
                "last_valid_from": max(g_from),
                "valid_until": until,
            },
            **pick(
                a,
                "started_utc",
                "finished_utc",
                "import",
                "navigation",
                "withdrawal",
                "all_stopped",
                "network_before",
                "network_after",
                "source_access",
                "cleanup",
            ),
            "research": pick(
                a["research"],
                "keys",
                "valid",
                "missing",
                "gap",
                "export_detail",
                "export_aggregate",
                "tickets",
            )
            | {"comparisons": results},
            "cold": pick(a["cold"], "new_broker", "old_session", "new_session")
            | {"same_volume": a["cold"]["volume_before"] == a["cold"]["volume_after"], "co": co},
        },
        "B": {
            "run": b["run"],
            "status": b["status"],
            "cases": b["scope"],
            "receipts": [bind(B)],
            "replay_sha256": bindings[1],
            **pick(b, "started_utc", "finished_utc", "cleanup", "sandbox_exit"),
            **pick(
                bs,
                "use_grants",
                "database_before_cold",
                "database_after_cold",
                "redis_pushes_during_import",
                "stage_after",
                "all_stopped",
                "same_session_and_distinct_new_session",
            ),
            "same_volume": bs["volume_before"] == bs["volume_after"] == bs["volume_while_stopped"],
            "new_broker": bs["redis_before"] != bs["redis_after"],
            "before_after_external_route": [
                bs[k]["external_route"] for k in ("isolation", "isolation_after_cold")
            ],
            "original_and_new_sessions": [
                bs[k]["status"] for k in ("original_session", "new_session")
            ],
            "representative_co": co,
            "cold_charts": cold_charts,
            "denials": pick(
                bs["new_session"]["analysis"],
                "ungranted_window",
                "export_detail",
                "export_aggregate",
                "export_tickets",
            ),
        },
        "R": {
            "run": r["run"],
            "status": r["result"],
            "cases": ["V11", "V14 (segment recovery)", "T10"],
            "receipts": [bind(R)],
            "replay_sha256": bindings[2],
            **pick(
                r,
                "guard",
                "network",
                "child_exit",
                "restore_target",
                "remaining_containers",
                "runtime_root_cleared",
                "custody_media_cleared",
            ),
            **pick(
                ra,
                "initial_grants",
                "custody_mount",
                "input",
                "import",
                "published_segments",
                "restore_success",
                "withdraw_restore",
                "withdraw_backup",
                "regrant_old_segment",
                "disposition",
                "legal_sibling_restore",
                "online_collection_runs",
                "child_processes_remaining",
            ),
        },
    }


if __name__ == "__main__":
    result = json.dumps(index(), indent=2, ensure_ascii=False) + "\n"
    if sys.argv[1:] == ["--check"]:
        check((HERE / "evidence.json").read_bytes() == result.encode(), "H index differs")
        print("S07 H receipt index PASS")
    elif not sys.argv[1:]:
        print(result, end="")
    else:
        raise SystemExit("usage: receipt_index.py [--check]")
