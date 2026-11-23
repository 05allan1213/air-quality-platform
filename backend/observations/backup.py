"""Controlled segment preparation, publication, and physical disposition."""

import errno
import fcntl
import json
import os
import re
import stat
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from accounts.governance import receipt
from accounts.models import GovernanceAction
from accounts.research_session import SessionCheck
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection, transaction
from django.db.models import DateTimeField, Func

from .backup_authority import (
    authority_root,
    canonical,
    configured_custodian,
    identity,
    private_root,
    read_file,
    verify_current,
    wire,
)
from .backup_models import BackupSegment, BackupSubject
from .backup_projection import FORMAT, rows
from .controlled_storage import (
    bound_directory,
    confirm_directory,
    directory,
    file_entry,
    register_volume,
    unlink_from,
)
from .disposition import schedule
from .loss import require_continuous
from .models import Product, Source, Station
from .normalization import INDICATORS
from .policy import REQUIRED, Denied, Scope, applicable, clock, locked_catalog_product

MAX_KEYS = 10000
MAX_BYTES = 32 * 1024 * 1024
LIFETIME = timedelta(days=1)
PREPARATION_WINDOW = timedelta(minutes=5)
BASE_FIELDS = REQUIRED | {"catalog", "history_subject", "backup_manifest"}
MANAGE = "accounts.manage_backups"
RESTORE = "accounts.restore_backup"
RELEASE_MARK = "user.air_quality_backup_release"


def parse_uuid(string):
    try:
        identifier = UUID(string)
        if str(identifier) != string:
            raise ValueError
        return identifier
    except (ValueError, AttributeError, TypeError):
        raise Denied("invalid_request") from None


def string(value):
    return isinstance(value, str) and 0 < len(value) <= 100 and "\x00" not in value


def parse_scope(scope):
    try:
        if set(scope) != {"geography", "object_code", "indicator", "start", "end"}:
            raise ValueError
        if not all(
            string(scope[k]) and scope[k] != "*" for k in ("geography", "object_code", "indicator")
        ):
            raise ValueError
        result = Scope.from_issue(scope)
        if result.end <= result.start or result.end - result.start > timedelta(days=366):
            raise ValueError
        if result.serialize() != scope:
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, OverflowError):
        raise Denied("invalid_request") from None


def paths(root, segment_id):
    return root / f"{segment_id}.partial", root / f"{segment_id}.segment"


def access(product, operation, scope, fields, now, *, since=None):
    basis = applicable(product, operation, scope, fields, now)
    if since is not None:
        require_continuous(product, operation, scope, fields, now, since=since)
    return basis.until


def authorize(product, scope, fields, now, *, since=None, restore=False):
    operations = ["backup", "retain_normalized"]
    if restore:
        operations.append("restore")
    until = [access(product, op, scope, fields, now, since=since) for op in operations]
    return min((x for x in until if x is not None), default=None)


def runtime_actor(actor_id, permission, *, additional=()):
    requirements = ((actor_id, permission), *additional)
    actors = {
        actor.pk: actor
        for actor in get_user_model()
        .objects.select_for_update()
        .filter(pk__in={identifier for identifier, _ in requirements}, is_active=True)
        .order_by("pk")
    }
    groups = Group.objects.filter(user__pk__in=actors).values("pk")
    list(Group.objects.select_for_update().filter(pk__in=groups).order_by("pk"))
    if any(
        identifier not in actors or not actors[identifier].has_perm(right)
        for identifier, right in requirements
    ):
        raise Denied("permission_denied")
    return actors[actor_id]


def subjects():
    """Trusted custodian mapping from current authority IDs to opaque historical identities."""
    try:
        data = json.loads(read_file(authority_root() / "subjects.json", 65536))
        if not isinstance(data, dict) or len({x["token"] for x in data.values()}) != len(data):
            raise ValueError
        if any(
            not x["token"]
            or not re.fullmatch(r"[a-z0-9_-]{8,80}", x["token"])
            or set(x) != {"token", "username", "identity"}
            or not string(x["username"])
            or str(UUID(x["identity"])) != x["identity"]
            or not k.isdecimal()
            for k, x in data.items()
        ):
            raise ValueError
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        raise Denied("backup_history_subject_unmapped") from None
    return data


