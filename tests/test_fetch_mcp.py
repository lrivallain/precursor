"""Tests for the built-in 'fetch' MCP server's request-body handling."""

from __future__ import annotations

import json
from typing import ClassVar

from precursor.backend.services.mcp.fetch_server import _encode_body


def test_string_body_is_sent_verbatim() -> None:
    payload, was_json = _encode_body('{"already":"encoded"}')
    assert payload == b'{"already":"encoded"}'
    assert was_json is False


def test_none_body_stays_none() -> None:
    assert _encode_body(None) == (None, False)


def test_object_body_is_serialised() -> None:
    # A model handed a JSON endpoint reaches for the object, not a string of
    # one. Serialising it here is what stops that becoming a validation error
    # the caller cannot see the shape of.
    payload, was_json = _encode_body({"text": "line one\nline two"})
    assert was_json is True
    assert json.loads(payload) == {"text": "line one\nline two"}


def test_array_body_is_serialised() -> None:
    payload, was_json = _encode_body([1, 2])
    assert was_json is True
    assert json.loads(payload) == [1, 2]


def test_non_ascii_survives_as_utf8() -> None:
    payload, _ = _encode_body({"t": "réunion"})
    # Not \u-escaped: the byte stream carries the accent, so a French subject
    # line reaches the server intact.
    assert "réunion".encode() in payload


class _FakeResponse:
    status_code = 200
    content = b"{}"
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}
    url = "http://example.test/"


class _CapturingClient:
    """Stands in for ``httpx.AsyncClient`` and records the outgoing request."""

    captured: ClassVar[dict[str, object]] = {}

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        _CapturingClient.captured = {"method": method, "url": url, **kwargs}
        return _FakeResponse()


async def test_object_body_sets_json_content_type(monkeypatch) -> None:
    from precursor.backend.services.mcp import fetch_server

    monkeypatch.setattr(fetch_server.httpx, "AsyncClient", _CapturingClient)
    await fetch_server.http_request("http://example.test/", method="POST", body={"text": "hi"})
    headers = _CapturingClient.captured["headers"]
    assert headers["Content-Type"] == "application/json"


async def test_caller_content_type_is_not_overridden(monkeypatch) -> None:
    from precursor.backend.services.mcp import fetch_server

    monkeypatch.setattr(fetch_server.httpx, "AsyncClient", _CapturingClient)
    await fetch_server.http_request(
        "http://example.test/",
        method="POST",
        headers={"content-type": "application/vnd.api+json"},
        body={"text": "hi"},
    )
    headers = _CapturingClient.captured["headers"]
    # Matched case-insensitively, so we don't send two conflicting headers.
    assert "Content-Type" not in headers
    assert headers["content-type"] == "application/vnd.api+json"


async def test_string_body_gets_no_implicit_content_type(monkeypatch) -> None:
    from precursor.backend.services.mcp import fetch_server

    monkeypatch.setattr(fetch_server.httpx, "AsyncClient", _CapturingClient)
    await fetch_server.http_request("http://example.test/", method="POST", body="raw")
    assert _CapturingClient.captured["headers"] is None
