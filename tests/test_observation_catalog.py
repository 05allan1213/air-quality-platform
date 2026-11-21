from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from django.db import IntegrityError, transaction
from observations.models import City, SiteEpoch, Station, StationIdentifier
from psycopg.types.range import TimestamptzRange
from s02_support import BOUNDARY, END, START, create_product, in_db_session, wait_for_block

pytestmark = pytest.mark.django_db(transaction=True)


def test_station_codes_are_unique_per_source_period():
    product = create_product("source-a")
    other = create_product("source-b")
    station = Station.objects.create(source=product.source, name="Shared name")
    same_name = Station.objects.create(source=other.source, name="Shared name")
    replacement = Station.objects.create(source=product.source, name="Replacement")

    def identifier(station, start, end, *, code="001", kind="code"):
        return StationIdentifier.objects.create(
            source=station.source,
            station=station,
            code=code,
            kind=kind,
            active_period=TimestamptzRange(start, end),
            evidence="F identity evidence",
        )

    original = identifier(station, START, BOUNDARY)
    identifier(replacement, BOUNDARY, END)
    identifier(same_name, START, END)
    identifier(station, START, END, code="New name", kind="alias")
    with pytest.raises(IntegrityError) as duplicate, transaction.atomic():
        identifier(replacement, START, END)
    assert duplicate.value.__cause__.sqlstate == "23P01"
    original.refresh_from_db()
    assert original.code == "001"
    assert list(
        StationIdentifier.objects.filter(
            source=product.source, code="001", active_period__contains=BOUNDARY
        ).values_list("station_id", flat=True)
    ) == [replacement.pk]
    assert station.pk != same_name.pk


