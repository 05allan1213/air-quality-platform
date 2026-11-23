"""Review and sign the UCI 501 rehearsal material outside the app's sandbox."""

import json
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from socketserver import StreamRequestHandler, UnixStreamServer
from threading import Thread
from uuid import UUID

from s06_restore_provision import target_id
from s06_restore_sign import sign_reviewed, write

SOURCE = ("uci.501", "uci501")
FIELDS = ("backup_manifest", "catalog", "history_subject", "normalized", "provenance")
STATIONS = ("Aotizhongxin", "Changping")
PERIOD = ("2013-03-01", "2017-03-01")
WINDOWS = (("2013-03-01", "2013-03-03"), ("2017-02-27", "2017-03-01"))
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
LICENSED_DOI = "https://doi.org/10.24432/C5RK5G"
ISSUER = "s07-h-custodian"
LIMIT = 16 * 1024 * 1024


def require(condition, code):
    if not condition:
        raise ValueError(code)


def license_evidence(uci_page, cc_page):
    uci = uci_page.read_bytes()
    license_text = cc_page.read_bytes()
    require(
        all(
            token in uci
            for token in (
                b"Beijing Multi-Site Air Quality [Dataset]",
                b"Chen, S. (2017)",
                LICENSED_DOI.encode(),
                b"This dataset is licensed under a",
                (LICENSE + "legalcode").encode(),
                b"(CC BY 4.0) license",
                b"This allows for the sharing and adaptation of the datasets for any purpose",
            )
        ),
        "uci_501_license",
    )
    require(
        LICENSE.encode() in license_text
        and b"Attribution 4.0 International" in license_text
        and b"retain the following if it is supplied by the Licensor" in license_text
        and b"indicate if You modified the Licensed Material" in license_text,
        "cc_by_4_0_terms",
    )
    return {
        "dataset": "Beijing Multi-Site Air Quality",
        "doi": LICENSED_DOI,
        "license": LICENSE,
        "provider_metadata_sha256": sha256(uci).hexdigest(),
        "license_page_sha256": sha256(license_text).hexdigest(),
        "authority": "operator-authorized one-time local rehearsal",
        "trust_domain": "disposable S07 H verification only",
    }


