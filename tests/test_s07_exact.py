"""C02: the exact-read version is scoped to the requesting session's authorized view."""

import re

import pytest
from observations.models import StationObservation
from observations.policy import revoke_grant
from s02_support import (
    START,
    allow,
    candidate,
    create_product,
    create_site,
    ingest_fixture,
    selector,
)
from s05_http import loopback

pytestmark = pytest.mark.django_db(transaction=True)


def test_exact_version_tracks_only_visible_changes(django_user_model):
    operator = django_user_model.objects.create_user(
        "s07-exact-operator", is_superuser=True, is_staff=True
    )
    reader = django_user_model.objects.create_user("s07-exact", password="s05-test-only")
    product = create_product()
    site = create_site(product)
    for operation in ("process_local", "retain_normalized"):
        allow(operator, product, operation, fields=["*"])
    display = allow(operator, product, "display", object_code="001")
    ingest_fixture(operator.pk, product.pk, "exact-1", "F", [candidate(product, 1)])
    query = selector(product, site)
    with loopback(reader) as (web, _):

        def get():
            return web.get("/api/v1/observations/current/", params=query)

        def version():
            response = get()
            assert response.status_code == 200
            body = response.json()
            assert body["count"] == 1
            row = body["results"][0]
            assert set(row).isdisjoint({"rights", "rights_version", "grants"})
            assert set(body).isdisjoint({"rights", "rights_version", "grants"})
            mark = row["input_version"]
            assert isinstance(mark, str) and re.fullmatch(r"[0-9a-f]{64}", mark)
            return mark, row["revision_id"]

        first, original = version()
        product.attribution = "S07 F updated public attribution"
        product.save(update_fields=["attribution"])
        updated, revision = version()
        assert updated != first and revision == original
        first = updated
        allow(operator, product, "display", object_code="hidden-F", attribution="hidden F basis")
        assert version() == (first, original)
        extra = allow(
            operator, product, "display", object_code="001", attribution="visible F basis"
        )
        second, current = version()
        assert second != first and current == original
        revoke_grant(operator.pk, extra.pk, "remove additional F basis")
        assert version() == (first, original)
        ingest_fixture(operator.pk, product.pk, "exact-2", "F", [candidate(product, 2, value="12")])
        third, changed = version()
        key = StationObservation.objects.get(product=product, observed_at_utc=START)
        assert changed == key.revisions.get(is_current=True).pk != original
        assert third not in {first, second}
        revoke_grant(operator.pk, display.pk, "withdraw F display")
        response = get()
        assert (response.status_code, response.json()) == (403, {"code": "use_denied"})