def test_two_postgresql_connections_cannot_commit_overlapping_site_periods():
    product = create_product()
    city = City.objects.create(source=product.source, name="One", geography="A")
    station = Station.objects.create(source=product.source, name="Station")
    pid_queue = Queue()

    def insert(period):
        return SiteEpoch.objects.create(
            station=station,
            city=city,
            active_period=period,
            name="Fixture",
            location_evidence="F",
            certainty="confirmed",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            old = insert(TimestamptzRange(START, BOUNDARY))
            overlapping = TimestamptzRange(START, END)
            future = pool.submit(in_db_session, pid_queue, lambda: insert(overlapping))
            wait_for_block(pid_queue.get(timeout=5))
        with pytest.raises(IntegrityError) as overlap:
            future.result(timeout=5)
    assert overlap.value.__cause__.sqlstate == "23P01"
    new = insert(TimestamptzRange(BOUNDARY, END))
    assert SiteEpoch.objects.get(station=station, active_period__contains=START).pk == old.pk
    assert SiteEpoch.objects.get(station=station, active_period__contains=BOUNDARY).pk == new.pk
    assert not SiteEpoch.objects.filter(station=station, active_period__contains=END).exists()


@pytest.mark.parametrize(
    "period",
    [
        TimestamptzRange(empty=True),
        TimestamptzRange(None, BOUNDARY),
        TimestamptzRange(START, BOUNDARY, "(]"),
    ],
)
def test_postgresql_rejects_noncanonical_site_period(period):
    product = create_product()
    city = City.objects.create(source=product.source, name="City", geography="A")
    station = Station.objects.create(source=product.source, name="Station")
    with pytest.raises(IntegrityError) as invalid, transaction.atomic():
        SiteEpoch.objects.create(
            station=station,
            city=city,
            active_period=period,
            name="Fixture",
            location_evidence="F",
            certainty="confirmed",
        )
    assert invalid.value.__cause__.sqlstate == "23514"


def test_postgresql_rejects_zero_city_codes_and_invalid_product_layers():
    from observations.models import CityIdentifier, Product

    product = create_product()
    city = City.objects.create(source=product.source, name="City", geography="A")
    with pytest.raises(IntegrityError) as invalid, transaction.atomic():
        CityIdentifier.objects.create(
            source=product.source,
            city=city,
            code="0",
            active_period=TimestamptzRange(START, END),
            evidence="F",
        )
    assert invalid.value.__cause__.diag.constraint_name == "city_code_nonzero"
    with pytest.raises(IntegrityError) as wrong_layer, transaction.atomic():
        Product.objects.create(source=product.source, code="h", family="H", shape="H", level="city")
    assert wrong_layer.value.__cause__.diag.constraint_name == "product_shape_level"
    with pytest.raises(IntegrityError) as unsafely_labeled, transaction.atomic():
        Product.objects.create(source=product.source, code="f", family="F", shape="C", level="city")
    assert unsafely_labeled.value.__cause__.diag.constraint_name == "product_synthetic_family"


@pytest.mark.parametrize("tier", ["city", "station"])
def test_direct_sql_rejects_invalid_report_anchors_and_duplicate_keys(tier):
    from observations.models import CityObservation, StationObservation
    from s02_support import create_site, sql_copy

    product = create_product(shape="C" if tier == "city" else "S")
    if tier == "city":
        city = City.objects.create(source=product.source, name="A", geography="A")
        model, entity = CityObservation, {"city": city}
    else:
        model, entity = StationObservation, {"site_epoch": create_site(product)}
    fields = {
        "product": product,
        "indicator": "CO",
        "object_code": "001",
        "standard_series": "not_applicable",
        "source_local_date": START.date(),
        **entity,
    }
    hourly = model.objects.create(
        **fields,
        report_kind="hourly",
        window_definition="hourly_label_unspecified",
        observed_at_utc=START,
    )
    daily = model.objects.create(
        **fields,
        report_kind="daily",
        window_definition="daily",
        local_report_date=START.date(),
    )
    for row, overrides in [
        (hourly, {"observed_at_utc": None, "local_report_date": START.date()}),
        (daily, {"observed_at_utc": START, "local_report_date": None}),
        (hourly, {"local_report_date": START.date()}),
        (hourly, {"observed_at_utc": None}),
        (hourly, {"report_kind": "unknown"}),
    ]:
        with pytest.raises(IntegrityError) as bad_anchor, transaction.atomic():
            sql_copy(row, **overrides)
        assert bad_anchor.value.__cause__.diag.constraint_name == f"{tier}observation_report_anchor"
    for row, anchor in [(hourly, "instant"), (daily, "day")]:
        with pytest.raises(IntegrityError) as duplicate, transaction.atomic():
            sql_copy(row)
        assert duplicate.value.__cause__.diag.constraint_name == f"{tier}observation_{anchor}"
    for report_kind in ("rolling_8h", "rolling_24h"):
        sql_copy(hourly, report_kind=report_kind, window_definition=report_kind)
        with pytest.raises(IntegrityError) as wrong_rolling_anchor, transaction.atomic():
            sql_copy(daily, report_kind=report_kind, window_definition=report_kind)
        assert (
            wrong_rolling_anchor.value.__cause__.diag.constraint_name
            == f"{tier}observation_report_anchor"
        )
    assert model.objects.count() == 4


def test_resolution_uses_half_open_site_period_and_rejects_a_crossing_window():
    from datetime import timedelta

    from observations.catalog import relocate, resolve
    from observations.policy import Denied
    from s02_support import create_site

    product = create_product()
    old = create_site(product)
    new = relocate(
        old.station,
        BOUNDARY,
        city=old.city,
        name="F relocated site",
        certainty="confirmed",
        location_evidence="F synthetic relocation",
    )
    assert resolve(product, "001", START) == (old, "A")
    assert resolve(product, "001", BOUNDARY) == (new, "A")
    assert resolve(product, "001", BOUNDARY + timedelta(hours=1)) == (new, "A")
    with pytest.raises(Denied, match="identity_unresolved"):
        resolve(product, "001", END)
    # A right-labelled hour ending at relocation still describes the old site.
    assert resolve(product, "001", BOUNDARY, BOUNDARY - timedelta(hours=1), BOUNDARY) == (old, "A")
    with pytest.raises(Denied, match="identity_window_crossing"):
        resolve(
            product,
            "001",
            BOUNDARY + timedelta(hours=1),
            BOUNDARY - timedelta(hours=1),
            BOUNDARY + timedelta(hours=1),
        )


def test_product_shapes_preserve_separate_values_and_standard_series(django_user_model):
    from datetime import timedelta
    from decimal import Decimal

    from observations.models import CityIdentifier, CityObservation, StationObservation
    from observations.normalization import CONCENTRATIONS
    from observations.queries import read_current
    from s02_support import allow, candidate, create_site, selector
    from s02_support import ingest_fixture as ingest_batch

    actor = django_user_model.objects.create_user("s02-shape", is_superuser=True)
    listed = create_product("listed", "L")
    city = City.objects.create(source=listed.source, geography="A", name="Synthetic city")
    CityIdentifier.objects.create(
        source=listed.source,
        city=city,
        code="001",
        active_period=TimestamptzRange(START, END),
        evidence="F city scope",
    )
    direct = create_product("direct", "C", source=listed.source)
    trend = create_product("trend", "T", source=listed.source)
    daily = create_product("daily", "D", source=listed.source)
    stations = create_product("stations", "S", source=listed.source)
    site = create_site(stations)
    for product in (listed, direct, trend, daily, stations):
        for operation in ("process_local", "retain_normalized", "display"):
            allow(actor, product, operation)

    for product, aqi in [(listed, "11"), (direct, "22"), (trend, "33"), (daily, "44")]:
        is_daily = product == daily
        window = "local_daily" if is_daily else "hourly_label_unspecified"
        aqi_row = candidate(product, 1, aqi, indicator="AQI", unit="1", window=window)
        rows = [aqi_row]
        if product == listed:
            rows.extend(
                candidate(product, 1, "2", indicator=f"{indicator}Level", unit="grade")
                for indicator in CONCENTRATIONS
            )
        if product == direct:
            rows.extend(
                candidate(product, 1, "20", indicator=indicator, unit="µg/m³")
                for indicator in CONCENTRATIONS
            )
        ingest_batch(actor.pk, product.pk, "F products", "f-product-v1", rows)
        query = selector(product, city, indicator="AQI", standard_series="unknown")
        if is_daily:
            query.pop("observed_at")
            query.update(
                report_kind="daily",
                window_definition=window,
                local_report_date=START.date().isoformat(),
            )
        (view,) = read_current(actor.pk, query)["results"]
        assert Decimal(view["value"]) == Decimal(aqi)
        assert view["grade"] is None and view["color"] == "" and not view["comparable"]

    assert CityObservation.objects.filter(product=listed).count() == 7
    assert set(
        CityObservation.objects.filter(product=direct).values_list("indicator", flat=True)
    ) == {
        "AQI",
        *CONCENTRATIONS,
    }
    assert CityObservation.objects.filter(product=trend).count() == 1
    assert CityObservation.objects.filter(product=daily).count() == 1
    ingest_batch(
        actor.pk,
        stations.pk,
        "F station",
        "f-product-v1",
        [candidate(stations, 1, "55", indicator="AQI", unit="1")],
    )
    (station_view,) = read_current(
        actor.pk, selector(stations, site, indicator="AQI", standard_series="unknown")
    )["results"]
    assert Decimal(station_view["value"]) == Decimal("55")
    (station_key,) = StationObservation.objects.all()
    assert (station_key.product_id, station_key.site_epoch_id) == (stations.pk, site.pk)
    assert station_key.observed_at_utc == START

    at = START + timedelta(hours=8)
    ingest_batch(
        actor.pk,
        direct.pk,
        "different windows",
        "f-product-v1",
        [
            candidate(direct, 1, "20", at=at, indicator="O3", unit="µg/m³"),
            candidate(
                direct,
                1,
                "21",
                at=at,
                indicator="O3",
                unit="µg/m³",
                window="rolling_8h_right_label",
            ),
        ],
    )
    for standard, raw in [("HJ633-2012", "0"), ("HJ633-2026", "1")]:
        row = candidate(
            direct,
            1,
            raw,
            indicator="AQI",
            unit="1",
            standard_series=standard,
            standard_basis="source_stated",
            standard_evidence="F test standard notice",
        )
        ingest_batch(actor.pk, direct.pk, standard, "f-product-v1", [row])
        (view,) = read_current(
            actor.pk, selector(direct, city, indicator="AQI", standard_series=standard)
        )["results"]
        assert Decimal(view["value"]) == Decimal(raw)
        assert view["grade"] == 1 and view["comparable"]
    assert CityObservation.objects.filter(product=direct, indicator="AQI").count() == 3
    assert set(
        CityObservation.objects.filter(
            product=direct, indicator="O3", observed_at_utc=at
        ).values_list("report_kind", flat=True)
    ) == {"hourly", "rolling_8h"}
