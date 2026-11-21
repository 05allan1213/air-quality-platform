"""Offline reader for the frozen UCI 501 archive and S02 selection."""

import csv
import hashlib
import stat
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import BinaryIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from .normalization import (
    CONCENTRATIONS,
    BatchContext,
    Normalized,
    ProductProfile,
    Rejected,
    normalize,
)

PROFILE = "uci501-edges-v1"
SOURCE = "uci.501"
PRODUCT = "uci501"
UCI_URL = "https://archive.ics.uci.edu/dataset/501/beijing+multi+site+air+quality+data"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Chen, S. (2017). Beijing Multi-Site Air Quality [Dataset]. UCI Machine Learning Repository. "
    "https://doi.org/10.24432/C5RK5G. CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). "
    "Selected stations and hourly labels, CO converted from µg/m³ to mg/m³."
)
SHA256 = "d1b9261c54132f04c374f762f1e5e512af19f95c95fd6bfa1e8ac7e927e3b0b8"
ARCHIVE_BYTES = 7_959_991
EXPANDED_BYTES = 32_508_303
MAX_ARCHIVE_BYTES = 9 * 1024**2
MAX_EXPANDED_BYTES = 40 * 1024**2
MAX_MEMBER_BYTES = 4 * 1024**2
MAX_LINE_BYTES = 1024
MAX_FIELD_CHARS = 64
SUFFIX = "20130301-20170228"
DIRECTORY = f"PRSA_Data_{SUFFIX}/"
STATIONS = (
    "Aotizhongxin",
    "Changping",
    "Dingling",
    "Dongsi",
    "Guanyuan",
    "Gucheng",
    "Huairou",
    "Nongzhanguan",
    "Shunyi",
    "Tiantan",
    "Wanliu",
    "Wanshouxigong",
)
SELECTED_STATIONS = ("Aotizhongxin", "Changping")
START = datetime(2013, 3, 1)
END = datetime(2017, 3, 1)
HOURS_PER_STATION = 35_064
SELECTED_WINDOWS = (
    (START, datetime(2013, 3, 3)),
    (datetime(2017, 2, 27), END),
)
HEADER = (
    "No",
    "year",
    "month",
    "day",
    "hour",
    *CONCENTRATIONS,
    "TEMP",
    "PRES",
    "DEWP",
    "RAIN",
    "wd",
    "WSPM",
    "station",
)
NORMALIZATION = ProductProfile(
    "H",
    "Asia/Shanghai",
    "timezone_assumed",
    "hourly_label_unspecified",
    rule_version="uci501.v1",
    missing_markers=("NA",),
    allow_naive_label=True,
)


@dataclass(frozen=True)
class Selection:
    digest: str
    rows: tuple[Normalized, ...]
    stations_checked: int
    source_rows_checked: int
    expanded_bytes: int


def validate_archive(stream: BinaryIO) -> str:
    """Verify the exact official release on a seekable, task-controlled stream."""
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(64 * 1024):
        size += len(block)
        if size > MAX_ARCHIVE_BYTES:
            raise Rejected("archive_size_limit")
        digest.update(block)
    result = digest.hexdigest()
    if result != SHA256 or size != ARCHIVE_BYTES:
        raise Rejected("archive_fingerprint_mismatch")
    stream.seek(0)
    return result


def member_name(station: str) -> str:
    return f"{DIRECTORY}PRSA_Data_{station}_{SUFFIX}.csv"


def _check_members(archive: ZipFile) -> dict[str, ZipInfo]:
    expected = {DIRECTORY: None, **{member_name(station): station for station in STATIONS}}
    members = {}
    declared = 0
    for info in archive.infolist():
        name = info.filename
        if name in members:
            raise Rejected("duplicate_archive_member")
        if name != info.orig_filename or name not in expected:
            raise Rejected("invalid_archive_member")
        if info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise Rejected("unsupported_archive_member")
        mode = stat.S_IFMT(info.external_attr >> 16)
        if name == DIRECTORY:
            if info.file_size or mode not in {0, stat.S_IFDIR}:
                raise Rejected("invalid_archive_member")
        elif mode not in {0, stat.S_IFREG}:
            raise Rejected("invalid_archive_member")
        declared += info.file_size
        if info.file_size > MAX_MEMBER_BYTES or declared > MAX_EXPANDED_BYTES:
            raise Rejected("expanded_size_limit")
        members[name] = info
    if members.keys() != expected.keys():
        raise Rejected("missing_archive_member")
    return members