def request(path, action, material=None):
    message = json.dumps([action, material], separators=(",", ":")).encode("ascii")
    require(len(message) < LIMIT, "custody_message_limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(str(path))
        with connection.makefile("rwb") as stream:
            stream.write(message + b"\n")
            stream.flush()
            reply = json.loads(stream.readline(512))
    require(reply == {"accepted": True}, "independent_custody_rejected")


def independent_connection(env):
    import psycopg

    return psycopg.connect(
        host=env["POSTGRES_HOST"],
        port=5432,
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        connect_timeout=3,
        options="-c default_transaction_read_only=on -c statement_timeout=3000",
    )


@dataclass
class Review:
    env: dict
    root: Path
    key: Path
    public: bytes
    authority_id: str
    accepted: Counter

    def identify(self, cursor):
        cursor.execute(
            "SELECT current_setting('airquality.s06_run'), inet_server_addr() IS NULL, "
            "current_setting('transaction_read_only')"
        )
        require(cursor.fetchone() == (self.env["S06_RUN"], True, "on"), "custody_readonly_pg")

    def configure(self, cursor, material):
        require(material is None and not self.accepted, "custody_configuration_once")
        cursor.execute(
            """SELECT u.id FROM auth_user u
               JOIN auth_user_user_permissions up ON up.user_id=u.id
               JOIN auth_permission p ON p.id=up.permission_id
               JOIN django_content_type c ON c.id=p.content_type_id
               WHERE u.username=%s AND u.is_active AND NOT u.is_superuser
                 AND c.app_label='observations' AND p.codename='manage_grants'""",
            (ISSUER,),
        )
        (custodian,) = cursor.fetchone()
        cursor.execute(
            """SELECT u.id,u.username,s.identity FROM observations_backupsubject s
               JOIN auth_user u ON u.id=s.user_id
               WHERE u.username='s07-h-operator' AND u.is_active AND NOT u.is_superuser"""
        )
        ((actor, username, identity),) = cursor.fetchall()
        require(custodian != actor, "independent_custodian_session")
        cursor.execute(
            """SELECT p.id,p.rights_version,e.digest,clock_timestamp()
               FROM observations_product p JOIN observations_source s ON s.id=p.source_id
               JOIN observations_backupauthorityevent e ON e.product_id=p.id AND e.version=p.rights_version
               WHERE s.namespace=%s AND p.code=%s AND p.family='H' AND NOT p.synthetic""",
            SOURCE,
        )
        product, anchor_version, anchor_digest, now = cursor.fetchone()
        cursor.execute(
            """SELECT version,previous,digest FROM observations_backupauthorityevent
               WHERE product_id=%s ORDER BY version""",
            (product,),
        )
        previous = "0" * 64
        event_count = 0
        for event_count, (version, linked, digest) in enumerate(cursor.fetchall(), 1):
            require(version == event_count and linked == previous, "custody_initial_rights_chain")
            previous = digest
        require(
            event_count == anchor_version and previous == anchor_digest,
            "custody_initial_rights_chain",
        )
        cursor.execute(
            """SELECT count(*) FROM observations_usegrant WHERE product_id=%s AND
               (registered_at IS NULL OR revoked_at IS NOT NULL OR operation IN ('export','redistribute'))""",
            (product,),
        )
        require(cursor.fetchone() == (0,), "custody_no_unregistered_or_external_use")
        for start, end in WINDOWS:
            for code in STATIONS:
                cursor.execute(
                    """SELECT EXISTS(SELECT 1 FROM observations_usegrant
                       WHERE product_id=%s AND operation='backup' AND indicator='CO'
                         AND object_code=%s AND data_from=%s AND data_until=%s)""",
                    (product, code, date.fromisoformat(start), date.fromisoformat(end)),
                )
                if cursor.fetchone()[0]:
                    self.permitted(
                        cursor,
                        product,
                        "restore",
                        {
                            "geography": "beijing",
                            "object_code": code,
                            "indicator": "CO",
                            "start": start,
                            "end": end,
                        },
                        FIELDS,
                        now,
                        now,
                    )
        for code in ("Beijing", *STATIONS):
            self.permitted(
                cursor,
                product,
                "restore",
                {
                    "geography": "beijing",
                    "object_code": code,
                    "indicator": "*",
                    "start": PERIOD[0],
                    "end": PERIOD[1],
                },
                ("catalog",),
                now,
                now,
            )
        (self.root / "public.pem").write_bytes(self.public)
        (self.root / "public.pem").chmod(0o600)
        (self.root / "authority_id").write_text(self.authority_id, encoding="ascii")
        (self.root / "authority_id").chmod(0o600)
        write(
            self.root,
            "trust.json",
            {
                "custodian": custodian,
                "key_id": sha256(self.public).hexdigest(),
                "anchors": {
                    SOURCE[0]: {SOURCE[1]: {"version": anchor_version, "digest": anchor_digest}}
                },
            },
        )
        write(
            self.root,
            "subjects.json",
            {
                str(actor): {
                    "token": "s07-h-uci-import-operator",
                    "username": username,
                    "identity": str(identity),
                }
            },
        )

    def segment(self, cursor, material):
        action = material["action"]
        operation = UUID(material["operation"])
        target = UUID(material["target"]) if action == "restore" else None
        require(
            (action == "publish" and material["target"] is None)
            or (
                action == "restore"
                and target in {target_id(self.env["S06_RUN"], x) for x in ("", "2")}
            ),
            "custody_target_scope",
        )
        cursor.execute(
            """SELECT p.id,p.rights_version,p.attribution,b.manifest,b.snapshot_version,b.expires_at,
                      clock_timestamp(),i.expires_at
               FROM observations_backupsegment b
               JOIN observations_product p ON p.id=b.product_id
               JOIN observations_source s ON s.id=p.source_id
               LEFT JOIN observations_restoreintent i ON i.segment_id=b.id AND i.id=%s
               WHERE s.namespace=%s AND p.code=%s AND p.family='H' AND p.shape='H'
                 AND p.level='station' AND NOT p.synthetic AND p.source_timezone='Asia/Shanghai'
                 AND ((%s::uuid IS NULL AND b.id=%s AND b.state='preparing')
                      OR (%s::uuid IS NOT NULL AND b.state='available' AND i.target=%s
                          AND i.consumed_at IS NULL AND i.expires_at > clock_timestamp()))""",
            (operation, *SOURCE, target, operation, target, target),
        )
        row = cursor.fetchone()
        require(row is not None and cursor.fetchone() is None, "custody_current_segment")
        product, version, attribution, manifest, snapshot, expires, now, intent_expires = row
        current = material["current"]
        observed_at, expiry = (
            datetime.fromisoformat(material[key]) for key in ("observed_at", "expiry")
        )
        require(
            current["authority"] == self.authority_id
            and current["rights"]["product"] == list(SOURCE)
            and current["rights"]["rights_version"] == version
            and manifest == material["manifest"]
            and manifest["snapshot_version"] == snapshot
            and manifest["product"] == list(SOURCE)
            and manifest["fields"] == list(FIELDS)
            and manifest["format"] == "s06.logical.v2"
            and manifest["attribution"] == attribution
            and all(
                token in attribution
                for token in ("Chen, S. (2017)", LICENSED_DOI, LICENSE, "converted")
            )
            and manifest["keys"] == 48
            and 0 < manifest["bytes"] <= 1024 * 1024
            and now - timedelta(minutes=2) <= observed_at <= now < expiry <= expires
            and (intent_expires is None or expiry <= intent_expires),
            "custody_current_fields_and_authority",
        )
        scope = manifest["scope"]
        require(
            scope["geography"] == "beijing"
            and scope["object_code"] in STATIONS
            and scope["indicator"] == "CO"
            and (scope["start"], scope["end"]) in WINDOWS,
            "custody_scope_co_windows",
        )
        expected_catalog = [
            {
                "geography": "beijing",
                "object_code": code,
                "indicator": "*",
                "start": PERIOD[0],
                "end": PERIOD[1],
            }
            for code in ("Beijing", scope["object_code"])
        ]
        require(
            len(manifest["catalog"]) == 2
            and all(x in manifest["catalog"] for x in expected_catalog),
            "custody_catalog_scope",
        )
        cursor.execute(
            """SELECT version,previous,digest,kind FROM observations_backupauthorityevent
               WHERE product_id=%s ORDER BY version""",
            (product,),
        )
        events = [(e["version"], e["previous"], e["digest"], e["kind"]) for e in current["events"]]
        require(cursor.fetchall() == events and len(events) == version, "custody_rights_chain")
        for item, fields in [(scope, FIELDS), *((x, ("catalog",)) for x in expected_catalog)]:
            self.permitted(cursor, product, action, item, fields, now, expiry)

    def permitted(self, cursor, product, action, scope, fields, now, expiry):
        start, end = (date.fromisoformat(scope[k]) for k in ("start", "end"))
        for operation in (
            "backup",
            "retain_normalized",
            *(("restore",) if action == "restore" else ()),
        ):
            cursor.execute(
                """SELECT operation, audience, indicator, object_code, data_from, data_until,
                          fields, effect, restrictions, terms, attribution, valid_until
                   FROM observations_usegrant WHERE product_id=%s AND operation=%s
                     AND geography IN ('*',%s) AND object_code IN ('*',%s)
                     AND (indicator IN ('*',%s) OR %s='*') AND data_from < %s AND data_until > %s
                     AND registered_at <= %s AND valid_from <= %s AND revoked_at IS NULL
                     AND (valid_until IS NULL OR valid_until > %s)
                     AND (retain_until IS NULL OR retain_until > %s)""",
                (
                    product,
                    operation,
                    scope["geography"],
                    scope["object_code"],
                    scope["indicator"],
                    scope["indicator"],
                    end,
                    start,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            rows = cursor.fetchall()
            require(all(row[7] == "allow" and not row[8] for row in rows), "custody_deny")
            require(
                any(
                    audience == "platform"
                    and indicator in ("*", scope["indicator"])
                    and code in ("*", scope["object_code"])
                    and left <= start < end <= right
                    and set(fields) <= set(granted)
                    and valid_until is not None
                    and valid_until >= expiry
                    and LICENSE in terms
                    and LICENSED_DOI in terms
                    and LICENSED_DOI in attribution
                    for _, audience, indicator, code, left, right, granted, _, _, terms, attribution, valid_until in rows
                ),
                "custody_use_basis",
            )

    def accept(self, action, material):
        with independent_connection(self.env) as db, db.cursor() as cursor:
            self.identify(cursor)
            if action == "configure":
                self.configure(cursor, material)
            elif action == "sign":
                require(self.accepted["configure"] == 1, "custody_ready")
                self.segment(cursor, material)
                sign_reviewed(self.key, self.root, material)
                action = material["action"]
            else:
                raise ValueError("custody_action")
        self.accepted[action] += 1


@contextmanager
def custodian(path, root, env, receipt):
    key = root / "offline.pem"
    published = root / "public"
    published.mkdir(mode=0o700)
    subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)],
        check=True,
        capture_output=True,
        timeout=5,
    )
    key.chmod(0o600)
    public = subprocess.check_output(
        ["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout"],
        timeout=5,
    )
    review = Review(env, published, key, public, f"S07-H-rehearsal-{env['S06_RUN']}", Counter())
    errors = []

    class Handler(StreamRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            try:
                data = self.rfile.readline(LIMIT)
                require(data.endswith(b"\n") and len(data) < LIMIT, "custody_message_limit")
                review.accept(*json.loads(data))
            except Exception as exc:
                code = str(exc)
                errors.append(
                    code
                    if code.startswith("custody_") and code.isidentifier()
                    else type(exc).__name__
                )
                self.wfile.write(b'{"accepted":false}\n')
            else:
                self.wfile.write(b'{"accepted":true}\n')

    with UnixStreamServer(str(path), Handler) as server:
        path.chmod(0o600)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield published, key
        finally:
            server.shutdown()
            thread.join(timeout=10)
            path.unlink()
            receipt["custody_reviews"] = dict(review.accepted)
            receipt["custody_errors"] = errors
