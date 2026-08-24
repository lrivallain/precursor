"""Core GitHub GraphQL transport tests (shared by core and plugins)."""

from __future__ import annotations

import asyncio
from typing import Any

from precursor.backend.services.github_client import (
    GitHubClient,
    GitHubInsufficientScopeError,
)


def test_graphql_raises_typed_scope_error() -> None:
    """graphql() maps an INSUFFICIENT_SCOPES payload to the typed error."""
    client = GitHubClient(token="tok")

    class _Resp:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "data": {"repositoryOwner": None},
                "errors": [
                    {
                        "type": "INSUFFICIENT_SCOPES",
                        "message": (
                            "requires one of the following scopes: "
                            "['read:project'], but your token ..."
                        ),
                    }
                ],
            }

    async def _fake_post(_path: str, **_kwargs: Any) -> _Resp:
        return _Resp()

    async def _run() -> None:
        client._client.post = _fake_post  # type: ignore[method-assign]
        try:
            await client.graphql("q", {}, raise_on_error=False)
            raise AssertionError("expected GitHubInsufficientScopeError")
        except GitHubInsufficientScopeError as exc:
            assert exc.required_scopes == ["read:project"]
        finally:
            await client.aclose()

    asyncio.run(_run())
