#!/usr/bin/env python3
"""Offline signing of explicitly reviewed S06 authority material."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sign(key, body):
    with tempfile.TemporaryFile() as message:
        message.write(canonical(body))
        message.seek(0)
        signature = subprocess.run(
            [
                "/usr/bin/openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(key),
                "-in",
                f"/proc/self/fd/{message.fileno()}",
            ],
            pass_fds=(message.fileno(),),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=3,
            check=True,
        ).stdout
    return {"payload": body, "signature": base64.b64encode(signature).decode("ascii")}


def write(root, name, body):
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as file:
        path = Path(file.name)
        try:
            file.write(canonical(body))
            file.flush()
            os.fsync(file.fileno())
            path.replace(root / name)
        finally:
            path.unlink(missing_ok=True)


def sign_reviewed(key, root, material):
    policy = json.loads((root / "trust.json").read_bytes())
    action, request_id = material["action"], str(UUID(material["operation"]))
    current, manifest = material["current"], material["manifest"]
    authority = current["authority"]
    if authority != (root / "authority_id").read_text().strip() or action not in (
        "publish",
        "restore",
    ):
        raise ValueError("invalid_authority_material")
    product = manifest["product"]
    namespace, code = product
    if current["rights"]["product"] != product:
        raise ValueError("invalid_authority_material")
    version = current["rights"]["rights_version"]
    anchor = policy["anchors"][namespace][code]
    start, previous = anchor["version"], anchor["digest"]
    if not start <= manifest["snapshot_version"] <= version <= start + 256:
        raise ValueError("invalid_authority_material")
    issuer = {k: policy[k] for k in ("custodian", "key_id")}
    match = issuer | {"authority": authority, "product": product}
    events = [event for event in current["events"] if event["version"] > start]
    if len(events) != version - start:
        raise ValueError("invalid_authority_material")
    for number, event in enumerate(events, start + 1):
        if event["version"] != number or event["previous"] != previous:
            raise ValueError("invalid_authority_material")
        previous = event["digest"]
    head = match | {"version": version, "chain_tip": previous, "rights": digest(current["rights"])}
    at = datetime.fromisoformat(material["observed_at"])
    until = min(
        at + timedelta(minutes=2),
        datetime.fromisoformat(material["expiry"]),
        datetime.fromisoformat(manifest["expires_at"]),
    )
    if at >= until:
        raise ValueError("invalid_authority_material")
    seal = head | {
        "request": request_id,
        "action": action,
        "target": material["target"],
        "scope": manifest["scope"],
        "fields": manifest["fields"],
        "fingerprint": digest(manifest),
        "signed_at": material["observed_at"],
        "valid_until": until.isoformat(timespec="microseconds"),
    }
    filename = f"head-{digest(product)}.json"
    path = root / filename
    with (root / ".custodian.lock").open("a") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        if path.exists():
            old = json.loads(path.read_bytes())["payload"]
            event_digests = {event["version"]: event["digest"] for event in events}
            event_digests[start] = anchor["digest"]
            if (
                old["version"] > version
                or old["chain_tip"] != event_digests.get(old["version"])
                or (old["version"] == version and old["rights"] != head["rights"])
            ):
                raise ValueError("authority_rollback_or_fork")
        document = {
            "chain": [sign(key, event | match) for event in events],
            "seal": sign(key, seal),
        }
        write(root, filename, sign(key, head))
        write(root, f"{request_id}.{action}.json", document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-material", type=Path, required=True)
    parser.add_argument("--custodian-root", type=Path, required=True)
    parser.add_argument("--offline-key", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        sign_reviewed(
            args.offline_key, args.custodian_root, json.loads(args.reviewed_material.read_bytes())
        )
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "authority_signing_denied\n")


if __name__ == "__main__":
    main()
