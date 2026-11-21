"""S02 use decisions with PostgreSQL grant rows and current actor state."""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone
from observations.models import Product
from observations.policy import Denied, Scope, require_use, revoke_grant
from s02_support import BOUNDARY, START, allow, create_product
from samples.models import FixtureGrant

pytestmark = pytest.mark.django_db(transaction=True)
NORMALIZED_FIELDS = ("normalized", "provenance")


def decide(actor, product, operation, scope, fields=NORMALIZED_FIELDS):
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=product.pk)
        return require_use(actor.pk, product, operation, scope, fields)


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user("s02-operator", is_staff=True, is_superuser=True)


@pytest.fixture
def scope():
    return Scope("A", "001", "CO", START.date(), BOUNDARY.date())


def test_s02_does_not_borrow_s01_or_other_operation_permissions(operator, scope):
    product = create_product("one")
    other = create_product("two")
    FixtureGrant.objects.update_or_create(
        product="F",
        source="local.fixture.v1",
        geography="synthetic",
        audience="local_authenticated",
        operation="display",
        defaults={"valid_from": timezone.now(), "basis": "S01 fixture"},
    )
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)
    allow(operator, product, "display", geography="A")
    decide(operator, product, "display", scope)

    with pytest.raises(Denied):
        decide(operator, other, "display", scope)
    for operation in ("retain_normalized", "export", "backup", "redistribute"):
        with pytest.raises(Denied):
            decide(operator, product, operation, scope)


@pytest.mark.parametrize(
    "scope",
    [
        Scope("B", "001", "CO", START.date(), BOUNDARY.date()),
        Scope("A", "002", "CO", START.date(), BOUNDARY.date()),
        Scope("A", "001", "PM2.5", START.date(), BOUNDARY.date()),
        Scope("A", "001", "CO", START.date() - timedelta(days=1), BOUNDARY.date()),
        Scope("A", "001", "CO", START.date(), BOUNDARY.date() + timedelta(days=1)),
    ],
)
def test_a_grant_must_cover_the_requested_scope(operator, scope):
    product = create_product()
    allow(
        operator,
        product,
        "display",
        geography="A",
        object_code="001",
        indicator="CO",
        data_until=BOUNDARY.date(),
    )
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)


def test_confirmed_grants_do_not_combine_incomplete_field_sets(operator, scope):
    product = create_product()
    allow(operator, product, fields=["normalized"])
    allow(operator, product, fields=["provenance"])
    with pytest.raises(Denied):
        decide(operator, product, "retain_normalized", scope)
    broad = allow(operator, product, fields=list(NORMALIZED_FIELDS))
    narrow = allow(operator, product, geography="A", fields=list(NORMALIZED_FIELDS))
    decide(operator, product, "retain_normalized", scope)
    revoke_grant(operator.pk, broad.pk, "withdraw only the broad license")
    decide(operator, product, "retain_normalized", scope)
    revoke_grant(operator.pk, narrow.pk, "withdraw the remaining license")
    with pytest.raises(Denied):
        decide(operator, product, "retain_normalized", scope)


def test_explicit_prohibition_takes_precedence_only_in_matching_scope(operator, scope):
    product = create_product()
    allow(operator, product, "display")
    restriction = allow(operator, product, "display", geography="A", effect="deny")
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)
    decide(
        operator,
        product,
        "display",
        Scope("B", "001", "CO", START.date(), BOUNDARY.date()),
    )
    revoke_grant(operator.pk, restriction.pk, "withdraw matching prohibition")
    decide(operator, product, "display", scope)


def test_actor_permissions_and_operation_validity_are_rechecked(operator, scope):
    product = create_product()
    base = timezone.now()
    allow(operator, product, "display", valid_from=base + timedelta(days=1))
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)
    with pytest.raises(Denied, match="invalid_grant"):
        allow(operator, product, "display", audience="public")
    allow(operator, product, "public_display", audience="public")
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)
    active = allow(operator, product, "display", valid_from=base - timedelta(hours=1))
    decide(operator, product, "display", scope)
    type(active).objects.filter(pk=active.pk).update(valid_until=base)
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)
    allow(operator, product, "display")
    type(operator).objects.filter(pk=operator.pk).update(is_active=False)
    with pytest.raises(Denied):
        decide(operator, product, "display", scope)


