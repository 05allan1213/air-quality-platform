import csv
import struct
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from stat import S_IFLNK
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest
from observations import import_uci as uci
from observations.normalization import BatchContext, Rejected

CTX = BatchContext(datetime(2026, 1, 2, tzinfo=UTC))


@pytest.fixture
def tiny_manifest(monkeypatch):
    """Synthetic file shapes reach checks behind the production fingerprint boundary."""
    monkeypatch.setattr(uci, "START", datetime(2000, 1, 1))
    monkeypatch.setattr(uci, "HOURS_PER_STATION", 2)
    monkeypatch.setattr(uci, "STATIONS", ("Falpha", "Fbeta"))
    monkeypatch.setattr(uci, "SELECTED_STATIONS", ("Falpha",))
    monkeypatch.setattr(uci, "SELECTED_WINDOWS", ((uci.START, uci.START + timedelta(hours=2)),))


def csv_member(station, *, variant=None):
    head = list(uci.HEADER)
    if variant == "header_missing":
        head.pop(0)
    elif variant == "header_order":
        head[1], head[2] = head[2], head[1]
    first = ["1", "2000", "1", "1", "0", "1", "2", "3", "4", "100", "6"]
    second = ["2", "2000", "1", "1", "1", "1", "2", "3", "4", "NA", "6"]
    rows = [row + ["1", "1", "1", "1", "N", "1", station] for row in (first, second)]
    if variant == "station":
        rows[0][-1] = "Ffake"
    elif variant == "duplicate_hour":
        rows[1][4] = "0"
    elif variant == "row_number":
        rows[1][0] = "1"
    elif variant == "invalid_hour":
        rows[0][4] = "24"
    elif variant == "incomplete":
        rows.pop()
    elif variant == "long_line":
        rows[0][11] = "1" * (uci.MAX_LINE_BYTES + 1)
    file = StringIO(newline="")
    csv.writer(file).writerows([head, *rows])
    return file.getvalue().encode()


def synthetic_zip(monkeypatch, *, variant=None, bad_member=None):
    data = BytesIO()
    expanded = 0
    with ZipFile(data, "w", compression=ZIP_STORED) as archive:
        archive.writestr(uci.DIRECTORY, "")
        for station in uci.STATIONS:
            name = uci.member_name(station)
            content = csv_member(station, variant=variant)
            if bad_member == "invalid_encoding":
                content = content.replace(b"Falpha", b"\xfflpha")
            elif bad_member == "bad_quotes":
                content = content.replace(b",Falpha", b',"Falpha')
            if station == uci.STATIONS[0]:
                if bad_member == "symlink":
                    entry = ZipInfo(name)
                    entry.create_system = 3
                    entry.external_attr = (S_IFLNK | 0o777) << 16
                    archive.writestr(entry, content)
                    continue
                if bad_member == "missing":
                    continue
                if bad_member == "outside":
                    name = "../outside.csv"
                if bad_member == "absolute":
                    name = "/outside.csv"
                if bad_member == "duplicate":
                    with pytest.warns(UserWarning, match="Duplicate name"):
                        archive.writestr(name, content)
                        archive.writestr(name, content)
                    continue
            archive.writestr(name, content)
            expanded += len(content)
    monkeypatch.setattr(uci, "EXPANDED_BYTES", expanded)
    data.seek(0)
    return data


def test_exact_fingerprint_and_profile_are_required_before_zip_parsing(monkeypatch):
    packet = BytesIO(b"synthetic payload")
    with pytest.raises(Rejected, match="^archive_fingerprint_mismatch$"):
        uci.read_archive(packet, fetched_at=CTX.fetched_at)
    with pytest.raises(Rejected, match="^invalid_profile$"):
        uci.read_archive(packet, fetched_at=CTX.fetched_at, profile="arbitrary-profile")
    monkeypatch.setattr(uci, "MAX_ARCHIVE_BYTES", 8)
    with pytest.raises(Rejected, match="^archive_size_limit$"):
        uci.read_archive(packet, fetched_at=CTX.fetched_at)


