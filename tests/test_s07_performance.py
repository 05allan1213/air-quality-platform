"""Checks of the V13 fixture design and independent measurement utilities."""

import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from s07_performance import digest, overlap, quantile  # noqa: E402
from s07_performance_oracle import Mismatch, verify_range  # noqa: E402
from s07_performance_seed import (  # noqa: E402
    COMPARE_START,
    HOURS,
    hour_at,
    series_for,
    special_rows,
)


def test_distinct_series_and_gap_replacement_match_the_volume_spec():
    for dimension, expected in (("city", 20), ("station", 80)):
        series = series_for(dimension)
        assert len(series) == len(set(series)) == expected
    special = special_rows()
    absent = {(city, hour) for city, hour, moved, _, _ in special if moved is not None}
    extras = {(city, moved) for city, _, moved, _, _ in special if moved is not None}
    assert len(absent) == len(extras) == 6
    assert Counter(city for city, _ in absent) == Counter(city for city, _ in extras)
    assert all(COMPARE_START <= hour < COMPARE_START + 720 for _, hour in absent)
    assert all(hour >= HOURS for _, hour in extras)


def test_nearest_rank_and_true_five_request_overlap():
    assert quantile(list(range(1, 501)), 0.95) == 475
    assert overlap([{"start_ns": 2 * i, "end_ns": 2 * i + 1} for i in range(5)])["max"] == 1
    assert overlap([{"start_ns": i, "end_ns": i + 9} for i in range(5)])["max"] == 5


def test_compressed_comparison_distinguishes_gap_from_present_missing():
    start = hour_at(0)
    cells = [
        {"at": hour_at(0).isoformat(), "pos": 0, "key": None, "quality": None, "value": None},
        {"at": hour_at(0).isoformat(), "pos": 1, "key": 2, "quality": "missing", "value": None},
        {"at": hour_at(1).isoformat(), "pos": 0, "key": 3, "quality": "valid", "value": 9},
        {"at": hour_at(1).isoformat(), "pos": 1, "key": 4, "quality": "valid", "value": 7},
    ]
    points = [
        {
            "at": start.isoformat(),
            "pos": 0,
            "minimum": 9,
            "maximum": 9,
            "valid_count": 1,
            "gaps": 1,
        },
        {
            "at": start.isoformat(),
            "pos": 1,
            "minimum": 7,
            "maximum": 7,
            "valid_count": 1,
            "gaps": 0,
        },
    ]
    chart = {"resolution": "range", "interval_seconds": 12960, "points": points}
    assert verify_range(chart, cells, start=start, seconds=12960) == 2
    points[1]["gaps"] = 1
    with pytest.raises(Mismatch, match="range_gap_or_valid_count"):
        verify_range(chart, cells, start=start, seconds=12960)


def test_per_request_signature_ignores_clock_and_cursor_issue_time_only():
    first = {
        "evaluated_at": "now",
        "page": {"next_cursor": "a"},
        "results": [{"age_seconds": 1, "value": 2}],
    }
    paired = {
        "evaluated_at": "later",
        "page": {"next_cursor": "b"},
        "results": [{"age_seconds": 2, "value": 2}],
    }
    assert digest(first) == digest(paired)
    paired["results"][0]["value"] = 3
    assert digest(first) != digest(paired)
