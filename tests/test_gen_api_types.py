"""Tests for the frontend API type generator (scripts/gen_api_types.py).

The generated file types every `call()` in `frontend/src/lib/api.ts`, so the
mapping from OpenAPI to TypeScript (required vs optional, request vs response
shapes, the operations map) is pinned down here on a small hand-written schema.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gen_api_types.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_api_types", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load()


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json(schema: Any) -> dict[str, Any]:
    return {"content": {"application/json": {"schema": schema}}}


OPENAPI: dict[str, Any] = {
    "openapi": "3.1.0",
    "paths": {
        "/api/items": {
            "get": {
                "parameters": [
                    {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}},
                ],
                "responses": {"200": _json({"type": "array", "items": _ref("Item")})},
            },
            "post": {
                "requestBody": {"required": True, **_json(_ref("Item"))},
                "responses": {"201": _json(_ref("Item"))},
            },
        },
        "/api/items/{item_id}": {
            "get": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    },
                    {
                        "name": "since",
                        "in": "query",
                        "required": True,
                        "description": "Only newer rows",
                        "schema": {"type": "integer"},
                    },
                ],
                "responses": {"200": _json(_ref("Item"))},
            },
            "delete": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    },
                ],
                "responses": {"204": {"description": "No Content"}},
            },
        },
        "/api/items/{item_id}/run": {
            "post": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    },
                ],
                "requestBody": {"required": False, **_json(_ref("RunRequest"))},
                "responses": {"200": _json({})},
            },
        },
        "/api/items/{item_id}/export": {
            "get": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    },
                ],
                "responses": {"200": {"content": {"text/plain": {"schema": {"type": "string"}}}}},
            },
        },
        "/api/upload": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"multipart/form-data": {"schema": _ref("Body_upload")}},
                },
                "responses": {"200": _json({"type": "object", "additionalProperties": True})},
            },
        },
    },
    "components": {
        "schemas": {
            "Item": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string", "default": ""},
                },
                "required": ["id"],
            },
            "RunRequest": {
                "type": "object",
                "properties": {"input": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            },
            "Body_upload": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "contentMediaType": "application/octet-stream"},
                },
                "required": ["file"],
            },
        },
    },
}


@pytest.fixture(scope="module")
def output() -> str:
    text: str = gen.render(OPENAPI)
    return text


def _operation(output: str, key: str) -> str:
    start = output.index(f'  "{key}": {{\n')
    end = output.index("\n  };", start)
    return output[start:end]


def test_schemas_on_both_sides_get_an_input_type(output: str) -> None:
    assert "export interface Item {\n  id: number;\n  title: string;\n}" in output
    assert "export interface ItemInput {\n  id: number;\n  title?: string;\n}" in output


def test_uploaded_files_are_blobs(output: str) -> None:
    assert "export interface Body_upload {\n  file: Blob;\n}" in output


def test_operations_are_keyed_by_method_and_path(output: str) -> None:
    assert "export interface Operations {" in output
    keys = [line.split('"')[1] for line in output.splitlines() if line.startswith('  "')]
    assert keys == [
        "GET /api/items",
        "POST /api/items",
        "GET /api/items/{item_id}",
        "DELETE /api/items/{item_id}",
        "GET /api/items/{item_id}/export",
        "POST /api/items/{item_id}/run",
        "POST /api/upload",
    ]


def test_optional_query_and_response_list(output: str) -> None:
    assert _operation(output, "GET /api/items") == (
        '  "GET /api/items": {\n    query?: {\n      q?: string;\n    };\n    response: Item[];'
    )


def test_request_body_uses_the_input_type(output: str) -> None:
    op = _operation(output, "POST /api/items")
    assert "    body: ItemInput;" in op
    assert "    response: Item;" in op


def test_path_and_required_query_with_description(output: str) -> None:
    op = _operation(output, "GET /api/items/{item_id}")
    assert "    path: {\n      item_id: number;\n    };" in op
    assert "    query: {\n      /** Only newer rows */\n      since: number;\n    };" in op


def test_no_content_is_void(output: str) -> None:
    assert "    response: void;" in _operation(output, "DELETE /api/items/{item_id}")


def test_optional_body_and_untyped_response(output: str) -> None:
    op = _operation(output, "POST /api/items/{item_id}/run")
    assert "    body?: RunRequest;" in op
    assert "    response: unknown;" in op


def test_text_response_is_string(output: str) -> None:
    assert "    response: string;" in _operation(output, "GET /api/items/{item_id}/export")


def test_multipart_body_is_form(output: str) -> None:
    op = _operation(output, "POST /api/upload")
    assert "    form: Body_upload;" in op
    assert "    response: Record<string, unknown>;" in op


def test_check_reports_stale_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    schema = tmp_path / "openapi.json"
    schema.write_text(json.dumps(OPENAPI))
    out = tmp_path / "apiTypes.ts"
    monkeypatch.setattr(gen, "OUTPUT", out)
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    assert gen.main(["--schema", str(schema), "--check"]) == 1
    assert gen.main(["--schema", str(schema)]) == 0
    assert gen.main(["--schema", str(schema), "--check"]) == 0
