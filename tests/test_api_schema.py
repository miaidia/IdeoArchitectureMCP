"""OpenAPI document validation (Phase 16; F-0558 — the structural gap).

The Phase 14 test asserts the expected PATHS exist; the gap closed here is that
the served document is a structurally VALID OpenAPI 3.x description:

* document-level invariants (version, info, non-empty paths);
* every operation has responses, every response has a description;
* every ``$ref`` in the document resolves inside ``components``;
* every operation carries the security requirement except the documented
  public endpoints (healthz/metrics/openapi itself);
* request/response component schemas are valid JSON Schemas (Draft 2020-12
  check_schema on each component).
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tests.api_helpers import build_api

PUBLIC_PATHS = {"/healthz", "/metrics"}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    harness = build_api(monkeypatch)
    yield harness
    from plot_mcp_server import usecases

    usecases.set_connectors(None)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()


@pytest.fixture
def openapi(api) -> dict[str, Any]:
    response = api.client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _operations(doc: dict[str, Any]):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                yield path, method, op


def _iter_refs(node: Any):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for value in node.values():
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def test_document_level_invariants(openapi) -> None:
    assert openapi["openapi"].startswith("3."), "OpenAPI 3.x required"
    assert openapi["info"]["title"]
    assert openapi["info"]["version"]
    assert openapi["paths"], "no paths served"


def test_every_operation_has_described_responses(openapi) -> None:
    for path, method, op in _operations(openapi):
        assert op.get("responses"), f"{method.upper()} {path} has no responses"
        for code, response in op["responses"].items():
            assert "description" in response, f"{method.upper()} {path} {code}"


def test_every_ref_resolves_within_components(openapi) -> None:
    for ref in _iter_refs(openapi):
        assert ref.startswith("#/"), f"external $ref not allowed: {ref}"
        node: Any = openapi
        for part in ref.lstrip("#/").split("/"):
            assert isinstance(node, dict) and part in node, f"unresolvable $ref: {ref}"
            node = node[part]


def test_component_schemas_are_valid_json_schemas(openapi) -> None:
    schemas = openapi.get("components", {}).get("schemas", {})
    assert schemas, "no component schemas"
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)  # raises on an invalid schema
        assert name


def test_protected_operations_require_the_api_key(openapi) -> None:
    """Every /v1 operation declares the API-key security scheme; the public
    endpoints (healthz/metrics) are the only exceptions."""
    security_schemes = openapi.get("components", {}).get("securitySchemes", {})
    assert security_schemes, "no security scheme declared"
    for path, method, op in _operations(openapi):
        if path in PUBLIC_PATHS or not path.startswith("/v1"):
            continue
        requirement = op.get("security", openapi.get("security"))
        assert requirement, f"{method.upper()} {path} lacks a security requirement"
