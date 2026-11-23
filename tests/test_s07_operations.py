"""S07 operator discovery, import ordering and trend accounting with F/I fixtures."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from collection import adapters, services
from collection.models import Job, Run, WorkWindow
from django.contrib.auth.models import Permission
from observations import history, import_uci
from observations.models import CityIdentifier, ImportAttempt, Source, StagedInput
from observations.policy import clock, revoke_grant
from observations.uci_catalog import register_uci
from psycopg.types.range import TimestamptzRange
from s02_support import BOUNDARY, START, observe_db
from s05_http import loopback
from s06_witness import witness
from test_s07_contract_user_http import http_witness as http_witness

from tests.collection.test_collection_postgres import LIMITS, city_packet, grant, make_job, request

pytestmark = pytest.mark.django_db(transaction=True)
ADMIN = "/api/v1/admin/"
TRENDS = ADMIN + "operations/trends/"
HISTORY = ADMIN + "history-imports/"
BOUNDS = {"start": "2024-01-02", "end": "2024-01-05"}


def success(response):
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    return response.json()


def assign(user, label, code):
    user.user_permissions.add(Permission.objects.get(content_type__app_label=label, codename=code))


def account(users, suffix, *, superuser=False):
    return users.objects.create_user(
        f"s07-operations-{suffix}", password="s05-test-only", is_superuser=superuser
    )


def seed_windows(operator, monkeypatch, settings):
    settings.S03_FIXTURE_HTTP = True
    source = Source.objects.create(
        namespace="s03.fixture", publisher="F lifecycle", collection_limits=LIMITS
    )
    job = make_job(operator, source)
    job.product.source_timezone = "Asia/Shanghai"
    job.product.save(update_fields=["source_timezone"])
    for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
        grant(operator, job.product, operation)
    monkeypatch.setattr(services, "publish", lambda _run_id: None)
    replies = []

    def fetch(*_args):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply).encode(), clock()

    monkeypatch.setattr(adapters, "fetch", fetch)

    def run(target, reply, selected_job=job):
        # Move only the synthetic quota history so no wall-clock sleep is required.
        prior = clock() - timedelta(minutes=2)
        Run.objects.filter(job=job, reserved_at__isnull=False).update(
            reserved_at=prior, occupied_until=prior
        )
        replies.append(reply)
        attempt = services.submit(operator.pk, request(selected_job, target=target))
        services.execute(attempt.pk)
        attempt.refresh_from_db()
        assert not replies
        return attempt

    complete_at = BOUNDARY + timedelta(hours=14)
    partial_at = BOUNDARY + timedelta(hours=15)
    zone = ZoneInfo(job.product.source_timezone)
    complete = run(complete_at, city_packet(at=complete_at.astimezone(zone)))
    subset = city_packet(at=partial_at.astimezone(zone))
    del subset["O3"]
    partial = run(partial_at, subset)
    # The partial window ends at local midnight and belongs to January 2.
    failed = run(BOUNDARY + timedelta(hours=13), adapters.WireError("timeout"))
    coalesced = services.submit(operator.pk, request(job, target=complete_at))
    services.execute(coalesced.pk)
    coalesced.refresh_from_db()
    empty = run(BOUNDARY + timedelta(hours=17), city_packet(at=partial_at.astimezone(zone)))
    unlisted = Job.objects.create(
        product=job.product, city=job.city, executor=operator, profile=job.profile, enabled=True
    )
    no_roster_at = BOUNDARY + timedelta(hours=19)
    no_roster = run(no_roster_at, city_packet(at=no_roster_at.astimezone(zone)), unlisted)
    assert complete.window.closed_at and complete.result == "new"
    assert partial.result == "partial" and not partial.window.closed_at
    assert failed.window.result == "failed" and failed.next_retry_at
    assert coalesced.status == "coalesced" and coalesced.window_id == complete.window_id
    assert empty.result == "no_new" and empty.request_state == "responded"
    assert no_roster.window.expected is None
    # Seed one terminal retry for the failed window. It shares its canonical business window.
    retry = Run.objects.create(
        job=job,
        window_id=failed.window_id,
        actor=operator,
        executor=operator,
        reason="retry",
        predecessor=failed,
        attempt=2,
        status="failed",
        error_code="timeout",
    )
    assert WorkWindow.objects.filter(product=job.product).count() == 5
    # Deterministic PG-clock-relative queue and execution durations. The coalesced
    # delivery has no start, while the retry intentionally has no finish.
    at = datetime(2024, 1, 2, 17, tzinfo=UTC)
    for index, attempt in enumerate(
        (complete, partial, failed, coalesced, empty, no_roster, retry)
    ):
        created = at + timedelta(minutes=index * 10)
        Run.objects.filter(pk=attempt.pk).update(
            created_at=created,
            started_at=None if attempt.pk == coalesced.pk else created + timedelta(seconds=3),
            finished_at=None if attempt.pk == retry.pk else created + timedelta(seconds=8),
            scheduled_for=created - timedelta(seconds=1) if attempt.pk == complete.pk else None,
        )
    return job, (complete, partial, failed, coalesced, empty, no_roster, retry)


LEDGER_SQL = """
WITH bins AS (
    SELECT w.id, w.result, w.expected,
           ((w.target_end - interval '1 microsecond') AT TIME ZONE 'Asia/Shanghai')::date AS day
    FROM collection_workwindow w WHERE w.product_id = %s
), cells AS (
    SELECT b.day, b.id, i.item_key,
           bool_or(i.status = 'rejected' OR current_row.id IS NOT NULL) AS received,
           bool_or(current_row.quality = 'valid' AND NOT obs.disputed AND NOT obs.retired) AS valid
    FROM bins b JOIN collection_runitem i ON i.window_id = b.id
    LEFT JOIN observations_receipt rc ON rc.collection_item_id = i.id
    LEFT JOIN observations_cityrevision history_row ON history_row.id = rc.city_revision_id
    LEFT JOIN observations_cityobservation obs ON obs.id = history_row.key_id
    LEFT JOIN observations_cityrevision current_row ON current_row.key_id = obs.id AND current_row.is_current
    WHERE (b.expected IS NULL OR b.expected ? i.item_key)
      AND (%s = '' OR i.indicator = %s)
    GROUP BY b.day, b.id, i.item_key
), firsts AS (
    SELECT DISTINCT b.id FROM bins b JOIN collection_runitem i ON i.window_id = b.id AND i.accepted
    JOIN observations_receipt rc ON rc.collection_item_id = i.id AND rc.outcome = 'first'
    WHERE (%s = '' OR i.indicator = %s)
)
SELECT b.day, count(*) AS windows, count(*) FILTER (WHERE b.expected IS NULL) AS unknown,
       CASE WHEN bool_or(b.expected IS NULL) THEN NULL ELSE
         (SELECT count(*) FROM bins x CROSS JOIN LATERAL jsonb_each(x.expected) AS c(k,v)
          WHERE x.day = b.day AND (%s = '' OR v ->> 2 = %s)) END AS e,
       (SELECT count(*) FROM cells c WHERE c.day = b.day AND c.received) AS o,
       (SELECT count(*) FROM cells c WHERE c.day = b.day AND c.valid) AS v,
       count(firsts.id) AS first_windows
