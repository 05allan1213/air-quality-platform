"""Controlled city-search aliases over real session-authenticated Admin HTTP."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from urllib.parse import unquote, urlencode
from uuid import uuid4

import pytest
from accounts.assigned_permissions import MAINTAIN_CITY_ALIAS
from accounts.models import AccountState, GovernanceAction
from accounts.research_session import SessionCheck
from collection.models import Job
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from observations.city_search_alias import match_city_names, reconcile_retention
from observations.disposition import dispose_due
from observations.models import City, CityIdentifier, CitySearchAlias, Source
from observations.policy import revoke_grant
from observations.selection import Segment, Visibility
from psycopg.types.range import TimestamptzRange
from s02_support import BOUNDARY, START, allow, create_product
from s05_http import loopback
from test_s06_accounts import permission as named_permission

pytestmark = pytest.mark.django_db(transaction=True)

ALIAS_URL = "/admin/observations/city-search-alias/{product}/{city}/"


def prepare(django_user_model, *, alias_permission=True, source=None):
    rights_owner = django_user_model.objects.create_user(
        "s08-alias-rights-owner", password="s05-test-only", is_superuser=True
    )
    operator = django_user_model.objects.create_user(
        "s08-alias-operator", password="s05-test-only", is_staff=True
    )
    if alias_permission:
        permission = Permission.objects.get(
            content_type__app_label="accounts", codename="maintain_city_search_alias"
        )
        operator.user_permissions.add(permission)
    product = create_product(code="s08-alias", shape="L", source=source)
    city = City.objects.create(source=product.source, name="正式城市名", geography="110000")
    CityIdentifier.objects.create(
        source=product.source,
        city=city,
        code="110100",
        active_period=TimestamptzRange(START, BOUNDARY),
        evidence="F",
    )
    keep = allow(rights_owner, product, "retain_normalized", fields=["catalog"])
    allow(rights_owner, product, "review", fields=["catalog"])
    return operator, rights_owner, product, city, keep


def payload(action, alias, version, request_id=None):
    return {
        "action": action,
        "reason_code": "search_correction",
        "expected_version": version,
        "request_id": str(request_id or uuid4()),
        "search_alias": alias,
    }


def test_alias_only_staff_can_set_clear_and_keep_monotonic_version(django_user_model):
    operator, _, product, city, _ = prepare(django_user_model)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)

    with loopback(operator) as (web, _):
        initial = web.get(route)
        assert initial.status_code == 200
        assert "正式城市名" in initial.text
        assert "search_alias" in initial.text
        assert "no-store" in initial.headers["cache-control"]

        set_command = payload("set", " 江北新城 ", 0)
        saved = web.post(route, data=set_command)
        assert saved.status_code == 200
        assert saved.json()["status"] == "set"
        assert saved.json()["version"] == 1
        assert CitySearchAlias.objects.get(product=product, city=city).search_alias == "江北新城"

        replay = web.post(route, data=set_command)
        assert replay.status_code == 200
        assert replay.json()["action_id"] == saved.json()["action_id"]
        different_intent = set_command | {"search_alias": "不同别名"}
        assert web.post(route, data=different_intent).status_code == 409

        web.headers.pop("X-CSRFToken")
        csrf_rejected = web.post(route, data=payload("set", "无令牌别名", 1))
        assert csrf_rejected.status_code == 403
        assert csrf_rejected.json() == {"code": "csrf_failed"}
        web.headers["X-CSRFToken"] = web.get("/api/v1/auth/csrf/").json()["csrf_token"]

        stale = web.post(route, data=payload("set", "另一别名", 0))
        assert stale.status_code == 409
        assert stale.json() == {"code": "precondition_conflict"}

        cleared = web.post(route, data=payload("clear", "", 1))
        assert cleared.status_code == 200
        assert cleared.json()["status"] == "cleared"
        assert cleared.json()["version"] == 2
        assert CitySearchAlias.objects.get(product=product, city=city).search_alias == ""

        after_clear = web.get(route)
        assert after_clear.status_code == 200
        assert 'name="expected_version" value="2"' in after_clear.text
        assert web.get(f"/admin/auth/user/{operator.pk}/change/").status_code == 404


def test_alias_form_version_is_bounded_ascii_decimal_and_rejects_duplicate_fields(
    django_user_model,
):
    operator, _, product, city, _ = prepare(django_user_model)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    with loopback(operator) as (web, _):
        base = payload("set", "有效名称", 0)
        for invalid in ("", "-1", "+1", "１", "9" * 20, "1\n"):
            response = web.post(route, data=base | {"expected_version": invalid})
            assert response.status_code == 400
            assert response.json() == {"code": "invalid_request"}

        repeated = [(key, str(value)) for key, value in base.items()]
        repeated.append(("expected_version", "0"))
        response = web.post(
            route,
            content=urlencode(repeated),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 400
        assert response.json() == {"code": "invalid_request"}

        assert web.post(route, data=base | {"unexpected": "field"}).status_code == 400
        assert web.post(route, data=base | {"search_alias": "x" * 17_000}).status_code == 400
        assert web.post(route, data=base | {"search_alias": "  有效 名称  "}).status_code == 200
        assert CitySearchAlias.objects.get(product=product, city=city).search_alias == "有效 名称"
        normalized = web.post(route, data=payload("set", "e\u0301", 1))
        assert normalized.status_code == 200
        assert CitySearchAlias.objects.get(product=product, city=city).search_alias == "é"
        assert web.post(route, data=payload("set", "带\n换行", 2)).status_code == 400
        assert web.post(route, data=payload("set", "https://example.invalid", 2)).status_code == 400


def test_alias_directory_lists_only_authorized_targets_and_has_bounded_pages(django_user_model):
    operator, owner, product, _, _ = prepare(django_user_model)
    other_product = create_product(code="s08-alias-no-right", shape="L")
    allow(
        owner,
        product,
        "retain_normalized",
        geography="110000",
        fields=["catalog"],
        data_until=datetime.max.date(),
    )
    allow(
        owner,
        product,
        "review",
        geography="110000",
        fields=["catalog"],
        data_until=datetime.max.date(),
    )
    retain_only_product = create_product(code="s08-alias-retain-only", shape="L")
    retain_only_city = City.objects.create(
        source=retain_only_product.source, name="仅留存权城市", geography="110000"
    )
    CityIdentifier.objects.create(
        source=retain_only_product.source,
        city=retain_only_city,
        code="119999",
        active_period=TimestamptzRange(START, BOUNDARY),
        evidence="F",
    )
    allow(
        owner,
        retain_only_product,
        "retain_normalized",
        fields=["catalog"],
        data_until=datetime.max.date(),
    )
    # The original wildcard retention is revoked; the narrow replacement must
    # be the only reason these same-geography directory entries are visible.
    old_grant = product.usegrant_set.filter(operation="retain_normalized", geography="*").first()
    revoke_grant(owner.pk, old_grant.pk, "Replace wildcard fixture right")
    dispose_due(product.pk)

    period = TimestamptzRange(START, BOUNDARY)
    for index in range(26):
        city = City.objects.create(
            source=product.source, name=f"获权城市{index:02}", geography="110000"
        )
        CityIdentifier.objects.create(
            source=product.source,
            city=city,
            code=f"11{index:04}",
            active_period=period,
            evidence="F",
        )
    hidden = City.objects.create(source=product.source, name="权外城市", geography="120000")
    CityIdentifier.objects.create(
        source=product.source, city=hidden, code="120100", active_period=period, evidence="F"
    )
    denied_city = City.objects.create(
        source=other_product.source, name="无权产品城市", geography="110000"
    )
    CityIdentifier.objects.create(
        source=other_product.source,
        city=denied_city,
        code="110100",
        active_period=period,
        evidence="F",
    )

    with loopback(operator) as (web, _):
        home = web.get("/admin/")
        assert home.status_code == 200
        assert 'href="/admin/observations/city-search-alias/"' in home.text
        first = web.get("/admin/observations/city-search-alias/")
        assert first.status_code == 200
        assert first.text.count(f"/admin/observations/city-search-alias/{product.pk}/") == 25
        assert "权外城市" not in first.text
        assert "无权产品城市" not in first.text
        assert 'rel="next"' in first.text
        import re

        cursor = unquote(re.search(r'<a rel="next" href="\?cursor=([^\"]+)', first.text).group(1))
        second = web.get(f"/admin/observations/city-search-alias/?cursor={cursor}")
        assert second.status_code == 200
        assert "获权城市25" in second.text
        assert 'rel="next"' not in second.text


def test_alias_route_requires_staff_permission_and_scoped_catalog_rights(django_user_model):
    operator, owner, product, city, retain = prepare(django_user_model, alias_permission=False)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    with loopback(operator) as (web, _):
        assert web.get(route).status_code in (302, 403)

    permission = Permission.objects.get(
        content_type__app_label="accounts", codename="maintain_city_search_alias"
    )
    operator.user_permissions.add(permission)
    unknown_route = ALIAS_URL.format(product=product.pk, city=city.pk + 9999)
    with loopback(operator) as (web, _):
        unknown = web.get(unknown_route)
    assert unknown.status_code == 404
    assert unknown.json() == {"code": "not_found"}

    revoke_grant(owner.pk, retain.pk, "F alias retention withdrawn")
    with loopback(operator) as (web, _):
        response = web.get(route)
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}


def test_account_manager_does_not_gain_alias_permission(django_user_model):
    manager = django_user_model.objects.create_user(
        "s08-alias-account-manager", password="s05-test-only", is_staff=True
    )
    AccountState.objects.create(user=manager)
    manage = Permission.objects.get(content_type__app_label="accounts", codename="manage_accounts")
    manager.user_permissions.add(manage)
    product = create_product(code="s08-alias-manager", shape="L")
    city = City.objects.create(source=product.source, name="正式城市名", geography="110000")
    route = ALIAS_URL.format(product=product.pk, city=city.pk)

    with loopback(manager) as (web, _):
        assert web.get("/admin/auth/user/").status_code == 200
        assert 'href="/admin/observations/city-search-alias/"' not in web.get("/admin/").text
        directory_denied = web.get("/admin/observations/city-search-alias/")
        assert directory_denied.status_code == 403
        denied = web.get(route)
    assert denied.status_code == 403
    assert denied.json() == {"code": "permission_denied"}


def test_superuser_without_assigned_alias_permission_cannot_discover_read_or_write(
    django_user_model,
):
    editor, _, product, city, _ = prepare(django_user_model, alias_permission=False)
    editor.is_superuser = True
    editor.save(update_fields=["is_superuser"])
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    directory = "/admin/observations/city-search-alias/"
    command = payload("set", "江北新城", 0)
    with loopback(editor) as (web, _):
        assert f'href="{directory}"' not in web.get("/admin/").text
        for response in (web.get(directory), web.get(route), web.post(route, data=command)):
            assert (response.status_code, response.json()) == (403, {"code": "permission_denied"})
        assert not CitySearchAlias.objects.filter(product=product).exists()
        assert not GovernanceAction.objects.filter(target_kind="city_search_alias").exists()

        group = Group.objects.create(name="Explicit search alias assignment")
        grant = named_permission(MAINTAIN_CITY_ALIAS)
        group.permissions.add(grant)
        editor.groups.add(group)
        assert f'href="{directory}"' in web.get("/admin/").text
        assert web.get(directory).status_code == 200
        assert web.get(route).status_code == 200
        assert web.post(route, data=command).status_code == 200
        group.permissions.remove(grant)
        for response in (web.get(directory), web.get(route), web.post(route, data=command)):
            assert (response.status_code, response.json()) == (403, {"code": "permission_denied"})
        assert CitySearchAlias.objects.get(product=product, city=city).version == 1
        assert GovernanceAction.objects.filter(target_kind="city_search_alias").count() == 1


def test_alias_permission_is_rechecked_after_receipt_write(django_user_model, monkeypatch):
    editor, _, product, city, _ = prepare(django_user_model)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    grant = named_permission(MAINTAIN_CITY_ALIAS)
    reached_finish = []
    real_finish = SessionCheck.finish

    def remove_before_finish(session, relations):
        reached_finish.append(GovernanceAction.objects.filter(actor=editor).count())
        editor.user_permissions.remove(grant)
        return real_finish(session, relations)

    with loopback(editor) as (web, _):
        monkeypatch.setattr(SessionCheck, "finish", remove_before_finish)
        response = web.post(route, data=payload("set", "江北新城", 0))
        assert (response.status_code, response.json()) == (403, {"code": "permission_denied"})
    assert reached_finish == [1]
    assert not CitySearchAlias.objects.filter(product=product).exists()
    assert not GovernanceAction.objects.filter(target_kind="city_search_alias").exists()


def test_alias_search_is_limited_to_the_supplied_authorized_catalog(django_user_model):
    _, _, product, city, _ = prepare(django_user_model)
    second = City.objects.create(source=product.source, name="同别名城市", geography="110000")
    CityIdentifier.objects.create(
        source=product.source,
        city=second,
        code="110200",
        active_period=TimestamptzRange(START, BOUNDARY),
        evidence="F",
    )
    hidden = City.objects.create(source=product.source, name="其他城市", geography="120000")
    CityIdentifier.objects.create(
        source=product.source,
        city=hidden,
        code="120100",
        active_period=TimestamptzRange(START, BOUNDARY),
        evidence="F",
    )
    CitySearchAlias.objects.create(product=product, city=city, search_alias="江北新城", version=1)
    CitySearchAlias.objects.create(product=product, city=second, search_alias="江北新城", version=1)
    CitySearchAlias.objects.create(product=product, city=hidden, search_alias="江北新城", version=1)

    visible_catalog = Visibility(
        product,
        [
            Segment(
                city.pk, "110100", "110000", "*", START, BOUNDARY, START.date(), BOUNDARY.date()
            ),
            Segment(
                second.pk, "110200", "110000", "*", START, BOUNDARY, START.date(), BOUNDARY.date()
            ),
        ],
    )
    rows = match_city_names(
        City.objects.filter(source=product.source), product, "江北", visible_catalog
    )
    assert set(rows.values_list("pk", flat=True)) == {city.pk, second.pk}

    no_catalog = Visibility(product, [])
    assert not match_city_names(
        City.objects.filter(source=product.source), product, "江北", no_catalog
    ).exists()


def test_two_alias_operators_cannot_write_the_same_version(django_user_model):
    first, _, product, city, _ = prepare(django_user_model)
    second = django_user_model.objects.create_user(
        "s08-alias-operator-second", password="s05-test-only", is_staff=True
    )
    permission = Permission.objects.get(
        content_type__app_label="accounts", codename="maintain_city_search_alias"
    )
    second.user_permissions.add(permission)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    barrier = Barrier(2)

    with loopback(first) as (first_web, _), loopback(second) as (second_web, _):

        def submit(web, value):
            barrier.wait(timeout=5)
            return web.post(route, data=payload("set", value, 0))

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda args: submit(*args), ((first_web, "甲城"), (second_web, "乙城")))
            )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    alias = CitySearchAlias.objects.get(product=product, city=city)
    assert alias.version == 1
    assert alias.search_alias in {"甲城", "乙城"}


def test_alias_retention_reconciler_deletes_rights_expired_metadata(django_user_model):
    _, owner, product, city, retain = prepare(django_user_model)
    alias = CitySearchAlias.objects.create(
        product=product, city=city, search_alias="江北新城", version=1
    )
    revoke_grant(owner.pk, retain.pk, "F alias retention withdrawn")

    with transaction.atomic():
        reconcile_retention(
            product,
            list(product.usegrant_set.filter(revoked_at__isnull=True)),
            product.usegrant_set.first().valid_from + timedelta(days=1),
        )

    assert not CitySearchAlias.objects.filter(pk=alias.pk).exists()


def test_disposition_clears_alias_but_keeps_job_city_anchor_and_version_watermark(
    django_user_model,
):
    source = Source.objects.create(namespace="cnemc.fixture", publisher="F alias fixture")
    operator, owner, product, city, retain = prepare(django_user_model, source=source)
    permission = Permission.objects.get(
        content_type__app_label="accounts", codename="maintain_city_search_alias"
    )
    operator.user_permissions.add(permission)
    route = ALIAS_URL.format(product=product.pk, city=city.pk)
    job = Job.objects.create(product=product, city=city, executor=operator, profile="s08-alias-f")

    with loopback(operator) as (web, _):
        saved = web.post(route, data=payload("set", "可处置别名", 0))
        assert saved.status_code == 200

    revoke_grant(owner.pk, retain.pk, "F alias retention withdrawn")
    dispose_due(product.pk)
    assert not CitySearchAlias.objects.filter(product=product, city=city).exists()
    city.refresh_from_db()
    job.refresh_from_db()
    assert job.city_id == city.pk
    assert city.name == city.geography == ""
    assert not CityIdentifier.objects.filter(city=city).exists()

    # Reintroduce the directory anchor and current rights in this isolated F
    # fixture to exercise the independent receipt watermark after row deletion.
    CityIdentifier.objects.create(
        source=product.source,
        city=city,
        code="110100",
        active_period=TimestamptzRange(START, BOUNDARY),
        evidence="F",
    )
    allow(owner, product, "retain_normalized", fields=["catalog"], data_until=datetime.max.date())
    allow(owner, product, "review", fields=["catalog"], data_until=datetime.max.date())
    with loopback(operator) as (web, _):
        stale = web.post(route, data=payload("set", "旧首写版本", 0))
    assert stale.status_code == 409
    assert stale.json() == {"code": "precondition_conflict"}
