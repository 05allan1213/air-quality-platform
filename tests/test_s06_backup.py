"""V13/V14/C01 with independently authenticated sessions, files, and PostgreSQL."""

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from hashlib import sha256
from http.client import HTTPConnection
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import Event
from uuid import UUID, uuid4

import pytest
import s06_restore_http
from accounts.models import GovernanceAction
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from observations.backup import BASE_FIELDS, paths, sweep_orphans
from observations.backup_authority import ZERO
from observations.backup_models import BackupSegment, BackupSubject, RestoreIntent
from observations.backup_restore import run
from observations.controlled_storage import register_volume
from observations.disposition import reconcile_product
from observations.models import MappingLink, StationObservation, UseGrant
from observations.policy import Denied, clock, revoke_grant
from psycopg import sql
from s02_support import observe_db
from s05_support import HOUR, START, candidate, grant, ingest, product, site
from s06_restore_custodian import request as custody_request
from s06_restore_http import release
from s06_restore_target import ALIAS, bootstrap_target, install_target
from s06_witness import witness
from test_s05_races import blocked_by, in_thread, pid
from test_s06_accounts import json_status, sign_in

pytest_plugins = ("test_s06_accounts",)
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not os.environ.get("S06_AUTHORITY_FIXTURE"),
        reason="use s06_restore_verify.py",
    ),
]
API = "/api/v1/admin/"
SCOPE = {
    "geography": "F-city",
    "object_code": "001",
    "indicator": "CO",
    "start": "2024-01-01",
    "end": "2024-01-02",
}


def private_file(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


@pytest.fixture
def archives(tmp_path, settings, identities, web_clients):
    owner, _, _, custodian, password = identities
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o700)
    mount = Path(os.environ["S06_AUTHORITY_FIXTURE"])
    authority = mount / uuid4().hex
    settings.S06_BACKUP_DIR = str(backup)
    settings.S06_AUTHORITY_DIR = str(authority)
    key = Path(os.environ["S06_WORK_ROOT"]) / "stage" / "custody.sock"
    public = (mount / "public.pem").read_bytes()
    subject = BackupSubject.objects.create(user=custodian)
    a, b = product("backup-a"), product("backup-b")
    trust = {"custodian": custodian.pk, "key_id": sha256(public).hexdigest(), "anchors": {}}
    for p in (a, b):
        trust["anchors"][p.source.namespace] = {p.code: {"version": 0, "digest": ZERO}}
        site(p)
        grant(custodian, p, "process_local")
        grant(custodian, p, "retain_normalized", fields=BASE_FIELDS | {"raw_field"})
        grant(custodian, p, "restore", fields=BASE_FIELDS)
        ingest(custodian, p, [candidate(p, at=START + HOUR, token=10)])
    custody_request(
        key,
        authority.name,
        "configure",
        {
            "trust": trust,
            "subjects": {
                str(custodian.pk): {
                    "token": "subject-s06-custodian",
                    "username": custodian.username,
                    "identity": str(subject.identity),
                },
            },
        },
    )
    client = sign_in(web_clients(), custodian, password)
    normal = sign_in(web_clients(), owner, password)
    return a, b, custodian, backup, authority, key, client, normal


def sign_reviewed(key, authority, material):
    custody_request(key, authority.name, "sign", material)


def replace_publish(key, authority, segment, document):
    custody_request(
        key, authority.name, "replace_publish", {"operation": str(segment), "proof": document}
    )


def prepare(client, product, **body):
    return client.post(
        f"{API}backup-products/{product.pk}/segments/", json={"scope": SCOPE, **body}
    )


def publish(client, segment):
    return client.post(f"{API}backup-segments/{segment}/publish/", json={})


def proof(client, action, operation):
    return json_status(client.get(f"{API}backup-authority/{action}/{operation}/"), 200)


def make_available(client, source, key, authority):
    segment = json_status(prepare(client, source), 201)["operation_id"]
    sign_reviewed(key, authority, proof(client, "publish", segment))
    assert json_status(publish(client, segment), 200)["state"] == "available"
    return segment


def make_intent(client, segment, target):
    response = client.post(
        f"{API}backup-segments/{segment}/restore-intents/", json={"target": str(target)}
    )
    return UUID(json_status(response, 201)["operation_id"])


def target_empty():
    with connections[ALIAS].cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM observations_stationrevision), "
            "(SELECT count(*) FROM auth_user), (SELECT count(*) FROM django_session)"
        )
        counts = cursor.fetchone()
        assert counts == (0, 0, 0)
    connections[ALIAS].close()
    return sum(counts)


