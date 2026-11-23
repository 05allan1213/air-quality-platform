"""Helpers for comparing the checked-in candidate to real Django HTTP responses."""

import json
import re
from collections import Counter
from functools import cache
from pathlib import Path

from django.urls import Resolver404, URLResolver, get_resolver, resolve
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/api/openapi.v1.json"
PREFIX = "/api/v1/"
METHODS = {"delete", "get", "patch", "post", "put"}
NATIVE = {
    PREFIX + "auth/csrf/": {"get"},
    PREFIX + "auth/register/": {"post"},
    PREFIX + "auth/login/": {"post"},
    PREFIX + "auth/logout/": {"post"},
    "/health/ready/": {"get"},
}
CONVERTER = re.compile(r"<(\w+):(\w+)>")


def _unique_properties(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f"Duplicate OpenAPI JSON key: {key}"
        result[key] = value
    return result


@cache
def specification():
    return json.loads(SPEC.read_text(), object_pairs_hook=_unique_properties)


def dereference(value):
    if "$ref" not in value:
        return value
    path = value["$ref"]
    assert path.startswith("#/components/"), "Only local OpenAPI components are supported"
    result = specification()
    for part in path[2:].split("/"):
        result = result[part.replace("~1", "/").replace("~0", "~")]
    return result


def schema_errors(instance, schema):
    validator = Draft202012Validator(
        {"components": specification()["components"], **schema},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return list(validator.iter_errors(instance))


class ContractDrift(AssertionError):
    pass


class ContractHttp(AssertionError):
    pass


def validate_schema(instance, schema):
    # Error messages identify only the schema location and rule. No response body,
    # cookies, or request parameters are emitted when a check fails.
    diagnostics = [
        (list(error.absolute_path), list(error.absolute_schema_path), error.validator)
        for error in schema_errors(instance, schema)
    ]
    if diagnostics:
        raise ContractDrift(diagnostics)


def routes(resolver=None, prefix="/"):
    for item in (resolver or get_resolver()).url_patterns:
        path = prefix + str(item.pattern)
        if isinstance(item, URLResolver):
            if path.startswith("/admin/"):
                # The native HTML admin resolver owns this separate namespace.
                yield path, {}, set(), "HTML Admin"
            else:
                yield from routes(item, path)
            continue
        params = {name: converter for converter, name in CONVERTER.findall(path)}
        path = CONVERTER.sub(r"{\2}", path)
        callback = item.callback
        api_view = getattr(callback, "cls", None)
        if api_view:
            methods = set(api_view.http_method_names) & METHODS
        else:
            assert path in NATIVE, f"Unclassified native boundary: {path}"
            methods = NATIVE[path]
        yield path, params, methods, callback.__module__ + "." + callback.__name__


def operation(path, method):
    return specification()["paths"][path][method.lower()]


class Witness:
    """Count schema-checked HTTP exchanges using only operation names and status codes."""

    def __init__(self):
        self.results = Counter()

    def inspect(self, response):
        request = response.request
        if not request.url.path.startswith(PREFIX):
            return
        try:
            match = resolve(request.url.path)
        except Resolver404:
            return
        path = "/" + CONVERTER.sub(r"{\2}", match.route)
        method = request.method.lower()
        known = specification()["paths"].get(path, {})
        if method not in known:  # Head, options and method rejection have separate checks.
            return
        response.read()
        declared = dereference(known[method]["responses"].get(str(response.status_code), {}))
        framework_error = declared.get("x-cache-control-optional", False) and (
            "no-store" not in response.headers.get("Cache-Control", "")
        )
        check_response(response, path, response.status_code, framework_error=framework_error)
        self.results[known[method]["operationId"], response.status_code] += 1

    def receipt(self):
        return [
            {"operation": name, "status": status, "count": count}
            for (name, status), count in sorted(self.results.items())
        ]


def check_response(response, path, status, *, code=None, method=None, framework_error=False):
    if response.status_code != status:
        media = response.headers.get("Content-Type", "")
        received_code = None
        if media == "application/json":
            data = response.json()
            if isinstance(data, dict) and re.fullmatch(
                r"[a-z][a-z0-9_]{1,63}", str(data.get("code", ""))
            ):
                received_code = data["code"]
        raise ContractHttp((path, status, response.status_code, received_code))
    endpoint = operation(path, method or response.request.method)
    if str(status) not in endpoint["responses"]:
        raise ContractHttp((path, method or response.request.method, status, "undeclared_status"))
    expected = dereference(endpoint["responses"][str(status)])
    headers = expected.get("headers", {})
    media = response.headers.get("Content-Type", "").split(";")[0]
    if framework_error:
        assert expected.get("x-cache-control-optional", False), path
    if "Cache-Control" in headers and not framework_error:
        if "no-store" not in response.headers.get("Cache-Control", ""):
            raise ContractHttp((path, method or response.request.method, status, "cache_control"))
    if "Content-Disposition" in headers:
        disposition = dereference(headers["Content-Disposition"])["schema"]
        validate_schema(response.headers["Content-Disposition"], disposition)
    else:
        assert "Content-Disposition" not in response.headers, path
    if status == 405:
        assert response.headers.get("Allow"), path
    if not expected.get("content"):
        assert response.content == b"", path
        return None
    if media not in expected["content"]:
        raise ContractHttp((path, method or response.request.method, status, "media_type"))
    schema = expected["content"][media]["schema"]
    if media != "application/json":
        validate_schema(response.text, schema)
        return None
    parsed = response.json()
    validate_schema(parsed, schema)
    if code:
        assert parsed["code"] == code, (path, status, parsed["code"])
    return parsed