def test_verified_archive_streams_members_and_returns_only_the_selection(
    tiny_manifest, monkeypatch
):
    data = synthetic_zip(monkeypatch)
    digest = "verified-test-fingerprint"
    result = uci._read_frozen_archive(data, CTX, digest)
    assert result.digest == digest and result.stations_checked == 2
    assert result.source_rows_checked == 4 and len(result.rows) == 12
    assert result.expanded_bytes == uci.EXPANDED_BYTES
    assert {row.object_code for row in result.rows} == {"Falpha"}
    co = [row for row in result.rows if row.indicator == "CO"]
    assert [row.quality for row in co] == ["valid", "missing"]
    assert str(co[0].value) == "0.100"
    assert co[0].time.observed_at_utc == datetime(1999, 12, 31, 16, tzinfo=UTC)
    assert co[0].time.window_start is co[0].time.window_end is None
    assert co[0].time.timezone_basis == "timezone_assumed"


@pytest.mark.parametrize(
    ("member", "code"),
    [
        ("symlink", "invalid_archive_member"),
        ("outside", "invalid_archive_member"),
        ("absolute", "invalid_archive_member"),
        ("missing", "missing_archive_member"),
        ("duplicate", "duplicate_archive_member"),
        ("invalid_encoding", "invalid_csv"),
        ("bad_quotes", "invalid_csv"),
    ],
)
def test_archive_rejects_unsupported_members_and_invalid_csv(
    tiny_manifest, monkeypatch, member, code
):
    data = synthetic_zip(monkeypatch, bad_member=member)
    with pytest.raises(Rejected, match=f"^{code}$"):
        uci._read_frozen_archive(data, CTX, "synthetic")


@pytest.mark.parametrize(
    ("variant", "code"),
    [
        ("header_missing", "invalid_csv_fields"),
        ("header_order", "invalid_csv_header"),
        ("station", "invalid_csv_station"),
        ("duplicate_hour", "invalid_csv_hour_sequence"),
        ("row_number", "invalid_csv_row_number"),
        ("invalid_hour", "invalid_csv_hour"),
        ("incomplete", "incomplete_csv_member"),
        ("long_line", "csv_line_limit"),
    ],
)
def test_archive_rejects_invalid_semantic_structure(tiny_manifest, monkeypatch, variant, code):
    data = synthetic_zip(monkeypatch, variant=variant)
    with pytest.raises(Rejected, match=f"^{code}$"):
        uci._read_frozen_archive(data, CTX, "synthetic")


def test_archive_rejects_truncation_and_bad_crc(tiny_manifest, monkeypatch):
    blob = synthetic_zip(monkeypatch).getvalue()
    with pytest.raises(Rejected, match="^invalid_zip$"):
        uci._read_frozen_archive(BytesIO(blob[:-5]), CTX, "synthetic")
    station = uci.STATIONS[0]
    broken = blob.replace(station.encode() + b"\r\n", b"xxxxx\r\n", 1)
    assert broken != blob
    with pytest.raises(Rejected, match="^invalid_zip$"):
        uci._read_frozen_archive(BytesIO(broken), CTX, "synthetic")


def test_member_header_limit_and_actual_stream_limit(tiny_manifest, monkeypatch):
    data = synthetic_zip(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(uci, "MAX_EXPANDED_BYTES", 10)
        with pytest.raises(Rejected, match="^expanded_size_limit$"):
            uci._read_frozen_archive(data, CTX, "synthetic")

    class DeceptiveMetadata:
        def open(self, info):
            return BytesIO(csv_member("Falpha"))

    info = ZipInfo(uci.member_name("Falpha"))
    info.file_size = 1
    monkeypatch.setattr(uci, "MAX_MEMBER_BYTES", len(uci.HEADER) * 5)
    with pytest.raises(Rejected, match="^expanded_size_limit$"):
        uci._worker_rows(DeceptiveMetadata(), info, "Falpha", CTX)


def test_archive_rejects_encrypted_members(tiny_manifest, monkeypatch):
    data = bytearray(synthetic_zip(monkeypatch).getvalue())
    central_header = data.find(b"PK\x01\x02")
    assert central_header > 0
    flags = struct.unpack_from("<H", data, central_header + 8)[0]
    struct.pack_into("<H", data, central_header + 8, flags | 1)
    with pytest.raises(Rejected, match="^unsupported_archive_member$"):
        uci._read_frozen_archive(BytesIO(data), CTX, "synthetic")