def checked_subjects():
    known = subjects()
    users = {
        str(user_id): (username, str(identifier))
        for user_id, username, identifier in BackupSubject.objects.filter(
            user_id__in=known
        ).values_list("user_id", "user__username", "identity")
    }
    return {
        key: {"token": row["token"], "identity": row["identity"]}
        for key, row in known.items()
        if users.get(key) == (row["username"], row["identity"])
    }


@contextmanager
def segment_directory(segment):
    try:
        root = private_root("S06_BACKUP_DIR")
    except Denied:
        raise OSError("backup_volume_unavailable") from None
    with bound_directory(root, segment.volume_generation) as fd:
        partial, final = paths(root, segment.pk)
        yield fd, partial.name, final.name


def segment_entry(fd, name):
    try:
        return file_entry(fd, name)
    except FileNotFoundError:
        return None


def lock_product(product_id):
    source_id = Product.objects.filter(pk=product_id).values_list("source_id", flat=True).first()
    if source_id is None:
        raise Denied("not_found")
    # Rights transactions may still need a foreign-key KEY SHARE check when
    # committing. Serialize catalog edits without blocking that FK check.
    Source.objects.select_for_update(no_key=True).get(pk=source_id)
    product, _ = locked_catalog_product(product_id)
    return product


def segment_lock(segment_id):
    product_id = (
        BackupSegment.objects.filter(pk=segment_id).values_list("product_id", flat=True).first()
    )
    product = lock_product(product_id)
    segment = BackupSegment.objects.select_for_update().get(pk=segment_id)
    return product, segment


def verify_rights(product, segment, now, *, restore=False):
    if now >= segment.expires_at:
        raise Denied("backup_expired")
    scope = Scope.from_issue(segment.scope)
    expiry = [
        segment.expires_at,
        authorize(
            product,
            scope,
            set(segment.fields),
            now,
            since=segment.snapshot_at,
            restore=restore,
        ),
    ]
    for dependency in (segment.manifest or {}).get("catalog", segment.catalog_dependencies):
        expiry.append(
            authorize(
                product,
                Scope.from_issue(dependency),
                {"catalog"},
                now,
                since=segment.snapshot_at,
                restore=restore,
            )
        )
    return min(value for value in expiry if value is not None)


def audit(operator, segment, action, before, after, product, *, request_id=None):
    return receipt(
        operator,
        {"action": action, "reason_code": "backup_rehearsal", "request_id": request_id},
        "backup_segment",
        segment.pk,
        before,
        after,
        segment.snapshot_version,
        product.rights_version,
    )


def phase_timeout():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '3s'")
        cursor.execute("SET LOCAL statement_timeout = '5s'")


def lock_projection(product, scope):
    if scope.indicator not in INDICATORS[product.shape]:
        raise Denied("invalid_request")
    if product.level == "station":
        list(
            Station.objects.select_for_update()
            .filter(
                source=product.source,
                stationidentifier__kind="code",
                stationidentifier__code=scope.object_code,
            )
            .order_by("pk")
        )