@pytest.fixture
def restore_target(django_db_blocker):
    with django_db_blocker.unblock():
        try:
            # pytest creates the database after the outer runner provisions PG.
            # Only the owner of this guarded test DB may adjust its ACL.
            install_target()
            test_db = connection.settings_dict["NAME"]
            assert test_db.startswith("test_s06_db_")
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(test_db)
                    )
                )
            bootstrap_target()
            yield install_target()
        finally:
            if ALIAS in connections.databases:
                connections[ALIAS].close()
                del connections[ALIAS]
                del connections.databases[ALIAS]


def test_v13_legal_sibling_survives_revocation_and_regrant(archives):
    a, b, custodian, backup, authority, key, client, normal = archives
    assert prepare(normal, a).status_code == 403
    assert prepare(client, a).status_code == 403
    assert not list(backup.iterdir())
    left, right = [grant(custodian, p, "backup", fields=BASE_FIELDS) for p in (a, b)]
    assert prepare(client, a, raw=True).status_code == 403
    assert not list(backup.iterdir())
    old = json_status(prepare(client, a), 201)["operation_id"]
    sign_reviewed(key, authority, proof(client, "publish", old))
    available = make_available(client, b, key, authority)
    revoke_grant(custodian.pk, left.pk, "fixture withdrawal")
    grant(custodian, a, "backup", fields=BASE_FIELDS)
    assert publish(client, old).status_code in {403, 409}
    assert BackupSegment.objects.get(pk=old).state == "blocked"
    assert all(not path.exists() for path in paths(backup, available)[:1])
    reconcile_product(a.pk)
    assert all(not path.exists() for path in paths(backup, old))
    assert paths(backup, available)[1].is_file()
    assert BackupSegment.objects.get(pk=old).manifest is None
    revoke_grant(custodian.pk, right.pk, "fixture withdrawal")
    reconcile_product(b.pk)
    assert not list(backup.iterdir())


def test_v13_unsigned_or_changed_material_never_opens_segment(archives):
    a, _, custodian, backup, authority, key, client, _ = archives
    grant(custodian, a, "backup", fields=BASE_FIELDS)
    with pytest.raises(OSError) as mount_write:
        (authority / "trust.json").open("r+b")
    assert mount_write.value.errno == errno.EROFS
    for path in (Path(os.environ["S06_PRIVATE_KEY_PROBE"]), authority / "offline.pem"):
        with pytest.raises(FileNotFoundError):
            path.stat()
    segment = json_status(prepare(client, a), 201)["operation_id"]
    assert publish(client, segment).status_code == 403
    sign_reviewed(key, authority, proof(client, "publish", segment))
    path = authority / f"{segment}.publish.json"
    grant(custodian, a, "display")
    assert publish(client, segment).status_code == 403
    sign_reviewed(key, authority, proof(client, "publish", segment))
    legal = path.read_bytes()
    custody_request(key, authority.name, "replace_signer", {"operation": segment})
    assert publish(client, segment).status_code == 403
    assert not paths(backup, segment)[1].exists()
    doc = json.loads(legal)
    doc["chain"].pop(0)
    replace_publish(key, authority, segment, doc)
    assert publish(client, segment).status_code == 403
    doc = json.loads(legal)
    doc["seal"]["payload"]["scope"]["indicator"] = "NO2"
    replace_publish(key, authority, segment, doc)
    assert publish(client, segment).status_code == 403
    assert not paths(backup, segment)[1].exists()
    replace_publish(key, authority, segment, json.loads(legal))
    assert publish(client, segment).status_code == 200
    orphan = backup / "0b4bfcda-189f-4d58-9070-55cda589116e.partial"
    private_file(orphan, b"injected fixture orphan")
    malformed_orphan = backup / "unfinished.partial"
    private_file(malformed_orphan, b"injected fixture orphan")
    sweep_orphans()
    assert not orphan.exists()
    assert not malformed_orphan.exists()


