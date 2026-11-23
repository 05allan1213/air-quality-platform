"""Independent Ed25519 attestations for the live authority database's rights sequence."""

import base64
import binascii
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .backup_models import BackupAuthorityEvent
from .models import Change, DispositionJob, UseGrant
from .policy import Denied

ZERO = "0" * 64
AUTHORITY_TTL = timedelta(minutes=2)
MAX_PROOF = 2 * 1024 * 1024
MAX_EVENTS = 256
GRANT_COLUMNS = (
    "pk",
    "operation",
    "audience",
    "geography",
    "object_code",
    "indicator",
    "data_from",
    "data_until",
    "fields",
    "terms_version",
    "registered_at",
    "valid_from",
    "valid_until",
    "retain_until",
    "expiry_mode",
    "effect",
    "restrictions",
    "confirmed_by_id",
    "revoked_at",
    "revoked_by_id",
)


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        cls=ExactEncoder,
    ).encode("ascii")


class ExactEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.astimezone(UTC).isoformat(timespec="microseconds")
        return super().default(obj)


def wire(value):
    return json.loads(canonical(value))


def digest(value):
    return sha256(canonical(value)).hexdigest()


def identity(product):
    return [product.source.namespace, product.code]


def private_root(setting):
    try:
        root = Path(getattr(settings, setting))
        st = root.lstat()
        if not (
            root.resolve(strict=True) == root
            and stat.S_ISDIR(st.st_mode)
            and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o700
        ):
            raise OSError("unsafe root")
    except (AttributeError, OSError, TypeError, ValueError):
        raise Denied("backup_storage_unavailable") from None
    return root


def authority_root():
    root = private_root("S06_AUTHORITY_DIR")
    backup = private_root("S06_BACKUP_DIR")
    if (
        root.is_relative_to(backup)
        or backup.is_relative_to(root)
        or not os.statvfs(root).f_flag & os.ST_RDONLY
    ):
        raise Denied("authority_invalid")
    return root


def read_file(path, maximum=MAX_PROOF):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > maximum
        ):
            raise OSError("unsafe proof file")
        result = stream.read(maximum + 1)
    if len(result) > maximum:
        raise OSError("oversized proof file")
    return result


