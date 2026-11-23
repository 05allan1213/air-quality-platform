"""One-time restore intents and a rollback-only rehearsal in an owned target."""

from datetime import timedelta
from types import SimpleNamespace

from accounts.research_session import SessionCheck
from django.conf import settings
from django.db import DatabaseError, connections, transaction

from .backup import (
    RESTORE,
    audit,
    checked_file,
    confirm_release,
    parse_uuid,
    phase_timeout,
    require_no_publication,
    require_published,
    runtime_actor,
    segment_directory,
    segment_lock,
    verify_rights,
    withhold_receipt,
)
from .backup_authority import configured_custodian, verify_current
from .backup_import import ImportedSegment, manifest_matches
from .backup_models import RestoreIntent
from .policy import Denied

INTENT_TTL = timedelta(minutes=5)


@transaction.atomic(durable=True)
def prepare_restore(request, segment_id, target):
    phase_timeout()
    require_no_publication(segment_id)
    product, segment = segment_lock(segment_id)
    operator = runtime_actor(request.user.pk, RESTORE)
    check = SessionCheck(request)
    now = check.finish(())
    require_published(segment)
    verify_rights(product, segment, now, restore=True)
    intent = RestoreIntent.objects.create(
        segment=segment,
        actor=operator,
        session_id=check.key,
        target=parse_uuid(target),
        expires_at=min(now + INTENT_TTL, segment.expires_at),
    )
    audit(operator.pk, segment, "restore_prepare", "absent", "prepared", product)
    return intent


def lock_intent(intent_id, *, material_request=None):
    phase_timeout()
    segment_id = (
        RestoreIntent.objects.filter(pk=intent_id).values_list("segment_id", flat=True).first()
    )
    require_no_publication(segment_id)
    product, segment = segment_lock(segment_id)
    intent = RestoreIntent.objects.select_for_update().get(pk=intent_id)
    custodian = configured_custodian()
    requirements = [(custodian, "observations.manage_grants")]
    if material_request is not None:
        if material_request.user.pk != custodian:
            raise Denied("permission_denied")
    operator = runtime_actor(intent.actor_id, RESTORE, additional=requirements)
    # The operation number is not authentication. Validate the same DB session
    # that authenticated its issuance, including its generation and expiry.
    check = SessionCheck(
        SimpleNamespace(
            user=operator,
            COOKIES={settings.SESSION_COOKIE_NAME: intent.session_id},
            session=SimpleNamespace(session_key=intent.session_id),
        )
    )
    return product, segment, intent, check, custodian


def verify_intent(product, segment, intent, check, custodian, target, *, claimed=None):
    now = check.finish(())
    if intent.target != target or intent.consumed_at != claimed or intent.expires_at <= now:
        raise Denied("precondition_conflict")
    require_published(segment)
    verify_rights(product, segment, now, restore=True)
    proof = verify_current(
        product, segment, "restore", intent.pk, now, custodian=custodian, target=target
    )
    now = check.finish(())
    rights_until = verify_rights(product, segment, now, restore=True)
    deadline = min(proof.until, intent.expires_at, rights_until)
    if now >= deadline:
        raise Denied("authority_invalid")
    return now, deadline, proof


def claim(intent_id, target):
    with transaction.atomic(durable=True):
        product, segment, intent, check, custodian = lock_intent(intent_id)
        when, _, _ = verify_intent(product, segment, intent, check, custodian, target)
        intent.consumed_at = when
        intent.save(update_fields=["consumed_at"])
        return when


def run(intent_id, target, alias, check_target, release):
    """Run against a host-validated alias; no value or credential returns to CLI.

    The target transaction is never committed, including on process loss.
    The staged view can only be accessed through the private verifier in
    this process and remains invisible to every other target connection.
    """
    if alias == "default":
        raise Denied("restore_target_invalid")
    claimed = claim(intent_id, target)
    with transaction.atomic(durable=True, using=alias):
        with connections[alias].cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '3s'")
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
            check_target(cursor, target)
        with transaction.atomic(durable=True):
            product, segment, intent, check, custodian = lock_intent(intent_id)
            verify_intent(product, segment, intent, check, custodian, target, claimed=claimed)
            manifest = manifest_matches(product, segment)
            with (
                segment_directory(segment) as (fd, _, final),
                checked_file(fd, final, manifest) as stream,
            ):
                imported = ImportedSegment(alias, manifest)
                imported.load(stream)

        def read(send):
            # The HTTP handler can send data only after this commit succeeds.
            with transaction.atomic(durable=True):
                product, segment, intent, check, custodian = lock_intent(intent_id)
                verify_intent(product, segment, intent, check, custodian, target, claimed=claimed)
                imported.confirm_subjects()
                result = imported.read()
                action = audit(
                    intent.actor_id,
                    segment,
                    "restore_read",
                    "prepared",
                    "consumed",
                    product,
                    request_id=intent.pk,
                )
                action.outcome = "pending"
                action.save(update_fields=["outcome"])
                _, deadline, proof = verify_intent(
                    product, segment, intent, check, custodian, target, claimed=claimed
                )
                imported.confirm_subjects()
            try:
                imported.confirm_subjects()
                confirm_release(deadline, proof, intent_id, "restore_read")
                # The audit is durable. Keep the authority rows locked until the
                # single authorized response has been written on the private socket.
                with transaction.atomic(durable=True):
                    product, segment, intent, check, custodian = lock_intent(intent_id)
                    _, current_until, current = verify_intent(
                        product, segment, intent, check, custodian, target, claimed=claimed
                    )
                    imported.confirm_subjects()
                    proof.confirm()
                    current.confirm()
                    if check.finish(()) >= min(deadline, current_until):
                        raise Denied("authority_invalid")
                    send(result)
            except (DatabaseError, Denied, OSError):
                withhold_receipt(intent_id, "restore_read")
                raise

        result = release(alias, read)
        transaction.set_rollback(True, using=alias)
    return result
