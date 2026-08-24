"""MCP server exposing the kanban boards to the assistant.

Runs as a stdio subprocess launched by Precursor (``python -m
precursor_kanban.mcp_server``) with the app's environment forwarded, so it
resolves the same database, settings and GitHub credentials the UI uses.

Contributed by the plugin via ``registry.add_mcp_server``, which is what lets a
plugin bring tools as well as routes and UI. Tools are read-only: moving a card
is a deliberate act the board already makes easy, and a model shuffling
someone's project board unprompted is not a feature.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from precursor.plugin_api import (
    SessionLocal,
    resolve_github_token,
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor_kanban.client import ProjectsClient

mcp = FastMCP("kanban")


async def _client() -> tuple[ProjectsClient, str]:
    """Build an authenticated client + the configured repo, or explain why not."""
    async with SessionLocal() as session:
        if not await resolve_issue_associations_enabled(session):
            raise ValueError(
                "GitHub issue associations are disabled. Enable them in Settings → GitHub."
            )
        repo = await resolve_global_github_repo(session)
        token = await resolve_github_token(session)
    if not repo:
        raise ValueError("No GitHub repository configured. Set one in Settings → GitHub.")
    if not token:
        raise ValueError(
            "No GitHub token available. Configure one in Settings or run `gh auth login`."
        )
    return ProjectsClient(token=token), repo


@mcp.tool()
async def list_boards() -> list[dict[str, Any]]:
    """List the GitHub Projects v2 boards available on the configured account.

    Returns each board's ``id`` (needed by ``get_board``), ``number``, ``title``
    and ``url``.
    """
    client, repo = await _client()
    try:
        return await client.list_repo_projects(repo)
    finally:
        await client.aclose()


@mcp.tool()
async def get_board(project_id: str) -> dict[str, Any]:
    """Read one board: its Status columns and every issue/PR card on it.

    ``project_id`` is the opaque node id from ``list_boards`` (e.g. ``PVT_…``).
    Each card carries its ``number``, ``title``, ``state``, ``repo``, ``labels``
    and the ``status_name`` column it sits in.
    """
    client, _repo = await _client()
    try:
        return await client.get_project_board(project_id)
    finally:
        await client.aclose()


@mcp.tool()
async def board_summary(project_id: str) -> dict[str, Any]:
    """Summarise a board as counts per column, plus its total card count.

    Cheaper for the model to reason over than the full card list when the
    question is "where does the work stand?".
    """
    client, _repo = await _client()
    try:
        board = await client.get_project_board(project_id)
    finally:
        await client.aclose()
    counts: dict[str, int] = {}
    for card in board.get("items") or []:
        counts[card.get("status_name") or "(no status)"] = (
            counts.get(card.get("status_name") or "(no status)", 0) + 1
        )
    return {
        "title": board.get("title"),
        "url": board.get("url"),
        "total": len(board.get("items") or []),
        "by_column": counts,
    }


def main() -> None:
    from precursor.backend.logging_config import configure_subprocess_logging

    configure_subprocess_logging()
    mcp.run()


if __name__ == "__main__":
    main()
