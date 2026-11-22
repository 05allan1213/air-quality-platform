"""Independent CSV and PostgreSQL oracles for the frozen H research selection."""

import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from s04_checks import check, sql

STATIONS = ("Aotizhongxin", "Changping")
METRICS = ("PM2.5", "PM10", "SO2", "NO2", "CO", "O3")
ZONE = ZoneInfo("Asia/Shanghai")
STARTS = (datetime(2013, 3, 1, tzinfo=ZONE), datetime(2017, 2, 27, tzinfo=ZONE))
HOUR = timedelta(hours=1)
SPAN = 48 * HOUR
SIZE = 7_959_991
DIGEST = "d1b9261c54132f04c374f762f1e5e512af19f95c95fd6bfa1e8ac7e927e3b0b8"
DOI = "https://doi.org/10.24432/C5RK5G"
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
PROFILE = "uci501-edges-v1"


def unit(metric):
    return "mg/m³" if metric == "CO" else "µg/m³"


def csv_oracle(raw):
    values = {}
    with ZipFile(io.BytesIO(raw)) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        check(len(members) == 12 and all(i.filename.endswith(".csv") for i in members), "12 CSVs")
        for station in STATIONS:
            (member,) = [i for i in members if f"/PRSA_Data_{station}_" in i.filename]
            with archive.open(member) as source:
                for row in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8", newline="")):
                    stamp = datetime(
                        *(int(row[k]) for k in ("year", "month", "day", "hour")), tzinfo=ZONE
                    )
                    if not any(start <= stamp < start + SPAN for start in STARTS):
                        continue
                    check(row["station"] == station, "CSV reporting identity")
                    for metric, peak in zip(
                        METRICS, (1000, 2000, 2000, 2000, 60, 1200), strict=True
                    ):
                        key = station, metric, stamp
                        check(key not in values, "unique source slot")
                        text = row[metric]
                        if text == "NA":
                            values[key] = ("missing", None)
                        else:
                            value = Decimal(text) / (1000 if metric == "CO" else 1)
                            check(value.is_finite() and value >= 0, "legal source concentration")
                            values[key] = ("suspect" if value > peak else "valid", value)
    return values


def csv_summary(values, start, metric):
    # Expected labels come from the frozen research window, including absent CSV rows.
    times = [start + i * HOUR for i in range(48)]
    columns = []
    samples = []
    for station in STATIONS:
        received = {t: values[station, metric, t] for t in times if (station, metric, t) in values}
        valid = {t: v for t, (q, v) in received.items() if q == "valid"}
        samples.append(valid)
        columns.append((len(times), len(received), len(valid), sum(valid.values()) / len(valid)))
    common = samples[0].keys() & samples[1].keys()
    differences = {t: samples[0][t] - samples[1][t] for t in common}
    return columns, (len(times), len(common), sum(differences.values()) / len(common)), differences


# Read persisted keys/current revisions and their original contributing batch, without using
# the production selection, research calendar, coverage SQL, or ORM projection.
CURRENT = """
 SELECT k.object_code, k.indicator, k.observed_at_utc AS at, k.id, r.id AS revision,
        k.site_epoch_id, k.qualification_version, r.quality, r.value, r.unit, b.fetched_at
 FROM observations_stationobservation k
 JOIN observations_stationrevision r ON r.key_id=k.id AND r.is_current AND NOT r.candidate
 JOIN observations_receipt rc ON rc.station_revision_id=r.id AND rc.outcome='first'
 JOIN observations_batch b ON b.id=rc.batch_id AND b.product_id=k.product_id AND NOT b.redacted
 WHERE k.product_id=%s AND NOT k.retired AND NOT k.disputed
   AND k.standard_series='not_applicable' AND k.window_definition='hourly_label_unspecified'
   AND k.report_kind='hourly' AND k.source_local_date=(k.observed_at_utc AT TIME ZONE 'Asia/Shanghai')::date
   AND k.watermark IS NULL AND r.revision_no=1 AND r.rule_version=1
   AND r.provenance->>'source_timezone'='Asia/Shanghai' AND k.qualification_version=1
   AND r.provenance->>'source_time'=to_char(k.observed_at_utc AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD"T"HH24:MI:SS')
   AND r.provenance->>'timezone_basis'='timezone_assumed'
   AND r.provenance->>'label_position'='unspecified' AND r.provenance->>'mapping'='uci501.v1'
   AND b.profile='uci501-edges-v1' AND b.identity=%s
"""


def sql_rows(product, epochs, values):
    rows = sql(CURRENT, product, DIGEST)
    indexed = {}
    for station, metric, stamp, key, rev, epoch, version, quality, value, u, fetched in rows:
        identity = station, metric, stamp
        check(
            identity not in indexed and epoch == epochs[station] and u == unit(metric),
            "PG current identity/unit",
        )
        check(
            values.get(identity) == (quality, value), "CSV/PG current quality and normalized value"
        )
        indexed[identity] = key, rev, version, fetched
    check(set(indexed) == set(values), "CSV/PG current slot sets")
    return indexed


def sql_summary(product, start, metric):
    # Build the expected time axis independently in PostgreSQL. An explicit NA arrives but
    # contributes neither to AVG nor to the common pair at that hour.
    grid = f"""WITH current AS ({CURRENT}), hours AS (
        SELECT generate_series(%s::timestamptz,%s::timestamptz - interval '1 hour',interval '1 hour') AS at
    ), slots AS (
        SELECT h.at,s.station,k.id,CASE WHEN k.quality='valid' THEN k.value END AS value
        FROM hours h CROSS JOIN unnest(%s::text[]) s(station)
        LEFT JOIN current k ON (k.at,k.object_code,k.indicator)=(h.at,s.station,%s)
    ) """
    args = (product, DIGEST, start, start + SPAN, list(STATIONS), metric)
    columns = sql(
        grid
        + "SELECT station,count(*),count(id),count(value),avg(value) FROM slots GROUP BY station ORDER BY station",
        *args,
    )
    pair = (
        grid
        + """, pairs AS (SELECT a.at,a.value-b.value AS delta FROM slots a JOIN slots b USING(at)
                               WHERE a.station=%s AND b.station=%s) """
    )
    pairs = sql(pair + "SELECT count(*),count(delta),avg(delta) FROM pairs", *args, *STATIONS)[0]
    differences = dict(
        sql(pair + "SELECT at,delta FROM pairs WHERE delta IS NOT NULL", *args, *STATIONS)
    )
    check(tuple(c[0] for c in columns) == STATIONS, "PG column order")
    return [c[1:] for c in columns], pairs, differences
