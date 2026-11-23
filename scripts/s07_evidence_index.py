"""Recompute the public inventory from selected local F/I receipts. Never copy payloads."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "docs/verification/S07_evidence.args"
RECEIPT = re.compile(
    r"(?:s01-evidence/s01v[a-f0-9]{12}/s07-legacy\.json|"
    r"(?:s06|s07)-evidence/(?:s06-|s07a-|s07-b-|s07-static-)[a-f0-9]{12}/"
    r"(?:V(?:01|02|03|12|14)/|performance/)?[a-zA-Z0-9-]+\.json)"
)
NODE = re.compile(r"tests/[\w./-]+\.py::[\w.:-]+(?:\[[^\r\n/]{1,200}\])?")


def file_data(relative):
    path = ROOT / "tmp" / relative
    if (
        path.resolve(strict=True) != path
        or not path.is_file()
        or path.stat().st_size > 24 * 1024**2
    ):
        raise ValueError(f"invalid receipt {relative}")
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def inventory(inputs):
    rows = []
    for line in inputs.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        cases, label, relative = line.split()
        if not (
            all(re.fullmatch(r"V(?:0[1-9]|1[0-7])", v) for v in cases.split(","))
            and re.fullmatch("[a-z0-9_-]+", label)
            and RECEIPT.fullmatch(relative)
        ):
            raise ValueError(f"invalid selection {line}")
        data, digest = file_data(relative)
        suite = data.get("suite", data)
        checks = suite.get("counts") or suite.get("summary", {})
        status = data.get("status") or data.get("result")
        if data.get("guard") == "PASS" and not status:
            status = data["guard"]
        if status is None and "exit_code" in suite:
            status = "PASS" if suite["exit_code"] == 0 else "FAIL"
        row = dict(cases=cases.split(","), label=label, receipt=f"tmp/{relative}", sha256=digest)
        if status in ("PASS", "FAIL", "BLOCKED", "NOT RUN"):
            row["status"] = status
        for source in ("source", "source_before", "source_sha256"):
            if re.fullmatch("[a-f0-9]{64}", str(data.get(source, ""))):
                row["source_sha256"] = data[source]
                break
        if isinstance(checks, dict) and set(checks) == {"PASS", "FAIL", "NOT RUN"}:
            row["nodes"] = checks
            nodes = suite.get("cases", [])
            actual = Counter(n["status"] for n in nodes)
            if any(actual[k] != n for k, n in checks.items()):
                raise ValueError(f"counts disagree in {relative}")
            row["not_passed"] = {
                k: [n["nodeid"] for n in nodes if n["status"] == k and NODE.fullmatch(n["nodeid"])]
                for k in ("FAIL", "NOT RUN")
                if actual[k]
            }
        rows.append(row)
    if len({r["label"] for r in rows}) != len(rows):
        raise ValueError("duplicate receipt label")
    return {
        "input": "F/I",
        "historical_csv": "The one historical FAIL is identified separately. All other receipts are from this S07 execution.",
        "receipts": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", type=Path, help="compare with the checked-in public inventory")
    args = p.parse_args()
    text = json.dumps(inventory(INPUTS), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if args.check.read_text() != text:
            raise SystemExit("S07 receipt index differs")
        print("S07 receipt index PASS")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
