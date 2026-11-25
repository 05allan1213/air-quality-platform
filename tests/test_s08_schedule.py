"""Approved F schedule transitions on real PostgreSQL and authenticated sessions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from uuid import uuid4

import pytest
import test_s06_accounts
from accounts.models import GovernanceAction
from accounts.research_session import SessionCheck
from collection import admission, s08_schedule, schedule_segments, services
from collection.models import ControlEvent, Job, Run, ScheduleTemplate, WorkWindow
from collection.s08_schedule import register_template
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from observations.models import CityIdentifier, Source, UseGrant
from observations.policy import Denied, clock
from psycopg.types.range import TimestamptzRange
from s02_support import START
from test_s06_accounts import denied, json_status, permission, sign_in

from tests.collection.test_collection_postgres import LIMITS, grant, make_job, request

pytestmark = pytest.mark.django_db(transaction=True)
sql_hook = test_s06_accounts.sql_hook
web_clients = test_s06_accounts.web_clients
JOBS = "/api/v1/admin/collection-jobs/"
PRODUCTS = "/api/v1/admin/catalog/products/"
CONFIGURE = "observations.configure_collection_schedule"
HOUR = timedelta(hours=1)


@pytest.fixture
def registered(settings, django_user_model, monkeypatch, web_clients):
    settings.S03_FIXTURE_HTTP = True
    host = django_user_model.objects.create_user("schedule-host")
    host.user_permissions.add(
        permission("observations.run_collection"),
        permission("observations.manage_collection"),
        permission("observations.manage_grants"),
    )
    source = Source.objects.create(namespace="s03.fixture", publisher="F", collection_limits=LIMITS)
    basis = make_job(host, source)
    CityIdentifier.objects.filter(city=basis.city).update(
        active_period=TimestamptzRange(START, None)
    )
    for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
        grant(host, basis.product, operation)
    template = register_template(host.pk, basis.pk, [3600, 7200, 10800], 0)
    operators = []
    for n in range(2):
        password = "schedule-test-password-for-" + str(n)
        operator = django_user_model.objects.create_user(f"schedule-editor-{n}", password=password)
        operator.user_permissions.add(permission(CONFIGURE))
        operators.append((operator, sign_in(web_clients(), operator, password)))
    deliveries = []
    monkeypatch.setattr(services, "publish", deliveries.append)
    first = clock().replace(minute=0, second=0, microsecond=0) + 2 * HOUR
    return host, basis, template, operators, first, deliveries


def command(action, version, **extra):
    reasons = {
        "create_draft": "schedule_create",
        "propose": "schedule_change",
        "activate": "schedule_activate",
        "discard": "schedule_discard",
    }
    return (
        dict(
            action=action,
            expected_version=version,
            request_id=str(uuid4()),
            reason_code=reasons[action],
        )
        | extra
    )


def detail(job):
    return f"{JOBS}{job}/schedule/"


def draft(web, template, due, cadence=3600):
    return command(
        "create_draft",
        template.version,
        template=template.pk,
        cadence_seconds=cadence,
        first_due_at=due.isoformat(),
    )


def decide(web, plan, action="activate", status=200):
    return json_status(
        web.post(
            detail(plan["job"]),
            json=command(
                action,
                plan["version"],
                template_version=plan["template_version"],
                expected_predecessor_version=plan["predecessor_version"],
            ),
        ),
        status,
    )


def propose(web, plan, due, cadence=7200, *, status=201):
    return json_status(
        web.post(
            detail(plan["job"]),
            json=command(
                "propose",
                plan["version"],
                template_version=plan["template_version"],
                cadence_seconds=cadence,
                first_due_at=due.isoformat(),
            ),
        ),
        status,
    )


def test_configure_only_and_original_receipt(registered):
    _, basis, template, [(editor, web), _], first, deliveries = registered
    response = web.get(PRODUCTS, params={"purpose": "configure", "scope": "synthetic"})
    results = json_status(response, 200)["results"]
    assert "no-store" in response.headers["Cache-Control"]
    assert [entry["template"] for entry in results] == [template.pk]
    assert results[0]["source"] == basis.product.source.namespace
    assert results[0]["job"] is None
    original = draft(web, template, first)
    plan = json_status(web.post(JOBS, json=original), 201)
    job = Job.objects.get(pk=plan["job"])
    assert not job.enabled and job.next_due_at is None and job.effective_from is None
    denied(web.post(JOBS, json=original | {"cadence_seconds": 7200}), "request_conflict", 409)
    denied(web.get(f"{JOBS}{job.pk}/"), "permission_denied")
    denied(
        web.post("/api/v1/admin/collection-runs/", json=request(job, target=first)),
        "permission_denied",
    )
    discarded = decide(web, plan, "discard")
    assert discarded["state"] == "discarded" and discarded["version"] > plan["version"]
    assert json_status(web.post(JOBS, json=original), 201) == plan
    replacement = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    assert replacement["job"] != plan["job"]
    editor.user_permissions.remove(permission(CONFIGURE))
    denied(web.post(JOBS, json=original), "permission_denied")
    assert not deliveries and not WorkWindow.objects.exists()
    assert GovernanceAction.objects.filter(actor=editor, target_kind="job").count() == 3
    assert basis.run_set.count() == 0


def test_superuser_needs_assigned_schedule_permission_for_commands_and_reads(registered):
    _, basis, template, [(editor, web), (_, peer)], first, deliveries = registered
    editor.is_superuser = True
    editor.save(update_fields=["is_superuser"])
    editor.user_permissions.clear()
    payload = draft(web, template, first)
    other = json_status(peer.post(JOBS, json=payload), 201)
    counts = (Job.objects.count(), GovernanceAction.objects.count(), ControlEvent.objects.count())
    options = {"purpose": "configure", "scope": "synthetic"}
    assert web.get(f"{JOBS}{basis.pk}/").status_code == 200
    denied(web.get(PRODUCTS, params=options), "permission_denied")
    denied(web.get(detail(other["job"])), "permission_denied")
    denied(web.post(JOBS, json=payload), "permission_denied")
    assert counts == (
        Job.objects.count(),
        GovernanceAction.objects.count(),
        ControlEvent.objects.count(),
    )

    group = Group.objects.create(name="Explicit schedule configuration")
    configure = permission(CONFIGURE)
    group.permissions.add(configure)
    editor.groups.add(group)
    assert (
        json_status(web.get(PRODUCTS, params=options), 200)["results"][0]["template"] == template.pk
    )
    assert json_status(web.get(detail(other["job"])), 200)["job"] == other["job"]
    discarded = decide(web, other, "discard")
    assert discarded["state"] == "discarded"
    counts = (Job.objects.count(), GovernanceAction.objects.count(), ControlEvent.objects.count())
    group.permissions.remove(configure)
    denied(web.get(PRODUCTS, params=options), "permission_denied")
    denied(web.get(detail(other["job"])), "permission_denied")
    denied(web.post(JOBS, json=payload), "permission_denied")
    assert counts == (
        Job.objects.count(),
        GovernanceAction.objects.count(),
        ControlEvent.objects.count(),
    )
    assert not deliveries and not Run.objects.exists()


def test_schedule_permission_is_rechecked_after_receipt_write(registered, monkeypatch):
    _, _, template, [(editor, web), _], first, deliveries = registered
    grant = permission(CONFIGURE)
    real_finish = SessionCheck.finish
    reached_finish = []

    def remove_before_finish(session, relations):
        reached_finish.append(GovernanceAction.objects.filter(actor=editor).count())
        editor.user_permissions.remove(grant)
        return real_finish(session, relations)

    monkeypatch.setattr(SessionCheck, "finish", remove_before_finish)
    before = set(Job.objects.values_list("pk", flat=True))
    before_actions = set(GovernanceAction.objects.values_list("pk", flat=True))
    before_events = set(ControlEvent.objects.values_list("pk", flat=True))
    denied(web.post(JOBS, json=draft(web, template, first)), "permission_denied")
    assert reached_finish == [1]
    assert set(Job.objects.values_list("pk", flat=True)) == before
    assert set(GovernanceAction.objects.values_list("pk", flat=True)) == before_actions
    assert set(ControlEvent.objects.values_list("pk", flat=True)) == before_events
    assert not deliveries and not Run.objects.exists()


def test_schedule_read_rejects_query_parameters(registered):
    _, _, template, [(_, web), _], first, _ = registered
    plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    path = detail(plan["job"])
    response = web.get(path, params={"scope": "synthetic"})
    denied(response, "invalid_request", 400)
    assert "no-store" in response.headers["Cache-Control"]


def test_isolated_schedule_gate_never_opens_collection_execution(registered, settings, monkeypatch):
    host, basis, template, [(editor, web), _], first, deliveries = registered
    settings.S03_FIXTURE_HTTP = False
    settings.S08_FIXTURE_SCHEDULE = True

    directory = json_status(
        web.get(PRODUCTS, params={"purpose": "configure", "scope": "synthetic"}), 200
    )
    assert [row["template"] for row in directory["results"]] == [template.pk]
    plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    assert plan["state"] == "draft"
    assert not Job.objects.get(pk=plan["job"]).enabled

    before = (Run.objects.count(), WorkWindow.objects.count())
    with pytest.raises(Denied, match="unregistered_profile"):
        services.submit(host.pk, request(basis, target=first))
    assert (Run.objects.count(), WorkWindow.objects.count()) == before
    assert deliveries == []

    from collection.adapters import transport

    def forbidden_fetch(*args, **kwargs):
        pytest.fail("S03 adapter reached while the execution gate is closed")

    monkeypatch.setattr(transport, "_fetch", forbidden_fetch)
    with pytest.raises(transport.WireError, match="unregistered_profile"):
        transport.fetch("C", "123", "Fixture City", first, "f-exact-v1")

    active = decide(web, plan)
    assert active["state"] == "active"
    assert (Run.objects.count(), WorkWindow.objects.count()) == before
    assert deliveries == []
    assert basis.run_set.count() == 0


@pytest.mark.parametrize("with_run", [False, True])
def test_future_window_blocks_activation(registered, with_run):
    host, basis, template, [(_, web), _], first, deliveries = registered
    plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    if with_run:
        run = services.submit(host.pk, request(basis, target=first))
        assert run.status == "failed" and run.request_state == "unsent"
    else:
        WorkWindow.objects.create(
            product=basis.product,
            scope_key=f"city:{basis.city_id}",
            profile=basis.profile,
            target_start=first,
            target_end=first + HOUR,
        )
    assert (
        json_status(web.get(detail(plan["job"])), 200)["preview"]["conflict"] == "registered_window"
    )
    assert decide(web, plan, status=409) == {"code": "precondition_conflict"}
    assert Job.objects.get(pk=plan["job"]).plan_state == "draft"
    assert WorkWindow.objects.count() == 1 and not deliveries


def test_unreviewable_takeover_never_checks_future_ledger(registered, monkeypatch):
    host, basis, template, [(_, web), _], first, deliveries = registered
    plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    hidden = first + 96 * HOUR
    grant(
        host,
        basis.product,
        "review",
        data_from=hidden.date(),
        data_until=(hidden + 24 * HOUR).date(),
        effect="deny",
    )

    def unexpected(*args):
        pytest.fail("a future window lookup requires authority for the entire takeover")

    monkeypatch.setattr(schedule_segments, "future_ledger", unexpected)
    for with_window in (False, True):
        if with_window:
            WorkWindow.objects.create(
                product=basis.product,
                scope_key=f"city:{basis.city_id}",
                profile=basis.profile,
                target_start=hidden,
                target_end=hidden + HOUR,
            )
        assert json_status(web.get(detail(plan["job"])), 200)["preview"]["conflict"] is None
        assert decide(web, plan, status=403) == {"code": "use_denied"}
    assert Job.objects.get(pk=plan["job"]).plan_state == "draft"
    assert not deliveries


def enroll_schedule(host, basis, first):
    stdout = StringIO()
    call_command(
        "s08_register_schedule_template",
        "--actor",
        str(host.pk),
        "--job",
        str(basis.pk),
        "--cadence",
        "3600",
        "--cadence",
        "14400",
        "--phase",
        first.isoformat(),
        stdout=stdout,
    )
    return stdout.getvalue().split()


@pytest.mark.parametrize("operation", ["review", "retain_normalized"])
@pytest.mark.parametrize("with_window", [False, True])
def test_host_enrollment_checks_open_data_scope_before_future_ledger(
    registered, monkeypatch, operation, with_window
):
    host, basis, template, _, first, deliveries = registered
    Job.objects.filter(pk=basis.pk).update(next_due_at=first)
    hidden = first + 96 * HOUR
    UseGrant.objects.filter(product=basis.product, operation=operation).update(
        data_until=hidden.date()
    )
    if with_window:
        WorkWindow.objects.create(
            product=basis.product,
            scope_key=f"city:{basis.city_id}",
            profile=basis.profile,
            target_start=hidden,
            target_end=hidden + HOUR,
        )

    def unexpected(*args):
        pytest.fail("host enrollment must prove authority for the entire takeover first")

    monkeypatch.setattr(schedule_segments, "future_ledger", unexpected)
    job_before = Job.objects.values().get(pk=basis.pk)
    template_before = list(ScheduleTemplate.objects.values())
    revisions_before = list(template.revisions.values())
    events_before = list(ControlEvent.objects.values())
    receipts_before = list(GovernanceAction.objects.values())
    with pytest.raises(CommandError, match="^use_denied$"):
        enroll_schedule(host, basis, first)
    assert Job.objects.values().get(pk=basis.pk) == job_before
    assert list(ScheduleTemplate.objects.values()) == template_before
    assert list(template.revisions.values()) == revisions_before
    assert list(ControlEvent.objects.values()) == events_before
    assert list(GovernanceAction.objects.values()) == receipts_before
    assert WorkWindow.objects.count() == int(with_window) and not deliveries


@pytest.mark.parametrize("outcome", ["eligible", "conflict", "expired"])
def test_host_enrollment_respects_current_grant_validity_and_window_conflicts(
    registered, monkeypatch, outcome
):
    host, basis, template, _, first, deliveries = registered
    Job.objects.filter(pk=basis.pk).update(next_due_at=first)
    expires = clock() + 48 * HOUR
    UseGrant.objects.filter(product=basis.product, operation="review").update(valid_until=expires)
    if outcome == "expired":
        monkeypatch.setattr(s08_schedule, "clock", lambda: expires)
    elif outcome == "conflict":
        WorkWindow.objects.create(
            product=basis.product,
            scope_key=f"city:{basis.city_id}",
            profile=basis.profile,
            target_start=first,
            target_end=first + HOUR,
        )
    if outcome == "eligible":
        assert enroll_schedule(host, basis, first) == [str(template.pk), str(template.version + 1)]
        basis.refresh_from_db()
        assert basis.plan_state == "active" and basis.enabled
        assert basis.schedule_anchor_at == basis.effective_from == basis.next_due_at == first
        assert basis.schedule_template_id == template.pk
    else:
        code = "use_denied" if outcome == "expired" else "precondition_conflict"
        with pytest.raises(CommandError, match=f"^{code}$"):
            enroll_schedule(host, basis, first)
        assert Job.objects.get(pk=basis.pk).plan_state == "legacy"
        assert ScheduleTemplate.objects.get(pk=template.pk).version == template.version
    assert not deliveries


@pytest.mark.parametrize("operation", ["acquire_online", "process_local"])
@pytest.mark.parametrize("verb", ["create_draft", "propose", "activate", "discard"])
def test_schedule_rechecks_execution_rights_at_transaction_exit(
    registered, monkeypatch, operation, verb
):
    _, basis, template, [(_, web), _], first, deliveries = registered
    if verb in ("activate", "discard"):
        draft_plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
    if verb == "propose":
        draft_plan = json_status(web.post(JOBS, json=draft(web, template, first)), 201)
        root = decide(web, draft_plan)
    expires = clock() + 48 * HOUR
    UseGrant.objects.filter(product=basis.product, operation=operation).update(valid_until=expires)
    real_finish = SessionCheck.finish
    after_session_check = []

    def finish(session, relations):
        now = real_finish(session, relations)
        after_session_check.append(True)
        return now

    def grant_clock():
        return expires if after_session_check else clock()

    monkeypatch.setattr(SessionCheck, "finish", finish)
    monkeypatch.setattr(admission, "clock", grant_clock)
    before = (Job.objects.count(), ControlEvent.objects.count(), GovernanceAction.objects.count())
    if verb == "create_draft":
        result = json_status(web.post(JOBS, json=draft(web, template, first)), 403)
    elif verb == "propose":
        result = propose(web, root, first + 2 * HOUR, status=403)
    else:
        result = decide(web, draft_plan, verb, status=200 if verb == "discard" else 403)
    assert after_session_check and not deliveries
    if verb == "discard":
        assert result["state"] == "discarded"
    else:
        assert result == {"code": "use_denied"}
        assert before == (
            Job.objects.count(),
            ControlEvent.objects.count(),
            GovernanceAction.objects.count(),
        )
        if verb == "activate":
            assert Job.objects.get(pk=draft_plan["job"]).plan_state == "draft"
        if verb == "propose":
            assert Job.objects.get(pk=root["job"]).pending_successor_id is None


def test_concurrent_predecessor_proposal_is_serialized(registered):
    _, _, template, operators, first, _ = registered
    web = operators[0][1]
    root = decide(web, json_status(web.post(JOBS, json=draft(web, template, first)), 201))
    simultaneous = Barrier(2, timeout=4)

    def contender(client, cadence):
        args = command(
            "propose",
            root["version"],
            template_version=template.version,
            cadence_seconds=cadence,
            first_due_at=(first + 2 * HOUR).isoformat(),
        )
        simultaneous.wait()
        return client.post(detail(root["job"]), json=args)

    with ThreadPoolExecutor(max_workers=2) as threads:
        a = threads.submit(contender, operators[0][1], 7200)
        b = threads.submit(contender, operators[1][1], 10800)
        responses = [a.result(timeout=6), b.result(timeout=6)]
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert Job.objects.filter(predecessor_id=root["job"], plan_state="draft").count() == 1
    winner = next(response.json() for response in responses if response.status_code == 201)
    release = decide(web, winner, "discard")
    assert release["predecessor_version"] > winner["predecessor_version"]
    current = json_status(web.get(detail(root["job"])), 200)
    assert current["pending_successor"] is None
    assert propose(web, current, first + 3 * HOUR)["job"] != winner["job"]


def test_first_read_and_rejected_writes_do_not_settle_handoff(registered, monkeypatch, web_clients):
    host, _, template, [(_, web), _], first, deliveries = registered
    root = decide(web, json_status(web.post(JOBS, json=draft(web, template, first)), 201))
    boundary = first + 2 * HOUR
    successor = decide(web, propose(web, root, boundary))
    old = Job.objects.get(pk=root["job"])
    job = Job.objects.get(pk=successor["job"])
    before_version = job.control_version
    host.set_password("disposable schedule handoff password")
    host.save(update_fields=["password"])
    # Open a separate real Session for the control and manual endpoints.
    manager = sign_in(web_clients(), host, "disposable schedule handoff password")
    services.change_job(host.pk, old.pk, "pause")
    ControlEvent.objects.filter(job=old, action="pause").update(
        occurred_at=boundary - timedelta(minutes=5)
    )
    now = boundary + timedelta(minutes=5)
    monkeypatch.setattr(services, "clock", lambda: now)
    real_finish = SessionCheck.finish

    def finish(session, relations):
        real_finish(session, relations)
        return now

    monkeypatch.setattr(SessionCheck, "finish", finish)
    path = f"{JOBS}{job.pk}/"
    event_count = ControlEvent.objects.count()
    action_count = GovernanceAction.objects.count()

    assert json_status(web.get(detail(job.pk)), 200)["version"] == before_version + 1
    target = json_status(manager.get(path), 200)["targets"]["job"]
    assert (target["state"], target["reason"], target["control_version"]) == (
        "paused",
        "predecessor_paused",
        before_version + 1,
    )
    denied(
        manager.post("/api/v1/admin/collection-runs/", json=request(job, target=boundary)),
        "use_denied",
    )
    stale = {
        "action": "resume",
        "request_id": str(uuid4()),
        "expected_version": before_version,
        "reason_code": "operator_resume",
    }
    denied(manager.post(path, json=stale), "precondition_conflict", 409)
    job.refresh_from_db()
    assert job.enabled and not job.handoff_checked and job.control_version == before_version
    assert ControlEvent.objects.count() == event_count
    assert GovernanceAction.objects.count() == action_count
    assert not Run.objects.exists() and not deliveries

    services.scheduled(job.pk)
    services.scheduled(job.pk)
    job.refresh_from_db()
    assert not job.enabled and job.handoff_checked and job.control_version == before_version + 1
    assert job.pause_reason == "predecessor_paused"
    assert ControlEvent.objects.filter(job=job, action="auto_pause").count() == 1
    assert not Run.objects.exists() and not deliveries
    assert json_status(manager.get(path), 200)["targets"]["job"] == target
    restored = stale | {"request_id": str(uuid4()), "expected_version": target["control_version"]}
    assert json_status(manager.post(path, json=restored), 200)["enabled"]
    services.scheduled(job.pk)
    assert Run.objects.filter(job=job, scheduled_for=boundary).count() == 1


def test_fixed_phase_late_delivery_and_pause_at_boundary(registered, monkeypatch):
    host, _, template, [(_, web), _], first, deliveries = registered
    root = decide(web, json_status(web.post(JOBS, json=draft(web, template, first)), 201))
    boundary = first + 2 * HOUR
    next_plan = decide(web, propose(web, root, boundary))
    second_boundary = boundary + 3 * HOUR
    final = decide(web, propose(web, next_plan, second_boundary, 10800))
    root_job = Job.objects.get(pk=root["job"])
    new_job = Job.objects.get(pk=next_plan["job"])
    final_job = Job.objects.get(pk=final["job"])
    fake = [first]
    monkeypatch.setattr(services, "clock", lambda: fake[0])
    services.scheduled(root_job.pk)
    payload = request(root_job, target=first)
    original = services.submit(host.pk, payload)
    with pytest.raises(Denied, match="precondition_conflict"):
        services.submit(host.pk, request(new_job, target=boundary))
    fake[0] = boundary - timedelta(minutes=5)
    services.change_job(host.pk, root_job.pk, "pause")
    ControlEvent.objects.filter(job=root_job, action="pause").update(occurred_at=fake[0])
    fake[0] = boundary + timedelta(minutes=5)
    services.change_job(host.pk, root_job.pk, "resume")
    ControlEvent.objects.filter(job=root_job, action="resume").update(occurred_at=fake[0])
    # An outage crosses both boundaries. The late resume does not change the old state at B.
    fake[0] = second_boundary + timedelta(minutes=10)
    services.scheduled(final_job.pk)
    services.scheduled(new_job.pk)
    services.scheduled(root_job.pk)
    for job in (root_job, new_job, final_job):
        job.refresh_from_db()
    assert root_job.enabled and root_job.next_due_at is None
    assert not new_job.enabled and not final_job.enabled
    assert new_job.pause_reason == final_job.pause_reason == "predecessor_paused"
    assert Run.objects.filter(reason="scheduled").count() == 2
    assert set(Run.objects.filter(reason="scheduled").values_list("scheduled_for", flat=True)) == {
        first,
        first + HOUR,
    }
    assert services.submit(host.pk, payload).pk == original.pk
    assert services.claim(original.pk) is not None
    with pytest.raises(Denied, match="precondition_conflict"):
        services.submit(host.pk, request(root_job, target=boundary))
    assert not WorkWindow.objects.filter(target_start__gte=boundary).exists()
    services.change_job(host.pk, final_job.pk, "resume")
    old_version = Job.objects.get(pk=final_job.pk).control_version
    services.scheduled(final_job.pk)
    final_job.refresh_from_db()
    assert final_job.control_version == old_version
    assert final_job.next_due_at == second_boundary + 3 * HOUR
    assert Run.objects.filter(job=final_job, scheduled_for=second_boundary).count() == 1
    assert len(deliveries) == 4
