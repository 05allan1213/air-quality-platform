"""PostgreSQL helpers used by the S02 synthetic acceptance cases."""

import time
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache

import psycopg
from django.db import connection, connections
from psycopg import sql

START = datetime(2024, 1, 1, tzinfo=UTC)
BOUNDARY = datetime(2024, 1, 2, tzinfo=UTC)
END = datetime(2024, 1, 3, tzinfo=UTC)


def observe_db():
    """Open a separate PostgreSQL connection to check committed rows."""
    return psycopg.connect(**connection.get_connection_params())


def in_db_session(pid_queue, operation):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid_queue.put(cursor.fetchone()[0])
        return operation()
    finally:
        connections.close_all()


def wait_for_block(pid, timeout=5):
    with connection.cursor() as cursor:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cursor.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", (pid,))
            if cursor.fetchone()[0]:
                return
            time.sleep(0.02)
    raise AssertionError(f"PostgreSQL session {pid} did not reach the lock")


@contextmanager
def reject_observation_write(model, event, when="TRUE"):
    """Inject an error in a test-owned database after a row is written."""
    if event not in {"INSERT", "UPDATE"}:
        raise ValueError("Unexpected trigger event")
    table = sql.Identifier(model._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION inject_s02_test_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 's02_injected_write_failure';
            END $$
        """)
        cursor.execute(
            sql.SQL(
                "CREATE TRIGGER inject_s02_test_failure AFTER {} ON {} "
                "FOR EACH ROW WHEN ({}) EXECUTE FUNCTION inject_s02_test_failure()"
            ).format(sql.SQL(event), table, sql.SQL(when))
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP TRIGGER inject_s02_test_failure ON {}").format(table))
            cursor.execute("DROP FUNCTION inject_s02_test_failure()")


def create_product(code="fixture", shape="S", source=None):
    from observations.models import Product, Source

    if source is None:
        source = Source.objects.create(
            namespace=f"test.s02.{code}", publisher="S02 synthetic tests"
        )
    return Product.objects.create(
        source=source,
        code=code,
        family="F",
        shape=shape,
        level="station" if shape == "S" else "city",
        synthetic=True,
    )


def create_site(product, code="001", geography="A"):
    from observations.models import City, SiteEpoch, Station, StationIdentifier
    from psycopg.types.range import TimestamptzRange

    city = City.objects.create(source=product.source, name=f"Test {geography}", geography=geography)
    station = Station.objects.create(source=product.source, name="Test Station")
    period = TimestamptzRange(START, END)
    StationIdentifier.objects.create(
        source=product.source, station=station, code=code, active_period=period, evidence="F"
    )
    return SiteEpoch.objects.create(
        station=station,
        city=city,
        active_period=period,
        name="F site",
        location_evidence="F",
        certainty="confirmed",
    )


def allow(actor, product, operation="retain_normalized", **overrides):
    from django.utils import timezone
    from observations.policy import register_grant

    defaults = {
        "operation": operation,
        "audience": {
            "review": "operator",
            "analyze": "operator",
            "display": "local_authenticated",
        }.get(operation, "platform"),
        "geography": "*",
        "object_code": "*",
        "indicator": "*",
        "data_from": START.date(),
        "data_until": END.date(),
        "fields": ["normalized", "provenance", "raw_field"],
        "terms": "S02 synthetic rights",
        "terms_version": "1",
        "valid_from": timezone.now(),
    }
    return register_grant(actor.pk, product.pk, **(defaults | overrides))


def sql_copy(row, **overrides):
    """Insert directly in PostgreSQL, copying all unspecified non-PK columns."""
    fields = [field for field in row._meta.concrete_fields if not field.primary_key]
    columns = [sql.Identifier(field.column) for field in fields]
    selection = [
        sql.Placeholder() if field.name in overrides else column
        for field, column in zip(fields, columns, strict=True)
    ]
    data = [overrides[field.name] for field in fields if field.name in overrides]
    table = sql.Identifier(row._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} WHERE {} = %s").format(
                table,
                sql.SQL(", ").join(columns),
                sql.SQL(", ").join(selection),
                table,
                sql.Identifier(row._meta.pk.column),
            ),
            (*data, row.pk),
        )


def candidate(
    product,
    token=None,
    value="10",
    *,
    at=START,
    code="001",
    indicator="CO",
    unit="mg/m³",
    window="hourly_label_unspecified",
    rule=1,
    **profile_fields,
):
    from django.utils import timezone
    from observations.normalization import BatchContext, ProductProfile, normalize
    from observations.revisions import Candidate

    profile = ProductProfile(product.shape, "UTC", "F confirmed", window, **profile_fields)
    fields = {
        "object_code": code,
        "source_time": (at.date() if window == "local_daily" else at).isoformat(),
        "indicator": indicator,
        "raw_value": value,
        "raw_unit": unit,
        "row_locator": f"F:{code}:{at.isoformat()}",
    }
    return Candidate(
        normalize(profile, fields, BatchContext(timezone.now())),
        token=token,
        scheme="sequence" if token is not None else "",
        rule=rule,
    )


def selector(product, entity, *, at=START, **overrides):
    return {
        "product": str(product.pk),
        ("site_epoch" if product.level == "station" else "city"): str(entity.pk),
        "indicator": "CO",
        "report_kind": "hourly",
        "window_definition": "hourly_label_unspecified",
        "standard_series": "not_applicable",
        "observed_at": at.isoformat(),
        "scope": "synthetic",
        **overrides,
    }


@cache
def fixture_label(product_id, label):
    from observations.revisions import fixture_ticket

    return fixture_ticket(product_id)


def ingest_fixture(actor_id, product_id, label, profile, candidates):
    """Local labels let tests repeat the same server-issued request identity."""
    from observations.models import Product
    from observations.revisions import ingest_batch

    version = Product.objects.values_list("contract_version", flat=True).get(pk=product_id)
    return ingest_batch(actor_id, product_id, fixture_label(product_id, label), version, candidates)
