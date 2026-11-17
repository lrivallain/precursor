"""Canvas demo fixtures: chats illustrated with canvases.

Imported by ``seed_demo.py``: one canvas of each kind (an interactive HTML
simulation, a Mermaid sequence, a draw.io diagram, an SVG), plus a second chat
touring the ``window.precursor`` SDK, written to the built-in Canvas workspace
and attached to their chats. The canvas sources live in
``scripts/demo_canvas_files/``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Chat, ConversationArtifact, Message, MessageRole
from precursor.backend.services import canvases

FILES = Path(__file__).resolve().parent / "demo_canvas_files"
_NOW = datetime.now(UTC)


def _embed(chat: Chat, file: str) -> str:
    return canvases.embed_block(f"{chat.public_id}/{file}")


def _turns(chat: Chat) -> list[tuple[MessageRole, str]]:
    return [
        (
            MessageRole.USER,
            "Explain how TCP congestion control works: slow start, congestion "
            "avoidance and what happens on a loss. Something I can play with, please.",
        ),
        (
            MessageRole.ASSISTANT,
            "TCP never knows the network's capacity, so it **probes** for it with a "
            "*congestion window* (`cwnd`): how many segments may be in flight before "
            "an ACK comes back.\n\n"
            "- **Slow start**: `cwnd` doubles every round trip until it reaches the "
            "*slow-start threshold* (`ssthresh`).\n"
            "- **Congestion avoidance**: past the threshold it grows by one segment "
            "per round trip.\n"
            "- **On a loss** the threshold drops to half the window. Reno resumes "
            "from there (fast recovery); Tahoe restarts from one segment.\n\n"
            "Move the loss and the threshold, switch algorithms, and press play:\n\n"
            + _embed(chat, "tcp-congestion-window.canvas.html")
            + "\n\nThe window caps what the sender may have in flight:\n\n"
            + _embed(chat, "sliding-window.svg"),
        ),
        (
            MessageRole.USER,
            "How is the loss detected, and where does this sit between the two hosts?",
        ),
        (
            MessageRole.ASSISTANT,
            "Three duplicate ACKs tell the sender a segment is missing while later ones "
            "still arrive, so it retransmits at once instead of waiting for a timeout:\n\n"
            + _embed(chat, "tcp-loss-recovery.mmd")
            + "\n\nCongestion control lives in the sender's TCP; the receiver only "
            "advertises its own window (`rwnd`). The real limit is usually a "
            "bottleneck queue in between. The diagram is editable:\n\n"
            + _embed(chat, "tcp-path.drawio"),
        ),
    ]


def _sdk_turns(chat: Chat) -> list[tuple[MessageRole, str]]:
    return [
        (
            MessageRole.USER,
            "Show me what an interactive canvas can do with the Precursor SDK.",
        ),
        (
            MessageRole.ASSISTANT,
            "Here is a tour of `window.precursor`, one card per call. Try them: the "
            "counter survives a reload, **Ask** fills the message box below, and the "
            "theme card follows the app when you switch between light and dark.\n\n"
            + _embed(chat, "sdk-tour.canvas.html"),
        ),
    ]


async def _seed_chat(
    s: AsyncSession,
    workspace_id: int,
    chat: Chat,
    kinds: dict[str, canvases.CanvasKind],
    turns: Callable[[Chat], list[tuple[MessageRole, str]]],
    minutes_ago: int,
) -> None:
    s.add(chat)
    await s.flush()
    for file, kind in kinds.items():
        info = canvases.write_canvas(
            chat.public_id, file, kind, (FILES / file).read_text(encoding="utf-8")
        )
        s.add(
            ConversationArtifact(
                chat_id=chat.id, workspace_id=workspace_id, path=info.path, source="assistant"
            )
        )
    messages = turns(chat)
    for i, (role, content) in enumerate(messages):
        when = _NOW - timedelta(minutes=minutes_ago + (len(messages) - i) * 3)
        s.add(Message(chat_id=chat.id, role=role, content=content, created_at=when))


async def seed_canvas_chat(s: AsyncSession) -> None:
    workspace = await canvases.ensure_canvas_workspace(s)
    await _seed_chat(
        s,
        workspace.id,
        Chat(title="How TCP congestion control works", slug="tcp-congestion-control"),
        {
            "tcp-congestion-window.canvas.html": "html",
            "sliding-window.svg": "svg",
            "tcp-loss-recovery.mmd": "mermaid",
            "tcp-path.drawio": "drawio",
        },
        _turns,
        minutes_ago=0,
    )
    await _seed_chat(
        s,
        workspace.id,
        Chat(title="Canvas SDK tour", slug="canvas-sdk-tour"),
        {"sdk-tour.canvas.html": "html"},
        _sdk_turns,
        minutes_ago=20,
    )