def _fields(line: bytes) -> tuple[str, ...]:
    if len(line) > MAX_LINE_BYTES:
        raise Rejected("csv_line_limit")
    try:
        result = tuple(next(csv.reader([line.decode("utf-8")], strict=True)))
    except (csv.Error, UnicodeError, StopIteration) as exc:
        raise Rejected("invalid_csv") from exc
    if len(result) != len(HEADER) or any(
        len(field) > MAX_FIELD_CHARS or (field and not field.isprintable()) for field in result
    ):
        raise Rejected("invalid_csv_fields")
    return result


def _hour(fields: tuple[str, ...], offset: int) -> datetime:
    parts = fields[1:5]
    if any(not text.isascii() or not text.isdecimal() or len(text) > 4 for text in parts):
        raise Rejected("invalid_csv_hour")
    try:
        year, month, day, hour = (int(text) for text in parts)
        stamp = datetime(year, month, day, hour)
    except ValueError as exc:
        raise Rejected("invalid_csv_hour") from exc
    if stamp != START + timedelta(hours=offset):
        raise Rejected("invalid_csv_hour_sequence")
    return stamp


def _worker_rows(
    archive: ZipFile, info: ZipInfo, station: str, ctx: BatchContext
) -> tuple[list[Normalized], int, int]:
    selected: list[Normalized] = []
    count = 0
    consumed = 0
    with archive.open(info) as member:
        head = member.readline(MAX_LINE_BYTES + 1)
        consumed += len(head)
        if _fields(head) != HEADER:
            raise Rejected("invalid_csv_header")
        while line := member.readline(MAX_LINE_BYTES + 1):
            consumed += len(line)
            if consumed > MAX_MEMBER_BYTES:
                raise Rejected("expanded_size_limit")
            if count >= HOURS_PER_STATION:
                raise Rejected("csv_row_limit")
            fields = _fields(line)
            if fields[-1] != station:
                raise Rejected("invalid_csv_station")
            if fields[0] != str(count + 1):
                raise Rejected("invalid_csv_row_number")
            timestamp = _hour(fields, count)
            count += 1
            if station not in SELECTED_STATIONS or not any(
                lower <= timestamp < upper for lower, upper in SELECTED_WINDOWS
            ):
                continue
            for indicator, value in zip(CONCENTRATIONS, fields[5:11], strict=True):
                selected.append(
                    normalize(
                        NORMALIZATION,
                        {
                            "object_code": station,
                            "source_time": timestamp.isoformat(),
                            "indicator": indicator,
                            "raw_unit": "µg/m³",
                            "raw_value": value,
                            "row_locator": f"{station}:{count}:{indicator}",
                        },
                        ctx,
                    )
                )
    if count != HOURS_PER_STATION or consumed != info.file_size:
        raise Rejected("incomplete_csv_member")
    return selected, count, consumed


def read_archive(stream: BinaryIO, *, fetched_at: datetime, profile: str = PROFILE) -> Selection:
    """Check the complete archive in memory-bounded streams and return only the selected data."""
    if profile != PROFILE:
        raise Rejected("invalid_profile")
    digest = validate_archive(stream)
    return _read_frozen_archive(stream, BatchContext(fetched_at), digest)


def _read_frozen_archive(stream: BinaryIO, ctx: BatchContext, digest: str) -> Selection:
    """Parse after identity validation. Test-only synthetic streams exercise rejection branches."""
    selected: list[Normalized] = []
    checked = 0
    expanded = 0
    try:
        with ZipFile(stream) as archive:
            members = _check_members(archive)
            for station in STATIONS:
                data, count, consumed = _worker_rows(
                    archive, members[member_name(station)], station, ctx
                )
                expanded += consumed
                if expanded > MAX_EXPANDED_BYTES:
                    raise Rejected("expanded_size_limit")
                checked += count
                selected.extend(data)
    except (BadZipFile, EOFError, NotImplementedError, RuntimeError, zlib.error) as exc:
        raise Rejected("invalid_zip") from exc
    if expanded != EXPANDED_BYTES:
        raise Rejected("expanded_size_mismatch")
    return Selection(digest, tuple(selected), len(STATIONS), checked, expanded)
