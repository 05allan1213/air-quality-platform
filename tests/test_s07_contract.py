"""Executable drift checks for the S07 v1 OpenAPI candidate."""

from collections import Counter
from copy import deepcopy

from jsonschema import Draft202012Validator
from openapi_spec_validator import OpenAPIV31SpecValidator, validate
from s07_contract import (
    METHODS,
    NATIVE,
    PREFIX,
    dereference,
    routes,
    schema_errors,
    specification,
    validate_schema,
)


def assert_local_refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            dereference(node)
        for value in node.values():
            assert_local_refs(value)
    elif isinstance(node, list):
        for value in node:
            assert_local_refs(value)


def test_spec_31_and_local_json_schema():
    spec = specification()
    assert spec["openapi"] == "3.1.0"
    assert_local_refs(spec)
    validate(spec, cls=OpenAPIV31SpecValidator)
    for schema in spec["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)


def test_resolver_operation_diff_and_converter_contract():
    spec = specification()
    declared = {
        (path, method): operation
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
        if method in METHODS
    }
    resolved = list(routes())
    counts = Counter(
        (path, method)
        for path, _, methods, _ in resolved
        if path.startswith(PREFIX)
        for method in methods
    )
    assert counts and all(count == 1 for count in counts.values()), [
        operation for operation, count in counts.items() if count != 1
    ]
    assert counts.keys() == declared.keys(), {
        "missing": sorted(counts.keys() - declared.keys()),
        "extra": sorted(declared.keys() - counts.keys()),
    }
    assert len({operation["operationId"] for operation in declared.values()}) == len(declared)
    assert {path for path, _, _, _ in resolved if not path.startswith(PREFIX)} == {
        "/admin/",
        *(path for path in NATIVE if not path.startswith(PREFIX)),
    }
    for path, converters, methods, view in resolved:
        if not path.startswith(PREFIX):
            continue
        assert path.endswith("/") and view
        for method in methods:
            operation = declared[path, method]
            assert operation["description"]
            assert operation["responses"].get("405")
            assert operation["security"] or path == PREFIX + "auth/csrf/"
            parameters = [dereference(p) for p in operation.get("parameters", [])]
            assert len({(p["in"], p["name"]) for p in parameters}) == len(parameters)
            placeholders = {param["name"]: param for param in parameters if param["in"] == "path"}
            assert placeholders.keys() == converters.keys()
            for name, converter in converters.items():
                parameter = placeholders[name]
                schema = parameter["schema"]
                assert parameter["required"]
                if converter == "uuid":
                    assert schema["format"] == "uuid"
                elif converter == "int":
                    assert schema["type"] == "integer"
                else:
                    assert converter == "str" and schema["type"] == "string"


def exact_shape(response):
    schema = {"$ref": "#/components/schemas/ExactResult"}
    validate_schema(response, schema)
    obsolete = deepcopy(response)
    obsolete["results"][0]["input_version"] = {
        "revision": response["results"][0]["revision_id"],
        "rights": 3,
        "grants": [15],
    }
    assert schema_errors(obsolete, schema)
    for field in ("rights_version", "rights", "grants"):
        for target in ("row", "envelope"):
            leaked = deepcopy(response)
            (leaked["results"][0] if target == "row" else leaked)[field] = 1
            assert schema_errors(leaked, schema)