@transaction.atomic(durable=True)
def register_preparation(request, product_id, scope, fields):
    """Commit the cleanup obligation before opening a data file."""
    phase_timeout()
    product = lock_product(product_id)
    lock_projection(product, scope)
    operator = runtime_actor(request.user.pk, MANAGE)
    session = SessionCheck(request)
    root = private_root("S06_BACKUP_DIR")
    known = checked_subjects()
    now = clock()
    expiry = authorize(product, scope, fields, now)
    expiry = min(expiry or now + LIFETIME, now + LIFETIME)
    dependencies = set()

    def check_catalog(scope):
        nonlocal expiry
        if scope not in dependencies:
            until = authorize(product, scope, {"catalog"}, clock())
            if until is not None:
                expiry = min(expiry, until)
            dependencies.add(scope)

    # Freeze the authorized projection across both transactions without retaining its rows.
    key_count = 0
    size = 0
    preflight = sha256()
    for row in rows(product, scope, fields, known, check_catalog):
        encoded = canonical(row) + b"\n"
        size += len(encoded)
        key_count += 1
        if key_count > MAX_KEYS or size > MAX_BYTES:
            raise Denied("backup_projection_limit")
        preflight.update(encoded)
    final_now = session.finish(())
    if not key_count or expiry <= final_now:
        raise Denied("backup_empty_or_expired")
    authorize(product, scope, fields, final_now)
    for scope_dep in dependencies:
        authorize(product, scope_dep, {"catalog"}, final_now)
    segment = BackupSegment.objects.create(
        product=product,
        actor=operator,
        scope=scope.serialize(),
        fields=sorted(fields),
        snapshot_version=product.rights_version,
        snapshot_at=now,
        expires_at=expiry,
        volume_generation=register_volume(root),
        catalog_dependencies=[
            x.serialize()
            for x in sorted(
                dependencies,
                key=lambda x: (x.geography, x.object_code, x.indicator, x.start, x.end),
            )
        ],
    )
    schedule(product, preparation_deadline(segment), "backup_prepare_timeout")
    return segment.pk, key_count, size, preflight.digest()


def preparation_deadline(segment):
    return min(segment.expires_at, segment.snapshot_at + PREPARATION_WINDOW)


@transaction.atomic(durable=True)
def write_preparation(request, segment_id, key_count, size, preflight_digest):
    phase_timeout()
    product, segment = segment_lock(segment_id)
    scope = Scope.from_issue(segment.scope)
    lock_projection(product, scope)
    operator = runtime_actor(request.user.pk, MANAGE)
    session = SessionCheck(request)
    if (
        segment.actor_id != operator.pk
        or segment.state != segment.State.PREPARING
        or segment.manifest is not None
        or session.now >= preparation_deadline(segment)
    ):
        raise Denied("precondition_conflict")
    verify_rights(product, segment, session.now)
    known = checked_subjects()
    fields = set(segment.fields)
    dependencies = {Scope.from_issue(dep) for dep in segment.catalog_dependencies}
    with segment_directory(segment) as (fd, partial, _):
        created = False
        try:
            digest = sha256()
            written = 0

            def confirm_catalog(scope):
                if scope not in dependencies:
                    raise Denied("backup_dependency_unresolved")

            with os.fdopen(
                os.open(
                    partial,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=fd,
                ),
                "wb",
            ) as stream:
                created = True
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                for row in rows(product, scope, fields, known, confirm_catalog):
                    chunk = canonical(row) + b"\n"
                    written += len(chunk)
                    if written > size:
                        raise Denied("backup_projection_changed")
                    stream.write(chunk)
                    digest.update(chunk)
                if written != size or digest.digest() != preflight_digest:
                    raise Denied("backup_projection_changed")
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
            segment.manifest = wire(
                {
                    "format": FORMAT,
                    "migration": "0013_backup_segments",
                    "segment": str(segment.pk),
                    "product": identity(product),
                    "profile": product.contract_version,
                    "level": product.level,
                    "shape": product.shape,
                    "family": product.family,
                    "source_timezone": product.source_timezone,
                    "attribution": product.attribution,
                    "source_publisher": product.source.publisher,
                    "scope": segment.scope,
                    "fields": segment.fields,
                    "snapshot_version": segment.snapshot_version,
                    "snapshot_at": segment.snapshot_at,
                    "expires_at": segment.expires_at,
                    "keys": key_count,
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                    "catalog": segment.catalog_dependencies,
                }
            )
            segment.save(update_fields=["manifest"])
            audit(operator.pk, segment, "backup_prepare", "absent", segment.state, product)
            expiry = verify_rights(product, segment, session.finish(()))
            commit_within(min(expiry, preparation_deadline(segment)))
        except BaseException:
            if created:
                unlink_from(fd, partial)
            raise
    return segment


