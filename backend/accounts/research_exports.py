"""Produce bounded CSV bytes only after the ticket and both version checks commit."""

import csv
import hashlib
import unicodedata
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO

from django.http import HttpResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from observations.analysis_query import build_relation, detail_rows, token
from observations.policy import Denied
from observations.research_api import PARENT
from observations.research_spec import METHOD_VERSION, object_with_keys, parse_spec
from observations.s04_http import params
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ExportTicket
from .research_session import (
    SessionCheck,
    authorize_inputs,
    lock_dependencies,
    owned,
    paged,
    reject,
    request_transaction,
    scope,
)

FORMAT = "s05.csv.v2"
MAX_ROWS = 10_000
MAX_BYTES = 4 * 1024 * 1024
TICKET_TTL = timedelta(minutes=15)
TICKET_LIMIT = 12
TICKET_RATE = 5
COMMON_FIELDS = (
    "group",
    "data_scope",
    "source",
    "product",
    "attribution",
    "generated_at",
    "source_timezone",
    "indicator",
    "unit",
    "window_definition",
    "standard_series",
    "input_version",
    "method",
    "method_version",
    "processing",
)
DETAIL_REQUIRED = COMMON_FIELDS + (
    "entity",
    "observed_at",
    "value",
    "quality",
    "censor_direction",
    "censor_limit",
    "key",
    "revision",
    "fetched_at",
)
AGGREGATE_REQUIRED = COMMON_FIELDS + (
    "entity",
    "start",
    "end",
    "E",
    "O",
    "V",
    "conclusion",
    "mean",
    "E_common",
    "P",
)
OPTIONAL = {
    "detail": ("qualification_version", "report_kind", "fetched_at_basis"),
    "aggregate": (
        "median",
        "minimum",
        "maximum",
        "minimum_at",
        "maximum_at",
        "rank",
        "pearson",
        "mean_difference",
        "received_ratio",
        "valid_ratio",
    ),
}


def columns(kind, selected):
    required = DETAIL_REQUIRED if kind == "detail" else AGGREGATE_REQUIRED
    if (
        not isinstance(selected, (list, tuple))
        or any(
            not isinstance(field, str) or field not in (*required, *OPTIONAL[kind])
            for field in selected
        )
        or len(set(selected)) != len(selected)
    ):
        raise Denied("invalid_request")
    return (*required, *(field for field in OPTIONAL[kind] if field in selected))


def export_fields(kind, selected=()):
    return {"export_manifest", *("csv_" + field for field in columns(kind, selected))}


def normalize(data):
    object_with_keys(data, {"kind", "groups", "fields"}, {"kind", "groups"})
    kind, groups = data["kind"], data["groups"]
    if kind not in ("detail", "aggregate") or not isinstance(groups, list) or not groups:
        raise Denied("invalid_request")
    if len(groups) > 2:
        raise Denied("query_too_large")
    fields = columns(kind, data.get("fields", []))
    specs, versions = [], []
    for group in groups:
        object_with_keys(group, PARENT | {"input_version"}, {"input_version"})
        version = group["input_version"]
        if (
            not isinstance(version, str)
            or len(version) != 64
            or any(c not in "0123456789abcdef" for c in version)
        ):
            raise Denied("invalid_request")
        spec = parse_spec({key: group[key] for key in PARENT & group.keys()}, export=True)
        if kind == "aggregate" and spec.method == "detail":
            raise Denied("invalid_request")
        specs.append(spec)
        versions.append(version)
    if len({spec.synthetic for spec in specs}) != 1:
        raise Denied("invalid_request")
    if len(specs) == 2 and specs[0] == specs[1]:
        raise Denied("invalid_request")
    if (
        sum(spec.cells for spec in specs) > 12_000
        or sum(len(spec.ids) for spec in specs) > 5
        or len({ind for spec in specs for ind in spec.indicators}) > 7
    ):
        raise Denied("query_too_large")
    return kind, specs, versions, fields


def text_cell(value):
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value)
    if text.startswith(("\t", "\r", "\n")):
        return "'" + text
    normalized = unicodedata.normalize("NFKC", text)
    for char in normalized:
        if char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}:
            continue
        if char in "=+-@":
            return "'" + text
        break
    return text


