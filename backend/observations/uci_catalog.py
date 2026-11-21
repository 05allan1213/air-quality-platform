from datetime import UTC, date, datetime

from django.db import transaction
from psycopg.types.range import Range

from . import import_uci as uci
from .models import City, Product, SiteEpoch, Source, Station, StationIdentifier
from .policy import OPERATIONS, actor, register_grant

NAMESPACE = uci.SOURCE
DATASET = "https://doi.org/10.24432/C5RK5G"
LICENSE = uci.LICENSE_URL
ATTRIBUTION = uci.ATTRIBUTION
STATIONS = uci.SELECTED_STATIONS
WINDOWS = tuple((start.date(), end.date()) for start, end in uci.SELECTED_WINDOWS)


@transaction.atomic
def register_uci(actor_id):
    actor(actor_id, "manage_grants")
    source, _ = Source.objects.get_or_create(
        namespace=NAMESPACE, defaults={"publisher": "UCI Machine Learning Repository"}
    )
    product, created = Product.objects.get_or_create(
        source=source,
        code=uci.PRODUCT,
        defaults=dict(
            family="H",
            shape="H",
            level="station",
            source_timezone=uci.NORMALIZATION.source_timezone,
            evidence=DATASET,
            attribution=ATTRIBUTION,
        ),
    )
    if not created:
        # Rerunning setup never reopens a withdrawn grant.
        return product
    city = City.objects.create(source=source, name="Beijing", geography="beijing")
    period = Range(
        datetime(2013, 2, 28, 16, tzinfo=UTC), datetime(2017, 2, 28, 16, tzinfo=UTC), "[)"
    )
    for code in STATIONS:
        station = Station.objects.create(source=source, name=code)
        StationIdentifier.objects.create(
            source=source, station=station, code=code, active_period=period, evidence=DATASET
        )
        SiteEpoch.objects.create(
            station=station,
            city=city,
            name=code,
            active_period=period,
            location_evidence=DATASET,
            certainty="dataset_reporting_identity_only",
        )
    base = dict(
        geography="beijing",
        terms=LICENSE,
        terms_version="CC BY 4.0 / S02 local v1",
        attribution=ATTRIBUTION,
    )
    for op, fields in [
        ("acquire_file", ["archive"]),
        ("process_local", ["archive"]),
        ("retain_raw", ["archive"]),
    ]:
        register_grant(
            actor_id,
            product.pk,
            **base,
            operation=op,
            audience=OPERATIONS[op],
            data_from=date(2013, 3, 1),
            data_until=date(2017, 3, 1),
            fields=fields,
        )
    register_grant(
        actor_id,
        product.pk,
        **base,
        operation="retain_normalized",
        audience="platform",
        data_from=date(2013, 3, 1),
        data_until=date(2017, 3, 1),
        fields=["batch_identity"],
    )
    for station in STATIONS:
        for start, end in WINDOWS:
            for op, fields in [
                ("process_local", ["normalized"]),
                ("retain_normalized", ["normalized", "provenance", "raw_field"]),
                ("display", ["normalized", "provenance"]),
                ("review", ["normalized", "provenance", "raw_field"]),
            ]:
                register_grant(
                    actor_id,
                    product.pk,
                    **base,
                    operation=op,
                    audience=OPERATIONS[op],
                    object_code=station,
                    data_from=start,
                    data_until=end,
                    fields=fields,
                )
    return product
