"""C02/V11 HTTP consumers must fail closed while interrupted lineage is still in PG."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from accounts.models import ExportTicket, SavedQuery
from accounts.research_exports import export_fields
from observations.disposition import dispose_due
from observations.models import DispositionJob, StationObservation
from s02_support import observe_db, selector
from s05_support import ANALYZE, DISPLAY, RETAIN, candidate, grant, ingest, product, site
from test_s05_races import location
from test_s05_selection import DETAIL, parent
from test_s06_accounts import json_status, sign_in
from test_s06_accounts import sql_hook as sql_hook
from test_s06_accounts import web_clients as web_clients

pytestmark = pytest.mark.django_db(transaction=True)
CURRENT = "/api/v1/observations/current/"
EVIDENCE = "/api/v1/admin/observation-evidence/"
STATISTICS = "/api/v1/analysis/statistics/"
EXPORT = "/api/v1/me/exports/"
SAVED = "/api/v1/me/saved-queries/"
PASSWORD = "s06 pending verification F only"


@pytest.mark.parametrize("retain_raw_b", [False, True], ids=["normalized-only", "with-raw"])
def test_v11_pending_regrant_cannot_revive_old_inputs_through_http(
    django_user_model, web_clients, retain_raw_b
):
    operator = django_user_model.objects.create_superuser("pending-curator", password=PASSWORD)
    reader = django_user_model.objects.create_user("pending-reader", password=PASSWORD)
    admin, web = (sign_in(web_clients(), user, PASSWORD) for user in (operator, reader))
    p = product("pending-http")
    a, b = site(p, "101"), site(p, "102")
    lost_grant = grant(operator, p, "retain_normalized", fields=RETAIN, object_code="101")
    protected_fields = RETAIN | {"raw_field"} if retain_raw_b else RETAIN
    grant(operator, p, "retain_normalized", fields=protected_fields, object_code="102")
    for purpose, fields in (
        ("process_local", None),
        ("display", DISPLAY),
        ("analyze", ANALYZE),
        ("review", None),
        ("export", export_fields("detail")),
    ):
        grant(operator, p, purpose, fields=fields)
    # B has independent source lineage, so purging A cannot alter B's CSV projection.
    ingest(operator, p, [candidate(p, "101", value=17)])
    ingest(operator, p, [candidate(p, "102", value=23)])
    keys = dict(StationObservation.objects.filter(product=p).values_list("site_epoch_id", "pk"))

    def evidence(target):
        return admin.get(
            EVIDENCE,
            params={"product": p.pk, "scope": "synthetic", "station_key": keys[target.pk]},
        )

    def prepare(query, version):
        return web.post(
            EXPORT,
            json={"kind": "detail", "groups": [query | {"input_version": version}]},
        )

    def saved_run(saved):
        return web.post(f"{SAVED}{saved}/run/?scope=synthetic", json={})

    def snapshots(target):
        query = parent(p, [target], method="statistics", hours=1)
        stats = json_status(web.post(STATISTICS, json=query), 200)
        support = query | {"input_version": stats["input_version"]}
        detail = parent(p, [target], hours=1)
        version = json_status(web.post(DETAIL, json=detail), 200)["input_version"]
        ticket = json_status(prepare(detail, version), 201)["id"]
        saved = json_status(web.post(SAVED, json=query | {"title": "F intent"}), 201)["id"]
        assert json_status(saved_run(saved), 200)["state"] == "current"
        assert len(json_status(web.post(DETAIL, json=support), 200)["results"]) == 1
        return query, support, detail, version, ticket, saved

    a_stats, a_support, a_detail, a_version, a_ticket, a_saved = snapshots(a)
    b_stats, b_support, b_detail, b_version, b_ticket, b_saved = snapshots(b)
    assert Decimal(json_status(evidence(a), 200)["revisions"][0]["value"]) == 17
    assert Decimal(json_status(evidence(b), 200)["revisions"][0]["value"]) == 23
    assert (
        Decimal(json_status(web.get(CURRENT, params=selector(p, a)), 200)["results"][0]["value"])
        == 17
    )
    assert web.get(location(a_ticket)).headers["Content-Type"].startswith("text/csv")
    protected = web.get(location(b_ticket))
    assert protected.status_code == 200 and protected.headers["Content-Type"].startswith("text/csv")
    protected_csv = protected.content

    revoke = f"/api/v1/admin/grants/{lost_grant.pk}/actions/"
    version = json_status(admin.get(revoke), 200)["version"]
    json_status(
        admin.post(
            revoke,
            json={
                "action": "withdraw",
                "reason_code": "rights_review",
                "request_id": str(uuid4()),
                "expected_version": version,
            },
        ),
        200,
    )
    replacement = grant(
        operator,
        p,
        "retain_normalized",
        fields=RETAIN,
        object_code="101",
        valid_from=lost_grant.valid_from,
    )
    job = DispositionJob.objects.get(boundary_grant=lost_grant)
    assert replacement.pk not in job.boundary_basis

    def committed_state():
        with observe_db() as db:
            return db.execute(
                """SELECT (SELECT count(*) FROM observations_stationrevision WHERE key_id = %s),
                          (SELECT count(*) FROM observations_stationrevision WHERE key_id = %s),
                          (SELECT completed_at FROM observations_dispositionjob WHERE id = %s)""",
                (keys[a.pk], keys[b.pk], job.pk),
            ).fetchone()

    def committed_lineage(target):
        with observe_db() as db:
            return db.execute(
                """SELECT batch.id, batch.identity, batch.redacted, batch.fetched_at,
                          receipt.id, receipt.locator, rev.raw_value, rev.raw_unit
                   FROM observations_stationrevision rev
                   JOIN observations_receipt receipt ON receipt.station_revision_id = rev.id
                   JOIN observations_batch batch ON batch.id = receipt.batch_id
                   WHERE rev.key_id = %s""",
                (keys[target.pk],),
            ).fetchone()

    assert committed_state() == (1, 1, None)
    lost_lineage = committed_lineage(a)
    protected_lineage = committed_lineage(b)
    assert lost_lineage[0] != protected_lineage[0]
    assert protected_lineage[1] and protected_lineage[2] is False
    assert tuple(value is not None for value in protected_lineage[-3:]) == (retain_raw_b,) * 3
    for request in (
        web.get(CURRENT, params=selector(p, a)),
        evidence(a),
        web.post(STATISTICS, json=a_stats),
        web.post(DETAIL, json=a_support),
        saved_run(a_saved),
        prepare(a_detail, a_version),
    ):
        assert json_status(request, 403) == {"code": "use_denied"}
    stale = web.get(location(a_ticket))
    assert json_status(stale, 404) == {"code": "not_found"}
    assert "Content-Disposition" not in stale.headers
    assert not stale.headers["Content-Type"].startswith("text/csv")
    assert list(ExportTicket.objects.values_list("pk", flat=True)) == [UUID(b_ticket)]
    assert SavedQuery.objects.get(pk=a_saved).baseline == ""

    assert (
        Decimal(json_status(web.get(CURRENT, params=selector(p, b)), 200)["results"][0]["value"])
        == 23
    )
    assert Decimal(json_status(evidence(b), 200)["revisions"][0]["value"]) == 23
    assert (
        json_status(web.post(STATISTICS, json=b_stats), 200)["input_version"]
        == b_support["input_version"]
    )
    assert Decimal(json_status(web.post(DETAIL, json=b_support), 200)["results"][0]["value"]) == 23
    assert json_status(saved_run(b_saved), 200)["state"] == "current"
    fresh = json_status(prepare(b_detail, b_version), 201)["id"]
    assert ExportTicket.objects.get(pk=fresh).row_count == 1
    unaffected = web.get(location(b_ticket))
    assert unaffected.status_code == 200 and unaffected.content == protected_csv
    assert committed_state() == (1, 1, None)

    dispose_due(p.pk)
    remaining_a, remaining_b, completed_at = committed_state()
    assert (remaining_a, remaining_b) == (0, 1) and completed_at is not None
    assert json_status(web.get(CURRENT, params=selector(p, a)), 200)["results"] == []
    assert committed_lineage(a) is None
    assert committed_lineage(b) == protected_lineage
    unaffected = web.get(location(b_ticket))
    assert unaffected.status_code == 200 and unaffected.content == protected_csv