def encode_csv(fields, rows, budget):
    data = BytesIO()
    line = StringIO(newline="")
    writer = csv.writer(line)

    def append(row):
        writer.writerow(row)
        encoded = line.getvalue().encode("utf-8")
        if data.tell() + len(encoded) > MAX_BYTES:
            raise Denied("query_too_large")
        data.write(encoded)
        line.seek(0)
        line.truncate()

    append(fields)
    for index, row in enumerate(rows):
        if index % 128 == 0:
            budget.remaining_ms()
        append(text_cell(row.get(field)) for field in fields)
    budget.remaining_ms()
    return data.getvalue()


def aggregate_rows(rel):
    from observations.research_statistics import summarize

    report = summarize(rel, series=False)
    if report["state"] == "no_data":
        return []
    indexed = report["columns"]
    rows = []
    spec = rel.spec
    paired = spec.method in {"compare", "pearson"}
    for bucket in report.get("buckets", []):
        column = indexed[bucket["pos"]]
        rows.append(
            {
                **bucket,
                **{
                    field: column[field]
                    for field in ("entity", "indicator", "unit", "standard_series")
                },
                "conclusion": bucket["conclusion"]["state"],
                "processing": "source-local bucket, effective sample distribution",
            }
        )
    if paired:
        ranks = {row["pos"]: row for row in (report.get("ranking") or [])}
        for column in indexed:
            rank = ranks.get(column["pos"], {})
            rows.append(
                {
                    **column,
                    "start": spec.start,
                    "end": spec.end,
                    "conclusion": report["conclusion"]["state"],
                    "E_common": report["E_common"],
                    "P": report["P"],
                    "pearson": report.get("pearson"),
                    "mean_difference": report.get("mean_difference"),
                    "mean": rank.get("mean"),
                    "rank": rank.get("rank"),
                    "processing": "common expected slots, paired effective samples",
                }
            )
    return rows


def data_rows(kind, relations, generated_at):
    rows = []
    for index, rel in enumerate(relations, start=1):
        if kind == "detail":
            result = detail_rows(rel, limit=MAX_ROWS + 1 - len(rows))
            for row in result:
                row["sort_at"] = row["observed_at"] or rel.spec.start
                if rel.spec.report == "daily":
                    row["observed_at"] = row["local_report_date"]
                for field in ("value", "censor_limit"):
                    if row[field] is not None:
                        row[field] = Decimal(row[field])
        else:
            result = aggregate_rows(rel)
        if len(rows) + len(result) > MAX_ROWS:
            raise Denied("query_too_large")
        common = {
            "group": index,
            "data_scope": "synthetic" if rel.product.synthetic else "formal",
            "source": rel.product.source.namespace,
            "product": rel.product.pk,
            "attribution": "\n".join(rel.attributions),
            "generated_at": generated_at,
            "source_timezone": rel.product.source_timezone,
            "window_definition": rel.spec.window,
            "input_version": rel.input_version,
            "method": rel.spec.method,
            "method_version": rel.spec.method_version,
            "processing": "normalized observation; quality and censor fields retain source semantics",
        }
        rows.extend(common | row | {"attribution": common["attribution"]} for row in result)
    time_key = "sort_at" if kind == "detail" else "start"
    rows.sort(key=lambda row: (row[time_key], row["group"], row["pos"], row.get("key", 0)))
    return rows


def export_accesses(user_id, kind, specs, fields, *, now=None):
    purpose = "export" if kind == "detail" else "export_aggregate"
    # Frozen fields are server-generated, including fields from a superseded encoding.
    required = {"export_manifest", *("csv_" + field for field in fields)}
    return authorize_inputs(user_id, specs, purpose=purpose, fields=required, now=now)


def materialize(auth, kind, accesses, expected, fields, generated_at, budget):
    purpose = "export" if kind == "detail" else "export_aggregate"
    relations = [
        build_relation(auth.user_id, access.spec, ctx=access.ctx, purpose=purpose, access=access)
        for access in accesses
    ]
    versions = [rel.input_version for rel in relations]
    if versions != expected:
        raise Denied("input_changed")
    rows = data_rows(kind, relations, generated_at)
    body = encode_csv(fields, rows, budget)
    checksum = hashlib.sha256(body).hexdigest()
    basis = [entry for rel in relations for entry in rel.basis]
    # "until" sets the ticket deadline. "grant_until" describes the effective grants.
    signed_basis = [
        {key: value for key, value in entry.items() if key != "until"} for entry in basis
    ]
    signature = token(
        "s05-export-projection", [auth.user_id, kind, FORMAT, fields, signed_basis, checksum]
    )
    deadline = min(
        [generated_at + TICKET_TTL, *(rel.until for rel in relations if rel.until is not None)]
    )
    return relations, body, checksum, basis, signature, deadline, len(rows)


