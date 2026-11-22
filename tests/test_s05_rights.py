"""V01 P1 grant matrix and V02 purpose-scoped checks on PostgreSQL."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from observations.policy import (
    LOCAL_AUTHENTICATED,
    REQUIRED,
    Denied,
    Scope,
    applicable,
    clock,
    require_use,
    revoke_grant,
)
from observations.queries import read_current
from observations.selection import ReadContext
from s02_support import selector
from s05_support import ANALYZE, START, authorize, candidate, grant, ingest, product, site

pytestmark = pytest.mark.django_db(transaction=True)
SCOPE = Scope("F-city", "001", "CO", START.date(), START.date() + timedelta(days=1))


@pytest.fixture
def actors(django_user_model):
    return (
        django_user_model.objects.create_superuser("s05-curator"),
        django_user_model.objects.create_user("s05-reader"),
    )


@pytest.mark.parametrize("op", ["analyze", "export", "export_aggregate"])
def test_registration_and_decision_share_the_audience_matrix(actors, op):
    operator, reader = actors
    p = product()
    with pytest.raises(Denied, match="^invalid_grant$"):
        grant(operator, p, op, audience="public")
    with pytest.raises(Denied, match="^permission_denied$"):
        grant(reader, p, op)

    legacy = grant(operator, p, op, fields=["*"], audience="operator")
    assert applicable(p, op, SCOPE, REQUIRED, clock()).ids == (legacy.pk,)
    for audience in ("public", LOCAL_AUTHENTICATED):
        with pytest.raises(Denied, match="^use_denied$"):
            applicable(p, op, SCOPE, REQUIRED, clock(), audience=audience)

    current = grant(operator, p, op)
    assert applicable(p, op, SCOPE, REQUIRED, clock(), audience=LOCAL_AUTHENTICATED).ids == (
        current.pk,
    )
    assert applicable(p, op, SCOPE, REQUIRED, clock()).ids == (legacy.pk,)


@pytest.mark.parametrize(
    "field",
    [
        "research_coverage",
        "research_summary",
        "export_manifest",
        "statistics_baseline",
        "csv_value",
    ],
)
def test_derived_fields_require_one_complete_explicit_allow(actors, field):
    operator, _ = actors
    p = product()
    grant(operator, p, "display", fields=["*"])
    grant(operator, p, "display", fields=[field])
    wanted = REQUIRED | {field}
    with pytest.raises(Denied, match="^use_denied$"):
        applicable(p, "display", SCOPE, wanted, clock())
    complete = grant(operator, p, "display", fields={"*", field})
    assert applicable(p, "display", SCOPE, wanted, clock()).ids == (complete.pk,)


@pytest.mark.parametrize("veto", [{"effect": "deny"}, {"restrictions": {"hour": 3}}])
def test_scope_and_deny_preserve_authorized_basis(actors, veto):
    operator, _ = actors
    p = product()
    baseline = grant(operator, p, "analyze", fields=ANALYZE)
    for override in (
        {"object_code": "999"},
        {"geography": "unrelated"},
        {"indicator": "NO2"},
        {"data_from": SCOPE.end},
        {"audience": "operator"},
    ):
        grant(operator, p, "analyze", fields=["*"], **(veto | override))
    assert applicable(p, "analyze", SCOPE, ANALYZE, clock(), audience=LOCAL_AUTHENTICATED).ids == (
        baseline.pk,
    )

    grant(operator, p, "analyze", fields=["*"], **veto)
    with pytest.raises(Denied, match="^use_denied$"):
        applicable(p, "analyze", SCOPE, ANALYZE, clock(), audience=LOCAL_AUTHENTICATED)


def test_analyze_and_export_grants_do_not_cross_products_or_operator_use(actors):
    operator, reader = actors
    yes = product("export-yes", shape="H")
    no = product("export-no")
    target = site(yes)
    permits = authorize(operator, yes, detail_fields={"export_manifest", "csv_value"})
    authorize(operator, no)
    ingest(operator, yes, [candidate(yes)])
    fields = {"export_manifest", "csv_value"}
    assert applicable(yes, "export", SCOPE, fields, clock(), audience=LOCAL_AUTHENTICATED).ids == (
        permits["export"].pk,
    )
    with pytest.raises(Denied, match="^use_denied$"):
        applicable(
            no, "export", SCOPE, fields, clock(), [permits["export"]], audience=LOCAL_AUTHENTICATED
        )

    old = grant(operator, yes, "analyze", audience="operator")
    with transaction.atomic():
        assert require_use(operator.pk, yes, "analyze", SCOPE, REQUIRED).ids == (old.pk,)
        with pytest.raises(Denied, match="^permission_denied$"):
            require_use(reader.pk, yes, "analyze", SCOPE, REQUIRED)
    with pytest.raises(Denied, match="^permission_denied$"):
        read_current(reader.pk, selector(yes, target), "analyze")

    revoke_grant(operator.pk, permits["display"].pk, "V01 independent withdrawal")
    assert applicable(yes, "retain_normalized", SCOPE, REQUIRED, clock()).ids == (
        permits["retain_normalized"].pk,
    )
    result = read_current(operator.pk, selector(yes, target), "analyze")
    assert result["count"] == 1
    assert result["results"][0]["value"] == "10.000000"
    with pytest.raises(Denied, match="^use_denied$"):
        read_current(reader.pk, selector(yes, target))


def test_read_context_rechecks_time_and_separates_purposes_and_audiences(actors):
    operator, reader = actors
    p = product()
    target = site(p)
    grant(operator, p, "retain_normalized")
    grant(operator, p, "display")
    local = grant(operator, p, "analyze", valid_until=clock() + timedelta(milliseconds=700))
    with transaction.atomic():
        context = ReadContext(reader.pk, [p.pk], True)
        assert context.basis(p, SCOPE, purpose="display")
        assert context.basis(p, SCOPE, purpose="analyze") is None
        assert context.basis(p, SCOPE, purpose="analyze", audience=LOCAL_AUTHENTICATED).ids == (
            local.pk,
        )
        assert context.scope(
            p, ["CO"], [target.pk], purpose="analyze", audience=LOCAL_AUTHENTICATED
        ).permitted
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s - clock_timestamp())) + 0.01))",
                [local.valid_until],
            )
        with pytest.raises(Denied, match="^use_denied$"):
            context.finish()