def verify_signature(public_key, payload, signature):
    if len(signature) != 64:
        raise Denied("authority_invalid")
    # OpenSSL's Ed25519 raw input requires a seekable input file.
    with (
        tempfile.TemporaryFile() as key,
        tempfile.TemporaryFile() as msg,
        tempfile.TemporaryFile() as sig,
    ):
        for file, content in ((key, public_key), (msg, canonical(payload)), (sig, signature)):
            file.write(content)
            file.seek(0)
        result = subprocess.run(
            [
                "/usr/bin/openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-rawin",
                "-inkey",
                f"/proc/self/fd/{key.fileno()}",
                "-in",
                f"/proc/self/fd/{msg.fileno()}",
                "-sigfile",
                f"/proc/self/fd/{sig.fileno()}",
            ],
            pass_fds=(key.fileno(), msg.fileno(), sig.fileno()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    if result.returncode:
        raise Denied("authority_invalid")


def verify_envelope(key, envelope):
    try:
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise ValueError
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise ValueError
        signature = base64.b64decode(envelope["signature"], validate=True)
        verify_signature(key, payload, signature)
        return payload
    except (TypeError, ValueError, binascii.Error, OSError, subprocess.TimeoutExpired):
        raise Denied("authority_invalid") from None


def authority_identity():
    # This identifier comes from the custodian's independently configured root.
    value = read_file(authority_root() / "authority_id", 80).decode("ascii").strip()
    if not value or len(value) > 64:
        raise Denied("authority_invalid")
    return value


def rights_state(product):
    grants = UseGrant.objects.filter(product=product).order_by("pk")
    boundaries = (
        DispositionJob.objects.filter(product=product).exclude(boundary_grant=None).order_by("pk")
    )

    def grant_projection(grant):
        # Freeform terms and revoke reason never enter external evidence.
        return {column: getattr(grant, column) for column in GRANT_COLUMNS}

    return {
        "product": identity(product),
        "rights_version": product.rights_version,
        "grants": [grant_projection(grant) for grant in grants.iterator(chunk_size=128)],
        "losses": list(
            boundaries.values(
                "boundary_grant_id",
                "due_at",
                "completed_at",
                "boundary_version",
                "boundary_basis",
            )
        ),
    }


def record_event(product, kind, effective_at):
    """Call after finishing the rights mutation, with the Product lock held."""
    latest = BackupAuthorityEvent.objects.filter(product=product).order_by("-version").first()
    previous = latest.digest if latest else ZERO
    state = wire(rights_state(product))
    body = {
        "product": identity(product),
        "version": product.rights_version,
        "previous": previous,
        "kind": kind,
        "effective_at": effective_at,
        "rights": digest(state),
    }
    BackupAuthorityEvent.objects.create(
        product=product,
        version=product.rights_version,
        previous=previous,
        digest=digest(body),
        kind=kind,
        effective_at=effective_at,
        rights=state,
    )


def proof_observation(product):
    """Read-only export for a custodian. No observations, credentials, or free-form terms."""
    events = BackupAuthorityEvent.objects.filter(product=product).order_by("version")
    rights = rights_state(product)
    if any(grant["registered_at"] is None for grant in rights["grants"]):
        raise Denied("authority_invalid")
    return wire(
        {
            "authority": authority_identity(),
            "rights": rights,
            "events": list(
                events.values("version", "previous", "digest", "kind", "effective_at", "rights")
            ),
        }
    )


def configured_custodian():
    try:
        identifier = json.loads(read_file(authority_root() / "trust.json", 65536))["custodian"]
        if type(identifier) is not int or not 0 < identifier < 2**63:
            raise ValueError
        return identifier
    except (OSError, KeyError, TypeError, ValueError):
        raise Denied("authority_invalid") from None


@dataclass(frozen=True)
class VerifiedProof:
    until: datetime
    files: tuple

    def confirm(self):
        try:
            root = authority_root()
            if any(
                sha256(read_file(root / name, limit)).digest() != value
                for name, limit, value in self.files
            ):
                raise ValueError
        except (OSError, ValueError):
            raise Denied("authority_invalid") from None


def verify_current(product, segment, action, request_id, now, *, custodian, target=None):
    """Verify an independent request-bound seal against the locked, live authority."""
    try:
        root = authority_root()
        fingerprints = []

        def read(name, limit=MAX_PROOF):
            data = read_file(root / name, limit)
            fingerprints.append((name, limit, sha256(data).digest()))
            return data

        key = read("public.pem", 4096)
        policy = json.loads(read("trust.json", 65536))
        current = proof_observation(product)
        if read("authority_id", 80).decode("ascii").strip() != current["authority"]:
            raise ValueError
        document = json.loads(read(f"{request_id}.{action}.json"))
        if (
            type(policy["custodian"]) is not int
            or policy["custodian"] != custodian
            or policy["key_id"] != sha256(key).hexdigest()
            or set(document) != {"chain", "seal"}
            or not isinstance(document["chain"], list)
        ):
            raise ValueError
        namespace, code = identity(product)
        anchor = policy["anchors"][namespace][code]
        start = anchor["version"]
        if (
            type(start) is not int
            or not start <= segment.snapshot_version <= product.rights_version
        ):
            raise ValueError
        previous = anchor["digest"]
        active = [event for event in current["events"] if event["version"] > start]
        if (
            len(active) > MAX_EVENTS
            or len(active) != len(document["chain"])
            or len(active) != product.rights_version - start
        ):
            raise ValueError
        changes = Change.objects.filter(
            product=product,
            city_key=None,
            station_key=None,
            rights_version__gt=start,
            kind__in=("grant_registered", "grant_revoked", "disposition"),
        )
        if list(changes.order_by("rights_version").values_list("rights_version", "kind")) != [
            (event["version"], event["kind"]) for event in active
        ]:
            raise ValueError
        issuer = {key: policy[key] for key in ("custodian", "key_id")}
        for version, (event, envelope) in enumerate(
            zip(active, document["chain"], strict=True), start + 1
        ):
            expected = {
                **event,
                **issuer,
                "product": [namespace, code],
                "authority": current["authority"],
            }
            digest_body = {k: event[k] for k in ("version", "previous", "kind", "effective_at")}
            digest_body |= {"product": [namespace, code], "rights": digest(event["rights"])}
            if (
                event["version"] != version
                or event["previous"] != previous
                or event["digest"] != digest(digest_body)
                or verify_envelope(key, envelope) != expected
            ):
                raise ValueError
            previous = event["digest"]
        head_path = f"head-{digest(identity(product))}.json"
        expected_head = {
            **issuer,
            "authority": current["authority"],
            "product": [namespace, code],
            "version": product.rights_version,
            "chain_tip": previous,
            "rights": digest(current["rights"]),
        }
        if verify_envelope(key, json.loads(read(head_path))) != expected_head:
            raise ValueError
        seal = verify_envelope(key, document["seal"])
        at, until = (datetime.fromisoformat(seal[key]) for key in ("signed_at", "valid_until"))
        expected = {
            **expected_head,
            "fingerprint": digest(segment.manifest),
            "request": str(request_id),
            "target": str(target) if target else None,
            "action": action,
            "scope": segment.scope,
            "fields": segment.fields,
            "signed_at": seal["signed_at"],
            "valid_until": seal["valid_until"],
        }
        if seal != expected or not (
            now - AUTHORITY_TTL <= at <= now < until <= min(at + AUTHORITY_TTL, segment.expires_at)
        ):
            raise ValueError
    except (OSError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        raise Denied("authority_invalid") from None
    return VerifiedProof(until, tuple(fingerprints))
