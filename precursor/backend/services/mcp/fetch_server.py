"""Built-in MCP server: HTTP fetch ("curl-like").

Runs as a stdio subprocess. Exposes two tools:
- ``http_get(url, headers=None)`` — convenience GET.
- ``http_request(url, method="GET", headers=None, body=None, params=None,
  max_bytes=200_000)`` — full GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS.

Each tool returns a dict with ``status``, ``headers``, ``body``,
``truncated``, ``content_type``, and ``url`` (final URL after redirects).
Bodies are decoded as text when possible; binary payloads are base64-encoded
and flagged via ``encoding="base64"``.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_BYTES = 200_000
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

mcp = FastMCP("fetch")


def _decode_body(content: bytes, content_type: str | None) -> tuple[str, str]:
    """Return ``(body, encoding)`` — text when possible, else base64."""
    # httpx already handles charset detection via .text when content-type has
    # one. Here we fall back to utf-8 with replacement so the LLM always gets
    # *something* readable for HTML/JSON/plain text.
    looks_text = False
    if content_type:
        ct = content_type.lower()
        if (
            ct.startswith("text/")
            or "json" in ct
            or "xml" in ct
            or "javascript" in ct
            or "html" in ct
            or "yaml" in ct
        ):
            looks_text = True
    if looks_text or not content:
        try:
            return content.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            pass
    # Best-effort utf-8 with replacement; if it still has many replacement
    # chars, fall back to base64 so the LLM doesn't see garbage.
    try:
        decoded = content.decode("utf-8")
        if decoded.count("\ufffd") < max(10, len(decoded) // 100):
            return decoded, "utf-8"
    except UnicodeDecodeError:
        pass
    return base64.b64encode(content).decode("ascii"), "base64"


async def _do_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    body: str | None,
    params: dict[str, str] | None,
    max_bytes: int,
) -> dict[str, Any]:
    method = method.upper().strip()
    if method not in _ALLOWED_METHODS:
        raise ValueError(f"Unsupported method {method!r}; allowed: {sorted(_ALLOWED_METHODS)}")
    if max_bytes <= 0 or max_bytes > 5_000_000:
        max_bytes = _DEFAULT_MAX_BYTES

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
        resp = await client.request(
            method,
            url,
            headers=headers,
            content=body.encode("utf-8") if body is not None else None,
            params=params,
        )
        raw = resp.content
        truncated = False
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
            truncated = True
        decoded, encoding = _decode_body(raw, resp.headers.get("content-type"))
        return {
            "status": resp.status_code,
            "url": str(resp.url),
            "content_type": resp.headers.get("content-type"),
            "headers": dict(resp.headers),
            "body": decoded,
            "encoding": encoding,
            "truncated": truncated,
            "bytes": len(resp.content),
        }


@mcp.tool()
async def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Fetch the given URL with HTTP GET.

    Returns status code, response headers, and body (text when possible,
    base64 otherwise). Follows redirects. Body is truncated past ``max_bytes``
    (default 200 000).
    """
    return await _do_request(
        "GET",
        url,
        headers=headers,
        body=None,
        params=params,
        max_bytes=max_bytes,
    )


@mcp.tool()
async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    params: dict[str, str] | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Perform an arbitrary HTTP request.

    ``method`` is one of GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS.
    ``body`` is sent as the raw request body (UTF-8 encoded); set the
    matching ``Content-Type`` header yourself (e.g. ``application/json``).
    Follows redirects. Body is truncated past ``max_bytes`` (default
    200 000, hard cap 5 000 000).
    """
    return await _do_request(
        method,
        url,
        headers=headers,
        body=body,
        params=params,
        max_bytes=max_bytes,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
