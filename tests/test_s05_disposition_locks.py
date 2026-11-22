"""S05 regressions for frozen grants and ordered cross-source disposition."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from time import sleep
from uuid import UUID

import pytest
import test_s05_races as races
from accounts import research_exports as exports
from accounts.models import ExportTicket
from django.db import connection, transaction
from observations.disposition import reconcile_product, schedule
from observations.models import DispositionJob, Source
from observations.policy import clock, revoke_grant
from s05_support import HOUR, START, authorize, candidate, grant, ingest, product, site
from test_s05_races import (
    EXPORT,
    blocked_by,
    csv_matches,
    in_thread,
    location,
    pid,
    prepare,
    record,
    rejection,
    version,
)
from test_s05_selection import parent, post, session

pytestmark = pytest.mark.django_db(transaction=True)
race_state = races.state


def issued(web, query, stamp):
    response = prepare(web, query, stamp)
    assert response.status_code == 201, response.content
    return ExportTicket.objects.get(pk=response.json()["id"])


def test_current_allow_retires_ticket_in_registration_transaction(race_state):
    op, _, web, p, _, _, query, stamp = race_state
    old = issued(web, query, stamp)
    fields = exports.export_fields("detail")
    # These grants never contribute to this ticket's complete scoped obligation.
    for dimensions in (
        {"object_code": "hidden"},
        {"start": START + 48 * HOUR},
        {"indicator": "PM2.5"},
        {"audience": "operator"},
        {"fields": {"export_manifest"}},
    ):
        grant(op, p, "export", **({"fields": fields} | dimensions))
    old.refresh_from_db()
    assert old.invalidated_at is None
    csv_matches(web.get(location(old.pk)), old, stamp)

    with transaction.atomic():
        added = grant(op, p, "export", object_code="101", fields=fields)
        old.refresh_from_db()
        assert old.invalidated_at is not None
    # No intervening download may be required to tombstone the old signature.
    revoke_grant(op.pk, added.pk, "LR-02 F")
    rejection(web.get(location(old.pk)), 403, "use_denied")
    current = issued(web, query, stamp)
    csv_matches(web.get(location(current.pk)), current, stamp)
    record("grant-registration", invisible_changes=5, retired_after_revoke=True, refreshed_csv=200)


@pytest.mark.parametrize("effect", ["allow", "deny"])
def test_future_grant_shortens_only_intersecting_ticket_at_activation(race_state, effect):
    op, _, web, p, target, _, _, _ = race_state
    fields = exports.export_fields("detail") if effect == "allow" else ["*"]
    ingest(op, p, [candidate(p, "101", at=START + 24 * HOUR, token=1)])
    short = parent(p, [target], hours=6)
    long = parent(p, [target], hours=30)
    short_stamp = version(web, short)
    untouched = issued(web, short, short_stamp)
    ticket = issued(web, long, version(web, long))
    at = clock() + timedelta(seconds=2)
    new = grant(
        op, p, "export", fields=fields, start=START + 24 * HOUR, valid_from=at, effect=effect
    )
    ticket.refresh_from_db()
    original_expires = untouched.expires_at
    untouched.refresh_from_db()
    assert ticket.expires_at == at and ticket.invalidated_at is None
    assert untouched.expires_at == original_expires and untouched.invalidated_at is None
    assert web.get(location(ticket.pk)).status_code == 200
    sleep(max(0, (at - clock()).total_seconds()) + 0.02)
    if effect == "deny":
        rejection(web.get(location(ticket.pk)), 403, "use_denied")
    revoke_grant(op.pk, new.pk, "LR-02 future grant withdrawal after activation")
    rejection(web.get(location(ticket.pk)), 410, "expired")
    csv_matches(web.get(location(untouched.pk)), untouched, short_stamp)
    record(f"future-{effect}-registration", before_effect=200, after_revocation=410, disjoint=200)


def test_ticket_born_before_scheduled_allow_never_revives_after_grant_expires(race_state):
    op, _, web, p, _, _, query, stamp = race_state
    fields = exports.export_fields("detail")
    at = clock() + timedelta(seconds=2)
    end = at + timedelta(milliseconds=300)
    grant(op, p, "export", fields={"export_manifest"}, valid_from=at)
    grant(op, p, "export", fields=fields, object_code="hidden", valid_from=at)
    control = issued(web, query, stamp)
    assert control.expires_at == control.created_at + exports.TICKET_TTL
    csv_matches(web.get(location(control.pk)), control, stamp)

    # This grant exists when the new ticket is signed and expires naturally without
    # any request while it is active. Both tickets must keep the activation ceiling.
    grant(op, p, "export", fields=fields, object_code="101", valid_from=at, valid_until=end)
    pending = issued(web, query, stamp)
    control.refresh_from_db()
    assert pending.expires_at == control.expires_at == at
    for ticket in (pending, control):
        csv_matches(web.get(location(ticket.pk)), ticket, stamp)

    sleep(max(0, (end - clock()).total_seconds()) + 0.02)
    for ticket in (pending, control):
        rejection(web.get(location(ticket.pk)), 410, "expired")
    refreshed = issued(web, query, stamp)
    assert refreshed.expires_at == refreshed.created_at + exports.TICKET_TTL
    csv_matches(web.get(location(refreshed.pk)), refreshed, stamp)
    record("prescheduled-allow-expiry", before_effect=200, after_natural_expiry=410, refreshed=200)


@pytest.mark.parametrize("winner", [0, 1])
def test_cnemc_disposition_locks_union_in_pk_order_across_sources(
    django_user_model, monkeypatch, winner
):
    op = django_user_model.objects.create_superuser("s05-lock-curator")
    user = django_user_model.objects.create_user("s05-lock-reader")
    web = session(user)
    a_source = Source.objects.create(namespace="cnemc", publisher="Project F")
    b_source = Source.objects.create(namespace="cnemc.fixture", publisher="Project F")
    products = [
        product(f"s05-lock-{index}", source=source, timezone="Asia/Shanghai")
        for index, source in enumerate((a_source, a_source, b_source, b_source))
    ]
    groups = []
    for index, p in enumerate(products):
        code = str(101 + index)
        target = site(p, code)
        authorize(op, p, detail_fields=exports.export_fields("detail"))
        ingest(op, p, [candidate(p, code, token=1)])
        query = parent(p, [target], hours=6)
        groups.append(query | {"input_version": version(web, query)})
        saved = post(
            web,
            "/api/v1/me/saved-queries/",
            (query | {"method": "statistics", "title": "F order regression"}),
        )
        assert saved.status_code == 201, saved.content

    # Legacy per-product traversal took the high UUID first for A and low UUID first for B.
    pk = ExportTicket._meta.pk
    with monkeypatch.context() as fixed_uuids:
        ids = (UUID(int=1), UUID(int=2), UUID(int=3))
        fixed_uuids.setattr(pk, "_get_default", iter(ids).__next__)
        for left, right in ((1, 2), (0, 1), (0, 3)):
            response = post(
                web, EXPORT, {"kind": "detail", "groups": [groups[left], groups[right]]}
            )
            assert response.status_code == 201, response.content
    jobs = [schedule(p, clock(), "rights_boundary") for p in (products[0], products[2])]
    ready, release, rival = Queue(), Event(), Queue()

    def dispose(job, first):
        backend = pid()
        order = []
        if not first:
            rival.put(backend)

        def observe(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if "FOR UPDATE" in sql:
                for table in (
                    "observations_station",
                    "accounts_savedquery",
                    "accounts_exportticket",
                ):
                    if f'FROM "{table}"' in sql:
                        order.append(table)
                        assert "ORDER BY" in sql and " ASC" in sql
                if first and order[-1:] == ["accounts_exportticket"] and not release.is_set():
                    ready.put(backend)
                    assert release.wait(5)
            return result

        with connection.execute_wrapper(observe), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '6s'")
            reconcile_product(job.product_id)
        return order

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(in_thread, lambda: dispose(jobs[winner], True))
        holder = ready.get(timeout=5)
        b = pool.submit(in_thread, lambda: dispose(jobs[1 - winner], False))
        waiter = rival.get(timeout=5)
        try:
            blocked_by(waiter, holder)
        finally:
            release.set()
        orders = [a.result(timeout=7), b.result(timeout=7)]

    expected = ["observations_station", "accounts_savedquery", "accounts_exportticket"]
    assert orders == [expected, expected]
    assert (
        DispositionJob.objects.filter(
            pk__in=[j.pk for j in jobs], completed_at__isnull=False
        ).count()
        == 2
    )
    assert list(ExportTicket.objects.order_by("pk").values_list("pk", flat=True)) == list(ids)
    assert all(web.get(location(pk)).status_code == 200 for pk in ids)
    record(
        f"cross-source-disposition-{winner}",
        backend_blocking=True,
        committed=2,
        order=orders,
        retained=3,
    )
