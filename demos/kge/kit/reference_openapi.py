"""The reference OpenAPI 3.0.3 tool spec for /context, /ask, /search and /data/query.

Built from the live API schema. The kit ships kit/openapi/kge-tools.openapi.yaml, which the unit
test holds equal to this module, so a schema change cannot ship unnoticed. The kit build copies
that file and does not import the app: its Python is the standard library alone.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

PATHS = ("/context", "/ask", "/ask/runs/{run_id}", "/search", "/data/query")
SCHEMA_REF = "#/components/schemas/"
SPEC_PATH = Path(__file__).resolve().parent / "openapi" / "kge-tools.openapi.yaml"
KGE_AUTHORIZATION = {
    "name": "X-KGE-Authorization",
    "in": "header",
    "description": (
        "KGE sign-in, as `Bearer <token>`. On a private Cloud Run service the Google identity "
        "token is a separate header (`X-Serverless-Authorization`) and does not replace this one."
    ),
    "required": True,
    "schema": {"type": "string"},
}


def reference_spec(schema: dict | None = None) -> dict:
    """The operations, in OpenAPI 3.0.3, taken from the live schema with X-KGE-Authorization declared."""
    if schema is None:
        from kge.app import app
        schema = app.openapi()
    operations = {}
    for path in PATHS:
        method = "get" if path == "/ask/runs/{run_id}" else "post"
        operation = to_openapi_30(copy.deepcopy(schema["paths"][path][method]))
        parameters = list(operation.get("parameters") or [])
        if not any(item.get("name") == "X-KGE-Authorization" and item.get("in") == "header" for item in parameters):
            parameters.append(copy.deepcopy(KGE_AUTHORIZATION))
        operation["parameters"] = parameters
        operations[path] = {method: operation}
    names = schema_names(operations)
    components = schema.get("components", {}).get("schemas", {})
    pending = True
    while pending:
        pending = False
        for name in list(names):
            for extra in schema_names(components[name]) - names:
                names.add(extra)
                pending = True
    return {
        "openapi": "3.0.3",
        "info": {
            "title": schema["info"]["title"],
            "version": schema["info"]["version"],
            "description": (
                "Reference tool spec for an external agent calling a private KGE service. "
                "Send the KGE bearer token in X-KGE-Authorization."
            ),
        },
        "servers": [{"url": "https://<KGE_SERVICE_URL>"}],
        "paths": operations,
        "components": {"schemas": {name: to_openapi_30(copy.deepcopy(components[name])) for name in sorted(names)}},
    }


def yaml_text(spec: dict | None = None) -> str:
    """The spec as YAML, stable for the same schema."""
    text = yaml.safe_dump(spec if spec is not None else reference_spec(), sort_keys=False, allow_unicode=True, width=10**9)
    return text if text.endswith("\n") else text + "\n"


def schema_names(node) -> set[str]:
    found = set()

    def walk(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith(SCHEMA_REF):
                found.add(ref[len(SCHEMA_REF):])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def to_openapi_30(node):
    """JSON Schema as OpenAPI 3.0.3 spells it: no null type, no const, no keyword beside $ref."""
    if isinstance(node, list):
        return [to_openapi_30(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: to_openapi_30(value) for key, value in node.items()}
    if "const" in node:
        node["enum"] = [node.pop("const")]
    kinds = node.get("type")
    if isinstance(kinds, list):
        rest = [kind for kind in kinds if kind != "null"]
        if "null" in kinds and len(rest) == 1:
            node["type"] = rest[0]
            node["nullable"] = True
        elif kinds == ["null"]:
            node.pop("type", None)
            node["nullable"] = True
    options = node.get("anyOf")
    if isinstance(options, list):
        others = [item for item in options if item != {"type": "null"}]
        if others and len(others) < len(options):
            siblings = {key: value for key, value in node.items() if key != "anyOf"}
            if len(others) == 1:
                kept = others[0]
                if "$ref" in kept:
                    siblings["nullable"] = True
                    siblings["allOf"] = [{"$ref": kept["$ref"]}]
                    node = siblings
                else:
                    kept = dict(kept)
                    kept["nullable"] = True
                    for key, value in siblings.items():
                        kept.setdefault(key, value)
                    node = kept
            else:
                node = {**siblings, "anyOf": others, "nullable": True}
    for bound, key in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
        if key in node and not isinstance(node[key], bool):
            node.setdefault(bound, node[key])
            node[key] = True
    if "$ref" in node and len(node) > 1:
        ref = node.pop("$ref")
        node["allOf"] = [{"$ref": ref}]
    return node


def main() -> None:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(yaml_text())


if __name__ == "__main__":
    main()
