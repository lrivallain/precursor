"""The cross-window ``mcp.auth_resolved`` broadcast.

When one window renews a WorkIQ sign-in, every *other* window still showing the
``McpAuthBanner`` needs to hear that the credentials are fresh so it drops the
banner without a reload. That signal is a single event-bus broadcast; this
covers that the publisher emits it with the server. (That the reauthenticate
endpoint fires it on a successful sign-in is covered in ``test_workiq_preview``,
alongside the other endpoint tests that share its preview-mode fixture.)
"""

from __future__ import annotations

import asyncio

from precursor.backend.services import events


async def test_publish_mcp_auth_resolved_broadcasts_server() -> None:
    async with events.get_bus().subscribe() as q:
        await events.publish_mcp_auth_resolved("workiq")
        evt = await asyncio.wait_for(q.get(), timeout=2)

    assert evt["type"] == "mcp.auth_resolved"
    assert evt["server"] == "workiq"