FROM bins b LEFT JOIN firsts ON firsts.id = b.id
GROUP BY b.day ORDER BY b.day
"""
TERMINAL_SQL = """
SELECT ((w.target_end - interval '1 microsecond') AT TIME ZONE 'Asia/Shanghai')::date AS day,
       count(*), count(*) FILTER (WHERE r.request_state = 'responded'),
       count(r.started_at - r.created_at), avg(extract(epoch FROM r.started_at - r.created_at)),
       count(r.finished_at - r.started_at), avg(extract(epoch FROM r.finished_at - r.started_at)),
       count(r.scheduled_for), count(r.started_at - r.scheduled_for),
       avg(extract(epoch FROM r.started_at - r.scheduled_for))
FROM collection_run r JOIN collection_workwindow w ON w.id = r.window_id
WHERE w.product_id = %s AND r.status IN ('finished','failed','paused','expired','coalesced')
GROUP BY day ORDER BY day
"""


def compare_coverage(product, data, indicator=""):
    with observe_db() as db, db.cursor() as sql:
        sql.execute(LEDGER_SQL, [product, *([indicator] * 6)])
        expected = {row[0].isoformat(): tuple(row[1:]) for row in sql.fetchall()}
    for bucket in data["buckets"]:
        coverage = bucket["coverage"]
        first = bucket["first_observation_window_rate"]
        values = expected.get(bucket["date"], (0, 0, 0, 0, 0, 0))
        assert (
            bucket["window_count"],
            coverage["unknown_E_windows"],
            coverage["E"],
            coverage["O"],
            coverage["V"],
            first["numerator"],
        ) == values
        count, _unknown, e, o, v, new = values
        assert first == {
            "numerator": new,
            "denominator": count,
            "ratio": new / count if count else None,
        }
        assert coverage["received"]["ratio"] == (o / e if e else None)
        assert coverage["valid"]["ratio"] == (v / e if e else None)
        assert bucket["source_publication_delay"] is None


def compare_attempts(product, data):
    with observe_db() as db, db.cursor() as sql:
        sql.execute(TERMINAL_SQL, [product])
        expected = {row[0].isoformat(): tuple(row[1:]) for row in sql.fetchall()}
    for bucket in data["buckets"]:
        row = expected.get(bucket["date"], (0, 0, 0, None, 0, None, 0, 0, None))
        total, responded, queue_n, queue_avg, exec_n, exec_avg, sched_n, sched_known, sched_avg = (
            row
        )
        assert bucket["response_attempt_rate"] == {
            "numerator": responded,
            "denominator": total,
            "ratio": responded / total if total else None,
        }
        for label, samples, known, avg in (
            ("queue_wait", total, queue_n, queue_avg),
            ("execution", total, exec_n, exec_avg),
            ("schedule_delay", sched_n, sched_known, sched_avg),
        ):
            assert bucket[label] == {
                "sample_count": samples,
                "known_count": known,
                "mean_seconds": float(avg) if samples and known == samples else None,
            }


def test_v17_trends_match_independent_sql(
    django_user_model, monkeypatch, settings, record_property, http_witness
):
    operator = account(django_user_model, "trends", superuser=True)
    job, _attempts = seed_windows(operator, monkeypatch, settings)
    product = job.product
    query = {"product": product.pk, "scope": "synthetic", **BOUNDS}
    with loopback(operator) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        full = success(web.get(TRENDS, params=query))
        compare_coverage(product.pk, full)
        compare_attempts(product.pk, full)
        assert [row["window_count"] for row in full["buckets"]] == [3, 2, 0]
        assert full["buckets"][0]["window_results"] == {"new": 1, "partial": 1, "failed": 1}
        assert full["buckets"][1]["window_results"]["no_new"] == 1
        assert full["buckets"][1]["coverage"]["E"] is None
        over = web.get(TRENDS, params=query | {"end": "2024-03-01"})
        assert over.status_code == 400 and over.json() == {"code": "query_too_large"}

        only_aqi = grant(operator, product, "review", indicator="AQI", object_code="123")
        broad = product.usegrant_set.get(operation="review", indicator="*")
        revoke_grant(operator.pk, broad.pk, "F review narrowed to one object and field")
        partial = success(
            web.get(TRENDS, params=query | {"object_code": "123", "indicator": "AQI"})
        )
        compare_coverage(product.pk, partial, indicator="AQI")
        for row in partial["buckets"]:
            assert row["partially_authorized_windows"] == row["window_count"]
            assert row["window_results"] == row["terminal_attempt_states"] == {}
            assert row["response_attempt_rate"] == {"numerator": 0, "denominator": 0, "ratio": None}
        revoke_grant(operator.pk, only_aqi.pk, "F review withdrawn")
        denied = web.get(TRENDS, params=query)
        hidden = web.get(TRENDS, params=query | {"product": product.pk + 9000})
        assert denied.status_code == hidden.status_code == 403
        assert denied.json() == hidden.json() == {"code": "use_denied"}
        witness(
            record_property,
            pg_at=clock(),
            full_buckets=full["buckets"],
            partial_buckets=partial["buckets"],
            withdrawn=denied.status_code,
        )


def page_request(web, link, **params):
    url = urlsplit(link)
    return web.get(url.path, params=dict(parse_qsl(url.query)) | params)


def test_admin_discovery_filters_before_paging_and_links(
    django_user_model, monkeypatch, settings, http_witness
):
    operator = account(django_user_model, "workbench", superuser=True)
    job, attempts = seed_windows(operator, monkeypatch, settings)
    CityIdentifier.objects.filter(city=job.city).update(active_period=TimestamptzRange(START, None))
    hidden_product = Source.objects.create(namespace="s07.discovery.hidden", publisher="F hidden")
    hidden_job = make_job(operator, hidden_product)
    assert Job.objects.count() == 3
    with loopback(operator) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        route = ADMIN + "collection-jobs/"
        jobs = success(web.get(route, params={"scope": "synthetic", "page_size": 1}))
        assert jobs["results"][0]["id"] == attempts[5].job_id
        job_cursor = jobs["page"]["next_cursor"]
        second = success(
            web.get(route, params={"scope": "synthetic", "page_size": 1, "cursor": job_cursor})
        )
        assert second["page"]["next_cursor"] is None
        (_job,) = second["results"]
        assert _job["id"] == job.pk
        assert success(web.get(_job["links"]["detail"]))["job"] == job.pk
        runs = success(page_request(web, _job["links"]["runs"], page_size=1))
        cursor = runs["page"]["next_cursor"]
        next_page = success(page_request(web, _job["links"]["runs"], page_size=1, cursor=cursor))
        assert runs["results"][0]["id"] != next_page["results"][0]["id"]
        assert success(web.get(runs["results"][0]["links"]["detail"]))["runs"]
        windows = success(web.get(_job["links"]["windows"]))
        assert len(windows["results"]) == 4
        window = windows["results"][0]
        assert success(web.get(window["links"]["detail"]))["window_id"] == window["id"]
        (event,) = success(web.get(window["links"]["quality_issues"]))["results"]
        assert event["code"] == "outside_target" and event["window"] == window["id"]
        assert success(web.get(window["links"]["disposition"]))["product"] == job.product_id
        wrong = page_request(web, _job["links"]["runs"], page_size=2, cursor=cursor)
        assert wrong.status_code == 400 and wrong.json() == {"code": "invalid_cursor"}
        assert {row["id"] for row in success(web.get(_job["links"]["runs"]))["results"]} == {
            str(attempt.pk) for attempt in attempts if attempt.job_id == job.pk
        }
        route = ADMIN + "collection-windows/"
        query = {"scope": "synthetic", "product": hidden_job.product_id}
        assert success(web.get(route, params=query)) == success(
            web.get(route, params=query | {"product": hidden_job.product_id + 9999})
        )

        grant(operator, job.product, "review", indicator="AQI", object_code="123")
        broad = job.product.usegrant_set.get(operation="review", indicator="*")
        revoke_grant(operator.pk, broad.pk, "F review now restricted")
        saved = success(page_request(web, _job["links"]["runs"], page_size=1, cursor=cursor))
        assert saved["results"] == [] and saved["page"]["next_cursor"] is None
        partial = success(
            web.get(
                route,
                params=query
                | {"product": job.product_id, "object_code": "123", "indicator": "AQI"},
            )
        )
        for row in partial["results"]:
            detail = success(web.get(row["links"]["detail"]))
            assert not row["full_scope"] and "runs" not in row["links"] and row["result"] is None
            assert detail["closed_at"] is detail["result"] is None and detail["runs"] == []
            assert all(item["indicator"] == "AQI" for item in detail["cells"])
        assert len(partial["results"]) == 5


def registered_stage(user, product):
    return StagedInput.objects.create(
        product=product,
        owner=user,
        state="ready",
        expires_at=clock() + timedelta(minutes=10),
        volume_generation=uuid4(),
    )


def import_payload(stage):
    return {
        "profile": import_uci.PROFILE,
        "stage_id": str(stage.pk),
        "reason": "historical_verification",
    }


def test_history_status_is_owned_or_reviewed_and_does_not_expose_staging(
    django_user_model, monkeypatch, http_witness
):
    admin = account(django_user_model, "history-owner-admin", superuser=True)
    a = account(django_user_model, "history-a")
    b = account(django_user_model, "history-b")
    reviewer = account(django_user_model, "history-reviewer")
    for user in (a, b):
        assign(user, "observations", "import_history")
    assign(reviewer, "observations", "review_observation")
    product = register_uci(admin.pk)
    staged = registered_stage(a, product)
    other = registered_stage(b, product)
    ImportAttempt.objects.create(
        product=product,
        actor=b,
        stage=other,
        state="failed",
        error_code="F_RESTRICTED",
        reason="synthetic",
    )

    def unconfirmed(attempt):
        ImportAttempt.objects.filter(pk=attempt.pk).update(dispatch="unconfirmed")
        return str(attempt.pk), "unconfirmed"

    monkeypatch.setattr(history, "publish", unconfirmed)
    with loopback(a) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        receipt = web.post(HISTORY, json=import_payload(staged))
        assert receipt.status_code == 503 and receipt.json()["code"] == "dispatch_unconfirmed"
        posting = receipt.json()["attempt_id"]
        assert web.post(HISTORY, json=import_payload(staged)).status_code == 403
        page = success(web.get(HISTORY, params={"page_size": 1}))
        assert [row["id"] for row in page["results"]] == [posting]
        assert page["page"]["next_cursor"] is None
        status = success(web.get(page["results"][0]["links"]["detail"]))
        assert status["state"] == "pending" and status["dispatch"] == "unconfirmed"
        assert str(staged.pk) not in str(status) and str(staged.volume_generation) not in str(
            status
        )
        assert web.get(f"{HISTORY}{other.importattempt.pk}/").status_code == 403
    # Inject the F dispatch success at the broker boundary without importing source data.
    from observations.tasks import process_history

    def sent(*, args, task_id, retry):
        assert args == [task_id] and not retry
        assert ImportAttempt.objects.get(pk=task_id, actor=b).state == "pending"

    monkeypatch.undo()
    monkeypatch.setattr(process_history, "apply_async", sent)
    ImportAttempt.objects.filter(stage=other).delete()
    with loopback(b) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        accepted = web.post(HISTORY, json=import_payload(other))
        assert accepted.status_code == 202
        assert ImportAttempt.objects.get(pk=accepted.json()["attempt_id"]).dispatch == "sent"
    with loopback(reviewer) as (web, _):
        web.event_hooks["response"].append(http_witness.inspect)
        page = success(web.get(HISTORY))
        assert {row["id"] for row in page["results"]} == {posting, accepted.json()["attempt_id"]}
        assert "F_RESTRICTED" not in str(page)
        review = product.usegrant_set.filter(operation="review").first()
        revoke_grant(admin.pk, review.pk, "F history review withdrawn")
        assert web.get(f"{HISTORY}{posting}/").status_code == 403


@pytest.mark.parametrize("mutation", ["source_withdrawn", "account_disabled"])
def test_history_enqueue_rechecks_winning_pg_commit(
    django_user_model, monkeypatch, mutation, record_property
):
    admin = account(django_user_model, "race-admin", superuser=True)
    operator = account(django_user_model, "racing-importer")
    assign(operator, "observations", "import_history")
    product = register_uci(admin.pk)
    staged = registered_stage(operator, product)
    entered, resume = Event(), Event()
    gate = "source_product" if mutation == "source_withdrawn" else "lock_users_and_groups"
    original = getattr(history, gate)
    attempted_publication = []

    def pause(*args):
        entered.set()
        assert resume.wait(4)
        return original(*args)

    monkeypatch.setattr(history, gate, pause)
    monkeypatch.setattr(history, "publish", lambda row: attempted_publication.append(row.pk))
    with loopback(operator) as (web, _), ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(web.post, HISTORY, json=import_payload(staged))
        try:
            assert entered.wait(4)
            if mutation == "source_withdrawn":
                grant_to_withdraw = product.usegrant_set.get(
                    operation="process_local", fields=["archive"]
                )
                revoke_grant(admin.pk, grant_to_withdraw.pk, "F before import lock")
            else:
                django_user_model.objects.filter(pk=operator.pk).update(is_active=False)
            committed = clock()
        finally:
            resume.set()
        answer = response.result(timeout=10)
        assert answer.status_code == 403, answer.text
        assert answer.json() == {
            "code": "use_denied" if mutation == "source_withdrawn" else "not_authenticated"
        }
    with observe_db() as db, db.cursor() as sql:
        sql.execute(
            "SELECT s.state, count(a.id) FROM observations_stagedinput s LEFT JOIN observations_importattempt a ON a.stage_id=s.id WHERE s.id=%s GROUP BY s.id",
            [staged.pk],
        )
        state, created = sql.fetchone()
    assert (state, created, attempted_publication) == ("ready", 0, [])
    witness(
        record_property,
        pg_at=clock(),
        mutation=mutation,
        winning_commit=committed.isoformat(),
        status=answer.status_code,
        created=created,
        stage_state=state,
    )