def test_v13_current_proof_requires_committed_registration_witness(archives):
    a, _, custodian, backup, authority, key, client, _ = archives
    basis = grant(custodian, a, "backup", fields=BASE_FIELDS)
    segment = json_status(prepare(client, a), 201)["operation_id"]
    material = proof(client, "publish", segment)
    registered = next(
        foot["registered_at"]
        for foot in material["current"]["rights"]["grants"]
        if foot["pk"] == basis.pk
    )
    assert datetime.fromisoformat(registered) <= clock()
    sign_reviewed(key, authority, material)
    with pytest.raises(IntegrityError) as err, transaction.atomic():
        UseGrant.objects.filter(pk=basis.pk).update(registered_at=None)
    assert err.value.__cause__.sqlstate == "23514"

    def verify_unwitnessed_commit():
        assert UseGrant.objects.get(pk=extra.pk).registered_at is None
        response = client.get(f"{API}backup-authority/publish/{segment}/")
        assert json_status(response, 403) == {"code": "authority_invalid"}
        assert publish(client, segment).status_code == 403
        assert not paths(backup, segment)[1].exists()

    # The app's normal registration callback stamps the row after this observer.
    # The HTTP session can see the committed grant during that interval.
    with transaction.atomic():
        transaction.on_commit(verify_unwitnessed_commit)
        extra = grant(custodian, a, "display")
    assert UseGrant.objects.get(pk=extra.pk).registered_at <= clock()
    assert proof(client, "publish", segment)["current"]["rights"] != material["current"]["rights"]
    assert publish(client, segment).status_code == 403
    assert not paths(backup, segment)[1].exists()


def test_v13_missing_volume_is_pending_and_publish_loss_is_closed(archives, sql_hook, settings):
    a, _, custodian, backup, authority, key, client, _ = archives
    basis = grant(custodian, a, "backup", fields=BASE_FIELDS)
    segment = json_status(prepare(client, a), 201)["operation_id"]
    sign_reviewed(key, authority, proof(client, "publish", segment))
    ended = Event()

    def lose_authority(execute, sql, params, many, context):
        if 'UPDATE "observations_backupsegment"' in sql and not ended.is_set():
            custody_request(key, authority.name, "delete_publish", {"operation": segment})
            ended.set()
        return execute(sql, params, many, context)

    sql_hook["lose-authority-at-publish"] = lose_authority
    reply = client.post(
        f"{API}backup-segments/{segment}/publish/",
        json={},
        headers={"X-S06-Test-Hook": "lose-authority-at-publish"},
    )
    assert ended.is_set()
    assert json_status(reply, 403) == {"code": "authority_invalid"}
    assert BackupSegment.objects.get(pk=segment).state == "preparing"
    revoke_grant(custodian.pk, basis.pk, "F withdrawal before volume loss")
    missed = backup.parent / "wrong-volume"
    missed.mkdir(mode=0o700)
    register_volume(missed)
    settings.S06_BACKUP_DIR = str(missed)
    with pytest.raises(OSError, match="storage_generation_mismatch"):
        reconcile_product(a.pk)
    assert BackupSegment.objects.get(pk=segment).state == "blocked"
    assert paths(backup, segment)[1].is_file()
    settings.S06_BACKUP_DIR = str(backup)
    reconcile_product(a.pk)
    assert BackupSegment.objects.get(pk=segment).state == "removed"
    assert not any(p.exists() for p in paths(backup, segment))


@pytest.mark.skipif(not os.environ.get("S06_RESTORE_PASSWORD"), reason="use s06_restore_verify.py")
def test_v14_one_new_session_reads_and_target_rolls_back(archives, restore_target):
    _, b, custodian, _, authority, key, client, _ = archives
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    ingest(custodian, b, [candidate(b, at=START + HOUR, token=11, value="12")])
    ingest(custodian, b, [candidate(b, at=START + HOUR, value="17")])
    segment = make_available(client, b, key, authority)
    target, guard = restore_target
    intent = make_intent(client, segment, target)
    with pytest.raises(Denied, match="authority_invalid"):
        run(intent, target, ALIAS, guard, release)
    sign_reviewed(key, authority, proof(client, "restore", intent))

    def checked_release(alias, read):
        with connections[alias].cursor() as cursor:
            cursor.execute(
                "SELECT watermark, trusted_scheme, disputed, qualification_version "
                "FROM observations_stationobservation"
            )
            assert cursor.fetchone() == (11, "sequence", True, 2)
            cursor.execute(
                "SELECT is_current, candidate, source_token, raw_value, source_description "
                "FROM observations_stationrevision ORDER BY revision_no"
            )
            assert cursor.fetchall() == [
                (False, False, 10, None, ""),
                (True, False, 11, None, ""),
                (False, True, None, None, ""),
            ]
            cursor.execute(
                "SELECT is_active, is_staff, is_superuser, left(password, 1) "
                "FROM auth_user WHERE username LIKE 'history-%%'"
            )
            assert cursor.fetchone() == (False, False, False, "!")
        return release(alias, read)

    result = run(intent, target, ALIAS, guard, checked_release)
    assert result == {"keys": 1, "revisions": 3, "verified": True, "http": [403, 204, 200, 403]}
    with pytest.raises(Denied, match="precondition_conflict"):
        run(intent, target, ALIAS, guard, release)
    target_empty()

    cli_intent = make_intent(client, segment, target)
    sign_reviewed(key, authority, proof(client, "restore", cli_intent))
    output = StringIO()
    call_command("s06_restore", str(cli_intent), stdout=output)
    assert json.loads(output.getvalue()) == result
    target_empty()

    # The exact same segment is checked against a genuinely distinct PG target.
    # An intent bound to target one must not open target two.
    wrong_target_intent = make_intent(client, segment, target)
    sign_reviewed(key, authority, proof(client, "restore", wrong_target_intent))
    del connections[ALIAS]
    bootstrap_target("2")
    second_target, second_guard = install_target("2")
    assert second_target != target
    with pytest.raises(Denied, match="precondition_conflict"):
        run(wrong_target_intent, second_target, ALIAS, second_guard, release)
    target_empty()
    second_intent = make_intent(client, segment, second_target)
    sign_reviewed(key, authority, proof(client, "restore", second_intent))
    assert run(second_intent, second_target, ALIAS, second_guard, checked_release) == result
    target_empty()