def ticket_metadata(ticket):
    return {
        "id": str(ticket.pk),
        "kind": ticket.kind,
        "created_at": ticket.created_at,
        "expires_at": ticket.expires_at,
    }


def check_quota(user_id, now):
    current = ExportTicket.objects.filter(owner_id=user_id)
    if (
        current.filter(expires_at__gt=now, invalidated_at__isnull=True).count() >= TICKET_LIMIT
        or current.filter(created_at__gt=now - timedelta(minutes=1)).count() >= TICKET_RATE
    ):
        raise Denied("rate_limited")


def prepare(request):
    kind, specs, versions, fields = normalize(request.data)
    synthetic = specs[0].synthetic
    values = params(request.query_params, {"scope"})
    if values and scope(values) != synthetic:
        raise Denied("invalid_request")
    with request_transaction() as budget:
        export_accesses(request.user.pk, kind, specs, fields)
        lock_dependencies(specs)
        auth = SessionCheck(request)
        accesses = export_accesses(auth.user_id, kind, specs, fields, now=auth.now)
        values = materialize(auth, kind, accesses, versions, fields, auth.now, budget)
        relations, body, checksum, basis, signature, deadline, count = values
        now = auth.finish(relations)
        if deadline <= now:
            raise Denied("expired")
        if not count:
            return Response({"state": "no_data"})
        check_quota(auth.user_id, now)
        ticket = ExportTicket.objects.create(
            owner_id=auth.user_id,
            synthetic_scope=synthetic,
            kind=kind,
            format_version=FORMAT,
            groups=[spec.canonical for spec in specs],
            fields=list(fields),
            basis=basis,
            input_versions=versions,
            projection_signature=signature,
            data_sha256=checksum,
            row_count=count,
            byte_count=len(body),
            created_at=auth.now,
            expires_at=deadline,
        )
        ticket.products.set({spec.product_id for spec in specs})
        if auth.finish(relations) >= deadline:
            raise Denied("expired")
    return Response(ticket_metadata(ticket), status=201)


def frozen_specs(ticket):
    # Intents are stored on a private row with a fixed format and are never patched.
    return [parse_spec(group, export=True) for group in ticket.groups]


@never_cache
@api_view(["GET", "POST"])
def exports(request):
    try:
        if request.method == "POST":
            return prepare(request)
        with request_transaction():
            return paged(ExportTicket, request, "exports", ticket_metadata)
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET", "DELETE"])
def export_ticket(request, item_id):
    try:
        synthetic = scope(params(request.query_params, {"scope"}))
        with request_transaction():
            item = owned(
                ExportTicket, request.user.pk, synthetic, item_id, lock=request.method == "DELETE"
            )
            SessionCheck(request)
            if request.method == "DELETE":
                item.delete()
                return Response(status=204)
            return Response(ticket_metadata(item))
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET"])
def download_export(request, item_id):
    try:
        synthetic = scope(params(request.query_params, {"scope"}))
        user_id = request.user.pk
        with request_transaction() as budget:
            snapshot = owned(ExportTicket, user_id, synthetic, item_id)
            specs = frozen_specs(snapshot)
            export_accesses(user_id, snapshot.kind, specs, snapshot.fields)
            lock_dependencies(specs)
            ticket = owned(ExportTicket, user_id, synthetic, item_id, lock=True)
            auth = SessionCheck(request)
            if ticket.invalidated_at is not None:
                raise Denied()
            if snapshot.groups != ticket.groups:
                raise Denied("input_changed")
            accesses = export_accesses(
                auth.user_id, ticket.kind, specs, ticket.fields, now=auth.now
            )
            if ticket.format_version != FORMAT or any(
                s.method_version != METHOD_VERSION for s in specs
            ):
                raise Denied("method_changed")
            values = materialize(
                auth,
                ticket.kind,
                accesses,
                ticket.input_versions,
                tuple(ticket.fields),
                ticket.created_at,
                budget,
            )
            relations, body, checksum, _, signature, _, count = values
            now = auth.finish(relations)
            if now >= ticket.expires_at:
                raise Denied("expired")
            if not constant_time_compare(signature, ticket.projection_signature):
                if not constant_time_compare(checksum, ticket.data_sha256):
                    raise Denied("input_changed")
                raise Denied()
            if not (
                count == ticket.row_count
                and len(body) == ticket.byte_count
                and constant_time_compare(checksum, ticket.data_sha256)
            ):
                raise Denied("input_changed")
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="air-quality.csv"'
        return response
    except Denied as exc:
        return reject(exc)