def prepare(request, product_id, scope_data, *, raw=False):
    scope = parse_scope(scope_data)
    if type(raw) is not bool:
        raise Denied("invalid_request")
    fields = BASE_FIELDS | ({"raw_field"} if raw else set())
    registered = register_preparation(request, product_id, scope, fields)
    return write_preparation(request, *registered)


@contextmanager
def checked_file(fd, name, manifest):
    with os.fdopen(
        os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), "rb"
    ) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size != manifest["bytes"]
            or info.st_size > MAX_BYTES
        ):
            raise Denied("backup_integrity_failed")
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        checksum = sha256()
        while chunk := stream.read(64 * 1024):
            checksum.update(chunk)
        if checksum.hexdigest() != manifest["sha256"]:
            raise Denied("backup_integrity_failed")
        stream.seek(0)
        yield stream


def commit_within(deadline):
    # Bound remaining SQL work. Callers still need a post-commit deadline check
    # because PostgreSQL can finish COMMIT after statement_timeout.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT floor(extract(epoch FROM (%s - clock_timestamp())) * 1000)", [deadline]
        )
        milliseconds = min(5000, int(cursor.fetchone()[0]))
        if milliseconds <= 0:
            raise Denied("authority_invalid")
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", [f"{milliseconds}ms"])


def publish_segment(request, segment_id):
    phase_timeout()
    product, segment = segment_lock(segment_id)
    custodian = configured_custodian()
    operator = runtime_actor(
        request.user.pk, MANAGE, additional=((custodian, "observations.manage_grants"),)
    )
    session = SessionCheck(request)
    now = clock()
    if (
        segment.actor_id != operator.pk
        or segment.state != segment.State.PREPARING
        or not segment.manifest
        or segment.manifest.get("format") != FORMAT
    ):
        raise Denied("precondition_conflict")
    verify_rights(product, segment, now)
    initial = verify_current(product, segment, "publish", segment.pk, now, custodian=custodian)
    with segment_directory(segment) as (fd, partial, target):
        if segment_entry(fd, target) is not None:
            raise Denied("precondition_conflict")
        with checked_file(fd, partial, segment.manifest) as stream:
            now = session.finish(())
            verify_rights(product, segment, now)
            if now >= initial.until:
                raise Denied("authority_invalid")
            if not os.path.samestat(os.fstat(stream.fileno()), file_entry(fd, partial)):
                raise Denied("backup_integrity_failed")
            os.rename(partial, target, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        segment.state = segment.State.AVAILABLE
        segment.save(update_fields=["state"])
        schedule(product, segment.expires_at, "backup_expiry")
        action = audit(
            operator.pk,
            segment,
            "backup_publish",
            "preparing",
            segment.state,
            product,
            request_id=segment.pk,
        )
        action.outcome = "pending"
        action.save(update_fields=["outcome"])
        now = session.finish(())
        verify_rights(product, segment, now)
        proof = verify_current(product, segment, "publish", segment.pk, now, custodian=custodian)
        now = session.finish(())
        rights_until = verify_rights(product, segment, now)
    deadline = min(initial.until, proof.until, rights_until)
    if clock() >= deadline:
        raise Denied("authority_invalid")
    return segment, deadline, proof


def confirm_release(deadline, proof, operation, action):
    # A PostgreSQL COMMIT can complete after its statement deadline. Confirm
    # external material again and make an independent PG-clock release decision.
    proof.confirm()
    count = (
        GovernanceAction.objects.alias(
            pg_now=Func(function="clock_timestamp", output_field=DateTimeField())
        )
        .filter(request_id=operation, action=action, outcome="pending", pg_now__lt=deadline)
        .update(outcome="applied")
    )
    if count != 1:
        raise Denied("authority_invalid")
    # The acknowledgement itself can wait in a deferred trigger or at COMMIT.
    # Observe its completion before authorizing a physical release.
    proof.confirm()
    if clock() >= deadline:
        raise Denied("authority_invalid")


def withhold_receipt(operation, action):
    GovernanceAction.objects.filter(request_id=operation, action=action).update(outcome="withheld")


def released_file(directory_fd, name, segment_id):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise OSError("uncontrolled_file")
        try:
            return os.getxattr(fd, RELEASE_MARK) == segment_id.bytes
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise
            return False
    finally:
        os.close(fd)


def require_published(segment):
    if (
        segment.state != segment.State.AVAILABLE
        or not GovernanceAction.objects.filter(
            request_id=segment.pk, action="backup_publish", outcome="applied"
        ).exists()
    ):
        raise Denied("precondition_conflict")
    with segment_directory(segment) as (fd, _, final):
        if not released_file(fd, final, segment.pk):
            raise Denied("precondition_conflict")


def publication_key(segment_id):
    if segment_id is None:
        raise Denied("not_found")
    return int.from_bytes(sha256(segment_id.bytes).digest()[:8], signed=True)


def require_no_publication(segment_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [publication_key(segment_id)])
        if cursor.fetchone() != (True,):
            raise Denied("precondition_conflict")


def record_file_release(fd, segment_id):
    # A copy without this post-commit witness cannot be used after a crash.
    os.setxattr(fd, RELEASE_MARK, segment_id.bytes, os.XATTR_CREATE)
    os.fsync(fd)


@transaction.atomic(durable=True)
def release_file(request, segment_id, deadline, previous_proof):
    # All authority writes are already committed. Pin current rights and the
    # authenticated principals through the physical hand-off.
    phase_timeout()
    product, segment = segment_lock(segment_id)
    custodian = configured_custodian()
    runtime_actor(request.user.pk, MANAGE, additional=((custodian, "observations.manage_grants"),))
    if segment.actor_id != request.user.pk or segment.state != segment.State.AVAILABLE:
        raise Denied("precondition_conflict")
    session = SessionCheck(request)
    now = session.finish(())
    verify_rights(product, segment, now)
    current = verify_current(product, segment, "publish", segment.pk, now, custodian=custodian)
    with (
        segment_directory(segment) as (fd, _, final),
        checked_file(fd, final, segment.manifest) as stream,
    ):
        previous_proof.confirm()
        current.confirm()
        now = session.finish(())
        rights_until = verify_rights(product, segment, now)
        if clock() >= min(deadline, current.until, rights_until):
            raise Denied("authority_invalid")
        record_file_release(stream.fileno(), segment.pk)
        try:
            current.confirm()
            if clock() >= min(deadline, current.until, rights_until):
                raise Denied("authority_invalid")
        except (Denied, DatabaseError, OSError):
            os.removexattr(stream.fileno(), RELEASE_MARK)
            os.fsync(stream.fileno())
            raise
    return segment


def publish(request, segment_id):
    key = publication_key(segment_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        if cursor.fetchone() != (True,):
            raise Denied("precondition_conflict")
    try:
        with transaction.atomic(durable=True):
            _, deadline, proof = publish_segment(request, segment_id)
        try:
            confirm_release(deadline, proof, segment_id, "backup_publish")
            return release_file(request, segment_id, deadline, proof)
        except (DatabaseError, Denied, OSError):
            with transaction.atomic(durable=True):
                phase_timeout()
                product, segment = segment_lock(segment_id)
                if segment.state == segment.State.AVAILABLE:
                    segment.state = segment.State.BLOCKED
                    segment.save(update_fields=["state"])
                    schedule(product, clock(), "backup_publication_withheld")
                withhold_receipt(segment_id, "backup_publish")
            raise
    finally:
        if connection.connection is not None and not connection.connection.closed:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def block_segments(product, now):
    for segment in (
        BackupSegment.objects.select_for_update()
        .filter(product=product)
        .exclude(
            state__in=(BackupSegment.State.BLOCKED, BackupSegment.State.REMOVED),
        )
        .order_by("pk")
    ):
        try:
            verify_rights(product, segment, now)
        except Denied:
            segment.state = segment.State.BLOCKED
            segment.save(update_fields=["state"])


def reconcile_segments(product, now):
    """Under Product lock, remove every unavailable segment, including crash artifacts."""
    block_segments(product, now)
    segments = (
        BackupSegment.objects.select_for_update()
        .filter(product=product)
        .exclude(
            state=BackupSegment.State.REMOVED,
        )
        .order_by("pk")
    )
    for segment in segments:
        with segment_directory(segment) as (fd, partial, final):
            entry = segment_entry(fd, final)
            if segment.state == segment.State.AVAILABLE:
                if entry is None or not stat.S_ISREG(entry.st_mode):
                    raise OSError("backup_volume_or_segment_unavailable")
                # Missing witnesses must first be durably quarantined by the
                # inventory scan. Unlinking here would lose the only locator
                # if this transaction rolled back after changing the directory.
                if released_file(fd, final, segment.pk):
                    unlink_from(fd, partial)
                continue
            if (
                segment.state == segment.State.PREPARING
                and entry is None
                and now < preparation_deadline(segment)
            ):
                continue
            before = segment.state
            unlink_from(fd, partial)
            unlink_from(fd, final)
        segment.state, segment.manifest = segment.State.REMOVED, None
        segment.catalog_dependencies = []
        segment.save(update_fields=["state", "manifest", "catalog_dependencies"])
        audit(None, segment, "backup_disposition", before, segment.state, product)


def sweep_orphans():
    """Serializes with preparation so an uncommitted segment is never mistaken for an orphan."""
    root = private_root("S06_BACKUP_DIR")
    unwitnessed = defaultdict(list)
    with transaction.atomic(durable=True):
        phase_timeout()
        with connection.cursor() as cursor:
            cursor.execute("LOCK TABLE observations_backupsegment IN SHARE MODE")
        failed = False
        with directory(root) as fd:
            for name in os.listdir(fd):
                stem, _, suffix = name.rpartition(".")
                if suffix not in {"partial", "segment"}:
                    continue
                try:
                    identifier = parse_uuid(stem)
                except Denied:
                    identifier = None
                metadata = (
                    BackupSegment.objects.filter(pk=identifier)
                    .values("state", "product_id")
                    .first()
                    if identifier is not None
                    else None
                )
                if metadata is None:
                    try:
                        unlink_from(fd, name)
                    except OSError:
                        failed = True
                elif suffix == "segment" and metadata["state"] == BackupSegment.State.AVAILABLE:
                    if not released_file(fd, name, identifier):
                        unwitnessed[metadata["product_id"]].append(identifier)
            confirm_directory(root, fd)
    # Do not hold the orphan inventory lock while acquiring the normal range
    # locks. Recheck each candidate after any concurrent publisher finishes.
    for product_id, identifiers in sorted(unwitnessed.items()):
        marked = False
        with transaction.atomic(durable=True):
            phase_timeout()
            product = lock_product(product_id)
            candidates = (
                BackupSegment.objects.select_for_update()
                .filter(pk__in=identifiers, state=BackupSegment.State.AVAILABLE)
                .order_by("pk")
            )
            for segment in candidates:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_try_advisory_xact_lock(%s)", [publication_key(segment.pk)]
                    )
                    if cursor.fetchone() != (True,):
                        continue
                with segment_directory(segment) as (fd, _, final):
                    if released_file(fd, final, segment.pk):
                        continue
                segment.state = BackupSegment.State.BLOCKED
                segment.save(update_fields=["state"])
                withhold_receipt(segment.pk, "backup_publish")
                marked = True
            if marked:
                schedule(product, clock(), "backup_publication_withheld")
        if marked:
            # Durable blocked state and a due job remain if unlink or its
            # confirmation commit fails. Retries verify the original volume.
            from .disposition import reconcile_product

            reconcile_product(product_id)
    if failed:
        raise OSError("orphan_cleanup_failed")