@pytest.mark.skipif(not os.environ.get("S06_RESTORE_PASSWORD"), reason="use s06_restore_verify.py")
@pytest.mark.parametrize(
    "failure",
    [
        "subject_reused",
        "outside_link_lost",
        "checksum",
        "authority_pg_lost",
        "subject_changed_after_import",
        "subject_rebound_before_import",
        "subject_rebound_after_import",
        "role_revoked_after_import",
    ],
)
def test_v14_failure_after_preflight_clears_every_target_row(
    archives, restore_target, failure, identities
):
    _, b, custodian, backup, authority, key, web, normal = archives
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    segment = make_available(web, b, key, authority)
    target, guard = restore_target
    if failure == "role_revoked_after_import":
        actor = identities[0]
        permission = Permission.objects.get(
            content_type__app_label="accounts", codename="restore_backup"
        )
        actor.user_permissions.add(permission)
        intent = make_intent(normal, segment, target)
    else:
        intent = make_intent(web, segment, target)
    sign_reviewed(key, authority, proof(web, "restore", intent))

    def rebind():
        alternate = identities[0]
        binding = BackupSubject.objects.create(user=alternate)
        custody_request(
            key,
            authority.name,
            "replace_subjects",
            {
                str(alternate.pk): {
                    "token": "subject-s06-custodian",
                    "username": alternate.username,
                    "identity": str(binding.identity),
                }
            },
        )

    if failure == "subject_rebound_before_import":
        rebind()
    if failure == "subject_reused":
        # The original integer PK now names someone else. No administrator may
        # stand in for that historical subject, even with the valid seal.
        BackupSubject.objects.filter(user=custodian).delete()
        BackupSubject.objects.create(user=custodian)
    elif failure == "checksum":
        final = paths(backup, segment)[1]
        payload = final.read_bytes()
        private_file(final, bytes([payload[0] ^ 1]) + payload[1:])

    def lose_link(alias, read):
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT count(*) FROM observations_stationrevision")
            assert cursor.fetchone() == (1,)
        if failure == "authority_pg_lost":
            victim = pid()
            with observe_db() as pg:
                assert pg.execute("SELECT pg_terminate_backend(%s)", [victim]).fetchone() == (True,)
        elif failure == "outside_link_lost":
            custody_request(key, authority.name, "delete_restore", {"operation": str(intent)})
        elif failure == "subject_changed_after_import":
            BackupSubject.objects.filter(user=custodian).delete()
            BackupSubject.objects.create(user=custodian)
        elif failure == "subject_rebound_after_import":
            rebind()
        elif failure == "role_revoked_after_import":
            actor.user_permissions.remove(permission)
        else:
            pytest.fail("this case must fail before target login")
        return release(alias, read)

    expected = {
        "subject_reused": "backup_history_subject_unmapped",
        "outside_link_lost": "restore_read_denied",
        "checksum": "backup_integrity_failed",
        "subject_changed_after_import": "restore_read_denied",
        "subject_rebound_before_import": "backup_history_subject_unmapped",
        "subject_rebound_after_import": "restore_read_denied",
        "role_revoked_after_import": "restore_read_denied",
    }
    if failure == "authority_pg_lost":
        with pytest.raises(DatabaseError):
            run(intent, target, ALIAS, guard, lose_link)
        connection.close()
    else:
        with pytest.raises(Denied, match=expected[failure]):
            run(intent, target, ALIAS, guard, lose_link)
    target_empty()
    assert not GovernanceAction.objects.filter(action="restore_read", request_id=intent).exists()
    expected_retry = (
        "permission_denied" if failure == "role_revoked_after_import" else "precondition_conflict"
    )
    with pytest.raises(Denied, match=expected_retry):
        run(intent, target, ALIAS, guard, release)