def test_exact_read_respects_synthetic_space_disputes_and_role(operator, django_user_model):
    from decimal import Decimal

    from observations.models import StationObservation
    from observations.queries import read_current, read_evidence
    from s02_support import candidate, create_site, selector
    from s02_support import ingest_fixture as ingest_batch

    product = create_product()
    site = create_site(product)
    reader = django_user_model.objects.create_user("s02-reader")
    for operation in ("process_local", "retain_normalized", "display", "review", "analyze"):
        allow(operator, product, operation)
    ingest_batch(operator.pk, product.pk, "origin", "f-test-v1", [candidate(product, 1, "10")])
    query = selector(product, site)
    current = read_current(reader.pk, query)
    assert current["count"] == 1 and current["synthetic"]
    assert Decimal(current["results"][0]["value"]) == 10
    with pytest.raises(Denied, match="use_denied"):
        read_current(reader.pk, {k: v for k, v in query.items() if k != "scope"})
    empty = read_current(reader.pk, selector(product, site, at=START + timedelta(hours=1)))
    assert empty["count"] == 0 and empty["reason"] == "no_observation_at_time"
    ingest_batch(operator.pk, product.pk, "conflict", "f-test-v1", [candidate(product, 1, "20")])
    disputed = read_current(reader.pk, query)
    assert disputed["count"] == 0 and disputed["reason"] == "unresolved_dispute"
    key = StationObservation.objects.get(product=product)
    evidence = {"product": str(product.pk), "scope": "synthetic", "station_key": str(key.pk)}
    with pytest.raises(Denied, match="permission_denied"):
        read_evidence(reader.pk, evidence)
    reviewed = read_evidence(operator.pk, evidence)
    assert reviewed["disputed"]
    assert {(row["candidate"], Decimal(row["value"])) for row in reviewed["revisions"]} == {
        (True, Decimal(20)),
        (False, Decimal(10)),
    }


def test_display_withdrawal_invalidates_the_next_read_and_preserves_authorized_analysis(operator):
    from observations.disposition import dispose_due
    from observations.models import StationRevision
    from observations.queries import read_current
    from s02_support import candidate, create_site, selector
    from s02_support import ingest_fixture as ingest_batch

    product = create_product()
    site = create_site(product)
    for op in ("process_local", "retain_normalized", "analyze"):
        allow(operator, product, op)
    display = allow(operator, product, "display")
    ingest_batch(operator.pk, product.pk, "first", "f-test-v1", [candidate(product, 1)])
    query = selector(product, site)
    assert read_current(operator.pk, query)["count"] == 1
    before = read_current(operator.pk, query, "analyze")["results"][0]["input_version"]
    revoke_grant(operator.pk, display.pk, "withdraw display only")
    with pytest.raises(Denied, match="use_denied"):
        read_current(operator.pk, query)
    dispose_due(product.pk)
    assert StationRevision.objects.filter(key__product=product).count() == 1
    after = read_current(operator.pk, query, "analyze")["results"][0]["input_version"]
    assert after["revision"] == before["revision"] and after["rights"] > before["rights"]


@pytest.mark.parametrize(
    "override",
    [
        {"observed_at": "9999-12-31T23:00:00Z"},
        {"observed_at": "0001-01-01T00:00:00Z", "window_definition": "hourly_right_label"},
        {"local_report_date": "9999-12-31"},
        {"local_report_date": "2024-W01-1"},
        {"observed_at": "2024-01-01T00:00:00Z", "window_definition": "local_daily"},
    ],
)
def test_exact_selector_rejects_invalid_anchor_or_unrepresentable_scope(operator, override):
    from observations.queries import read_current
    from s02_support import create_site, selector

    product = create_product()
    query = selector(product, create_site(product), **override)
    if "local_report_date" in override:
        query.pop("observed_at")
        query.update(report_kind="daily", window_definition="local_daily")
    with pytest.raises(Denied, match="invalid_request"):
        read_current(operator.pk, query)
