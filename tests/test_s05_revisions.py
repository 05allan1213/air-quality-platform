"""V13 observation identity, statistical content and ticket invalidation via the API."""

from dataclasses import replace
from decimal import Decimal

import pytest
from accounts.research_exports import export_fields
from observations.models import CityObservation
from observations.policy import RAW
from observations.revisions import correct_mapping
from s05_support import HOUR, START, authorize, candidate, city, grant, ingest, product
from test_s05_private import (
    SCOPE,
    check,
    create_plan,
    csv_response,
    login,
    prepare_csv,
    read_version,
)
from test_s05_selection import DETAIL, parent, post

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("first_version", (1, None), ids=("same-version", "unordered"))
def test_conflicting_sample_changes_qualification_saved_content_and_old_ticket(
    django_user_model, first_version
):
    op = django_user_model.objects.create_superuser("v13-curator")
    web = login(
        django_user_model.objects.create_user("v13-conflict", password="v13-conflict-test-abcdef")
    )
    p = product("v13-qual", "C")
    a = city(p)
    authorize(op, p, detail_fields=export_fields("detail"))
    original = candidate(p, value=10, token=first_version)
    ingest(op, p, [original])
    query = parent(p, [a], method="statistics", hours=1)
    saved = create_plan(web, query)
    before, group = read_version(web, query)
    ticket = check(prepare_csv(web, group), 201)
    key = CityObservation.objects.get(product=p)
    current = key.revisions.get(is_current=True).pk
    assert before["columns"][0]["V"] == 1
    ingest(op, p, [candidate(p, value=20, token=1)])
    ingest(op, p, [original])
    again, changed_group = read_version(web, query)
    assert again["input_version"] != before["input_version"]
    col = again["columns"][0]
    assert (col["E"], col["O"], col["V"], col["exclusions"]["disputed"]) == (1, 1, 0, 1)
    row = check(post(web, DETAIL, changed_group), 200)["results"][0]
    assert (row["quality"], row["revision"], row["value"]) == ("disputed", current, None)
    assert key.revisions.filter(candidate=True, is_current=False).count() == 1
    assert check(csv_response(web, ticket), 409) == {"code": "input_changed"}
    assert (
        check(post(web, "/api/v1/me/saved-queries/" + saved["id"] + "/run/" + SCOPE, {}), 200)[
            "state"
        ]
        == "data_changed"
    )


def test_mapping_correction_recomputes_same_means_on_changed_hourly_membership(django_user_model):
    op = django_user_model.objects.create_superuser("v13-map-curator")
    web = login(
        django_user_model.objects.create_user("v13-corrected", password="v13-corrected-test-abcdef")
    )
    p = product("v13-map", "C")
    a = city(p)
    authorize(op, p, detail_fields=export_fields("detail"))
    for operation in ("retain_normalized", "review", "process_local"):
        grant(op, p, operation, fields=RAW)
    corrected = candidate(p, value="10000", unit="µg/m³", token=4, rule=2, rule_version="f.co.v2")
    first = replace(corrected, data=replace(corrected.data, value=Decimal(40)), rule=1)
    ingest(op, p, [first, candidate(p, at=START + HOUR, value=10, token=1)])
    query = parent(p, [a], method="statistics", hours=2)
    saved = create_plan(web, query)
    initial, group = read_version(web, query)
    ticket = check(prepare_csv(web, group), 201)
    key = CityObservation.objects.get(product=p, observed_at_utc=START)
    assert (
        correct_mapping(
            op.pk,
            p.pk,
            "city",
            key.pk,
            key.revisions.get(is_current=True).pk,
            key.watermark,
            corrected,
            "F original unit correction",
        )
        == "local_mapping_fix"
    )
    ingest(op, p, [candidate(p, at=START + HOUR, value=40, token=4)])
    latest, _ = read_version(web, query)
    assert Decimal(latest["whole_period_mean"]) == Decimal(initial["whole_period_mean"]) == 25
    assert (
        check(post(web, "/api/v1/me/saved-queries/" + saved["id"] + "/run/" + SCOPE, {}), 200)[
            "state"
        ]
        == "data_changed"
    )
    assert check(csv_response(web, ticket), 409) == {"code": "input_changed"}