@pytest.mark.skipif(not os.environ.get("S06_RESTORE_PASSWORD"), reason="use s06_restore_verify.py")
@pytest.mark.parametrize("commit_delay", [False, True])
def test_v17_restore_commit_failure_releases_no_body(
    archives, restore_target, monkeypatch, commit_delay
):
    _, b, custodian, _, authority, key, web, _ = archives
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    segment = make_available(web, b, key, authority)
    target, guard = restore_target
    intent = make_intent(web, segment, target)
    material = proof(web, "restore", intent)
    if commit_delay:
        material["expiry"] = (clock() + timedelta(seconds=3)).isoformat()
    sign_reviewed(key, authority, material)
    responses = []

    class ObservedConnection(HTTPConnection):
        def getresponse(self):
            response = super().getresponse()
            original = response.read

            def observe(*args, **kwargs):
                body = original(*args, **kwargs)
                responses.append((response.status, len(body)))
                return body

            response.read = observe
            return response

    monkeypatch.setattr(s06_restore_http, "HTTPConnection", ObservedConnection)
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION s06_fail_restore_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF current_setting('s06.commit_delay', true) = 'on' THEN
                    PERFORM pg_sleep(4);
                    RETURN NULL;
                END IF;
                RAISE check_violation USING MESSAGE = 's06 fixture deferred restore commit';
            END;
            $$;
            CREATE CONSTRAINT TRIGGER s06_fail_restore_commit
                AFTER INSERT ON accounts_governanceaction DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW WHEN (NEW.action = 'restore_read')
                EXECUTE FUNCTION s06_fail_restore_commit();
        """)
        cursor.execute(
            "SELECT set_config('s06.commit_delay', %s, false)", ["on" if commit_delay else "off"]
        )
    try:
        if commit_delay:
            with pytest.raises(Denied, match="restore_read_denied"):
                run(intent, target, ALIAS, guard, release)
        else:
            with pytest.raises(DatabaseError) as failure:
                run(intent, target, ALIAS, guard, release)
            assert failure.value.__cause__.sqlstate == "23514"
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER s06_fail_restore_commit ON accounts_governanceaction")
            cursor.execute("DROP FUNCTION s06_fail_restore_commit()")
    assert responses == [(403, 0), (204, 0), (403 if commit_delay else 503, 0), (403, 0)]
    receipts = GovernanceAction.objects.filter(action="restore_read", request_id=intent)
    if commit_delay:
        assert receipts.get().outcome == "withheld"
    else:
        assert not receipts.exists()
    assert RestoreIntent.objects.get(pk=intent).consumed_at is not None
    target_empty()


@pytest.mark.parametrize("phase", ["publish", "restore"])
def test_v17_successful_commit_observed_after_cutoff_is_withheld(
    archives, restore_target, sql_hook, monkeypatch, phase
):
    _, b, custodian, backup, authority, key, web, _ = archives
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    target, guard = restore_target
    if phase == "publish":
        operation = segment = json_status(prepare(web, b), 201)["operation_id"]
    else:
        segment = make_available(web, b, key, authority)
        operation = make_intent(web, segment, target)
    action = "backup_publish" if phase == "publish" else "restore_read"
    cutoff = clock() + timedelta(seconds=3)
    material = proof(web, phase, operation)
    material["expiry"] = cutoff.isoformat()
    sign_reviewed(key, authority, material)
    actual_commits = []

    def delay_return(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith('INSERT INTO "accounts_governanceaction"') and action in params:
            db = context["connection"]
            actual_commit = db._commit

            def delay():
                db._commit = actual_commit
                actual_commit()
                with observe_db() as source:
                    assert source.execute(
                        "SELECT count(*) FROM accounts_governanceaction WHERE request_id=%s AND action=%s",
                        [operation, action],
                    ).fetchone() == (1,)
                    source.execute(
                        "SELECT pg_sleep(greatest(extract(epoch from %s - clock_timestamp()), 0) + 0.02)",
                        [cutoff],
                    )
                actual_commits.append(True)

            db._commit = delay
        return result

    responses = []

    class ObservedConnection(HTTPConnection):
        def getresponse(self):
            response = super().getresponse()
            responses.append((response.status, response.getheader("Content-Length")))
            return response

    monkeypatch.setattr(s06_restore_http, "HTTPConnection", ObservedConnection)
    if phase == "publish":
        sql_hook["commit-return-delay"] = delay_return
        result = web.post(
            f"{API}backup-segments/{operation}/publish/",
            json={},
            headers={"X-S06-Test-Hook": "commit-return-delay"},
        )
        assert json_status(result, 403) == {"code": "authority_invalid"}
        assert BackupSegment.objects.get(pk=operation).state == "blocked"
        reconcile_product(b.pk)
        assert not any(path.exists() for path in paths(backup, operation))
    else:
        with connection.execute_wrapper(delay_return):
            with pytest.raises(Denied, match="restore_read_denied"):
                run(operation, target, ALIAS, guard, release)
        assert responses == [(403, "0"), (204, "0"), (403, "0"), (403, "0")]
        target_empty()
    assert actual_commits == [True]
    assert GovernanceAction.objects.get(action=action, request_id=operation).outcome == "withheld"


def test_v13_catalog_dependency_never_partially_archives(archives):
    a, _, custodian, backup, _, _, client, _ = archives
    grant(custodian, a, "backup", fields=BASE_FIELDS, indicator="CO")
    reply = prepare(client, a)
    assert reply.status_code == 403
    assert not list(backup.iterdir())
    grant(custodian, a, "backup", fields=BASE_FIELDS)
    # The predecessor belongs to another key outside the selected indicator.
    ingest(custodian, a, [candidate(a, indicator="NO2", at=START + HOUR, token=10)])
    keys = {row.indicator: row for row in StationObservation.objects.filter(product=a)}
    MappingLink.objects.create(
        product=a,
        station_old=keys["NO2"],
        station_new=keys["CO"],
        rule=2,
        actor=custodian,
        reason="F cross-key mapping dependency",
    )
    assert json_status(prepare(client, a), 403) == {"code": "backup_dependency_unresolved"}
    assert not BackupSegment.objects.filter(product=a).exists()
    assert not list(backup.iterdir())


@pytest.mark.skipif(not os.environ.get("S06_RESTORE_PASSWORD"), reason="use s06_restore_verify.py")
def test_v14_rejects_no_right_or_current_external_request(archives, restore_target, monkeypatch):
    _, b, custodian, backup, authority, key, web, _ = archives
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    segment = make_available(web, b, key, authority)
    target, guard = restore_target
    intent = make_intent(web, segment, target)
    sign_reviewed(key, authority, proof(web, "restore", intent))
    document = json.loads((authority / f"{intent}.restore.json").read_bytes())
    duplicate = make_intent(web, segment, target)
    private_file(backup / f"{duplicate}.restore.json", json.dumps(document).encode("ascii"))

    def reject_target(*args):
        pytest.fail("preflight must reject before opening target")

    stale = proof(web, "restore", intent)
    stale["observed_at"] = (
        datetime.fromisoformat(stale["observed_at"]) - timedelta(minutes=3)
    ).isoformat(timespec="microseconds")
    sign_reviewed(key, authority, stale)
    with pytest.raises(Denied, match="authority_invalid"):
        run(intent, target, ALIAS, reject_target, release)
    sign_reviewed(key, authority, proof(web, "restore", intent))
    with pytest.raises(Denied, match="authority_invalid"):
        run(duplicate, target, ALIAS, reject_target, release)
    custody_request(
        key, authority.name, "replace_restore", {"operation": str(duplicate), "proof": document}
    )
    with pytest.raises(Denied, match="authority_invalid"):
        run(duplicate, target, ALIAS, reject_target, release)
    connection.close()
    with monkeypatch.context() as unavailable:
        unavailable.setitem(connection.settings_dict, "HOST", str(backup / "missing-pg-socket"))
        with pytest.raises(DatabaseError):
            run(intent, target, ALIAS, reject_target, release)
    connection.close()
    assert RestoreIntent.objects.filter(pk__in=[intent, duplicate], consumed_at=None).count() == 2
    assert run(intent, target, ALIAS, guard, release)["http"] == [403, 204, 200, 403]
    restore = b.usegrant_set.get(operation="restore", revoked_at=None)
    revoke_grant(custodian.pk, restore.pk, "F revoke restore only")
    assert (
        web.post(
            f"{API}backup-segments/{segment}/restore-intents/", json={"target": str(target)}
        ).status_code
        == 403
    )
    assert paths(backup, segment)[1].is_file()
    target_empty()


def test_v17_c01_cutoff_crossed_while_publisher_waits(archives, sql_hook, record_property):
    a, _, custodian, backup, authority, key, web, _ = archives
    cutoff = clock() + timedelta(seconds=3)
    grant(custodian, a, "backup", fields=BASE_FIELDS, valid_until=cutoff, expiry_mode="recoverable")
    segment = json_status(prepare(web, a), 201)["operation_id"]
    sign_reviewed(key, authority, proof(web, "publish", segment))
    waiter = Queue()

    def report(execute, sql, params, many, context):
        if 'FROM "observations_product"' in sql and "FOR UPDATE" in sql:
            waiter.put(pid())
        return execute(sql, params, many, context)

    sql_hook["publish-until-cutoff"] = report
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            holder = pid()
            type(a).objects.select_for_update().get(pk=a.pk)
            future = pool.submit(
                web.post,
                f"{API}backup-segments/{segment}/publish/",
                json={},
                headers={"X-S06-Test-Hook": "publish-until-cutoff"},
            )
            blocked_by(waiter.get(timeout=3), holder)
            connection.cursor().execute(
                "SELECT pg_sleep(greatest(extract(epoch from %s - clock_timestamp()), 0) + 0.02)",
                [cutoff],
            )
        response = future.result(timeout=6)
        assert json_status(response, 403) == {"code": "backup_expired"}
    assert not paths(backup, segment)[1].exists()
    reconcile_product(a.pk)
    count = sum(file.exists() for file in paths(backup, segment))
    assert count == 0
    witness(
        record_property,
        pg_at=clock(),
        condition="C01",
        order="cutoff",
        lock_target="product",
        pg_lock_wait=True,
        cutoff_utc=cutoff.isoformat(),
        http=response.status_code,
        copy_files_after=count,
    )


def test_v17_c01_publication_waits_for_withdrawal_commit(archives, sql_hook, record_property):
    a, _, custodian, backup, authority, key, web, _ = archives
    basis = grant(custodian, a, "backup", fields=BASE_FIELDS)
    segment = json_status(prepare(web, a), 201)["operation_id"]
    sign_reviewed(key, authority, proof(web, "publish", segment))
    waiter = Queue()
    errors = Queue()

    def report(execute, sql, params, many, context):
        if 'FROM "observations_product"' in sql and "FOR UPDATE" in sql:
            waiter.put(pid())
        try:
            return execute(sql, params, many, context)
        except DatabaseError as exc:
            errors.put(getattr(exc.__cause__, "sqlstate", "unknown"))
            raise

    sql_hook["publish"] = report
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            holder = pid()
            revoke_grant(custodian.pk, basis.pk, "F withdrawal before publisher")
            grant(custodian, a, "backup", fields=BASE_FIELDS)
            assert BackupSegment.objects.get(pk=segment).state == "blocked"
            future = pool.submit(
                web.post,
                f"{API}backup-segments/{segment}/publish/",
                json={},
                headers={"X-S06-Test-Hook": "publish"},
            )
            blocked_by(waiter.get(timeout=3), holder)
            wait_at = clock()
        response = future.result(timeout=6)
        if response.status_code != 409:
            state = errors.get_nowait() if not errors.empty() else "none"
            raise type(
                f"UnexpectedPublishHTTP{response.status_code}_{state}", (AssertionError,), {}
            )()
    state = BackupSegment.objects.get(pk=segment).state
    assert state == "blocked"
    reconcile_product(a.pk)
    count = sum(file.exists() for file in paths(backup, segment))
    assert count == 0
    a.refresh_from_db(fields=["rights_version"])
    witness(
        record_property,
        pg_at=wait_at,
        condition="C01",
        order="withdrawal",
        lock_target="product",
        pg_lock_wait=True,
        http=response.status_code,
        segment_state=state,
        rights_version=a.rights_version,
        copy_files_after=count,
    )


def test_v17_c01_withdrawal_waits_for_publisher_commit(archives, monkeypatch, record_property):
    a, _, custodian, backup, authority, key, web, _ = archives
    basis = grant(custodian, a, "backup", fields=BASE_FIELDS)
    segment = json_status(prepare(web, a), 201)["operation_id"]
    sign_reviewed(key, authority, proof(web, "publish", segment))
    publisher, revoker = Queue(), Queue()
    continue_publish = Event()

    from observations import backup as backup_service

    record = backup_service.record_file_release

    def at_release(fd, identifier):
        publisher.put(pid())
        assert continue_publish.wait(3)
        return record(fd, identifier)

    def revoke():
        revoker.put(pid())
        revoke_grant(custodian.pk, basis.pk, "F withdrawal after publisher")
        grant(custodian, a, "backup", fields=BASE_FIELDS)

    monkeypatch.setattr(backup_service, "record_file_release", at_release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            web.post,
            f"{API}backup-segments/{segment}/publish/",
            json={},
        )
        try:
            holder = publisher.get(timeout=4)
            pending = pool.submit(in_thread, revoke)
            blocked_by(revoker.get(timeout=3), holder)
            wait_at = clock()
            # Both authority acknowledgements committed. The file still lacks
            # its release witness and the revoker waits on the final range lock.
            assert BackupSegment.objects.get(pk=segment).state == "available"
            assert backup_service.RELEASE_MARK not in os.listxattr(paths(backup, segment)[1])
        finally:
            continue_publish.set()
        response = future.result(timeout=5)
        assert json_status(response, 200)["state"] == "available"
        pending.result(timeout=6)
    state = BackupSegment.objects.get(pk=segment).state
    assert state == "blocked"
    reconcile_product(a.pk)
    count = sum(file.exists() for file in paths(backup, segment))
    assert count == 0
    a.refresh_from_db(fields=["rights_version"])
    witness(
        record_property,
        pg_at=wait_at,
        condition="C01",
        order="publication",
        lock_target="product",
        pg_lock_wait=True,
        http=response.status_code,
        segment_state=state,
        rights_version=a.rights_version,
        copy_files_after=count,
    )


@pytest.mark.skipif(not os.environ.get("S06_RESTORE_PASSWORD"), reason="use s06_restore_verify.py")
@pytest.mark.parametrize("withdraw_first", [True, False])
def test_v17_c01_restore_and_withdrawal_both_commit_orders(
    archives, restore_target, withdraw_first, record_property
):
    a, b, custodian, backup, authority, key, web, _ = archives
    basis = grant(custodian, a, "backup", fields=BASE_FIELDS)
    grant(custodian, b, "backup", fields=BASE_FIELDS)
    segment = make_available(web, a, key, authority)
    brother = make_available(web, b, key, authority)
    held = Queue()
    unpause = Event()

    target, guard = restore_target
    intent = make_intent(web, segment, target)
    sign_reviewed(key, authority, proof(web, "restore", intent))

    def controlled_target(cursor, target):
        guard(cursor, target)
        if withdraw_first:
            held.put("precheck")
            assert unpause.wait(4)

    def release_under_observation(alias, read):
        def observed_read(send):
            def observed_send(data):
                held.put(pid())
                assert unpause.wait(4)
                send(data)

            return read(observed_send)

        return release(alias, observed_read)

    def restore():
        return run(intent, target, ALIAS, controlled_target, release_under_observation)

    def withdraw():
        revoker.put(pid())
        revoke_grant(custodian.pk, basis.pk, "F withdrawal during restore")
        grant(custodian, a, "backup", fields=BASE_FIELDS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(in_thread, restore)
        holder = held.get(timeout=6)
        revoker = Queue()
        try:
            revoked = pool.submit(in_thread, withdraw)
            if withdraw_first:
                assert holder == "precheck"
                revoked.result(timeout=4)
            else:
                blocked_by(revoker.get(timeout=3), holder)
            observed_at = clock()
        finally:
            unpause.set()
        read_proof = {}
        if withdraw_first:
            with pytest.raises(Denied, match="precondition_conflict"):
                future.result(timeout=8)
        else:
            recovery = future.result(timeout=8)
            assert recovery["http"] == [403, 204, 200, 403]
            read_proof = {"restore_http": recovery["http"][2], "replay_http": recovery["http"][3]}
            revoked.result(timeout=8)
    reconcile_product(a.pk)
    count = sum(file.exists() for file in paths(backup, segment))
    assert count == 0
    target_empty()
    with pytest.raises(Denied, match="precondition_conflict"):
        run(intent, target, ALIAS, guard, release)
    buddy = make_intent(web, brother, target)
    sign_reviewed(key, authority, proof(web, "restore", buddy))
    sibling = run(buddy, target, ALIAS, guard, release)
    assert sibling["http"] == [403, 204, 200, 403]
    a.refresh_from_db(fields=["rights_version"])
    witness(
        record_property,
        pg_at=observed_at,
        condition="C01",
        order="withdrawal" if withdraw_first else "restore",
        lock_target="product",
        pg_lock_wait=not withdraw_first,
        sibling_http=sibling["http"][2],
        target_rows_after=target_empty(),
        copy_files_after=count,
        rights_version=a.rights_version,
        **read_proof,
    )
