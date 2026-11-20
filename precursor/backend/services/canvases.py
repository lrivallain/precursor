"""Conversation canvases: visuals a conversation draws, kept as workspace files.

A canvas is a file in the built-in ``canvas`` workspace, under a folder named
after the conversation's public id (``<public_id>/<name><suffix>``). Its kind
comes from the suffix. Writing one through the canvas tools makes it a
conversation artifact like any other workspace file (the turn engine records the
write), so the Files section, the artifacts list and the model's context all see
it without a table of their own. A reply shows it with a ```canvas block naming
its path.

Kept free of the LLM and git stacks: the stdio MCP server imports it.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.clock import utcnow
from precursor.backend.config import CANVAS_WORKSPACE_SLUG, get_settings
from precursor.backend.models import Chat, Topic, Workspace
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services.slugs import slugify

if TYPE_CHECKING:
    from precursor.backend.schemas.workspace import FileNode

logger = logging.getLogger(__name__)

CanvasKind = Literal["html", "drawio", "mermaid", "svg"]
ConversationKind = Literal["topic", "chat"]

WORKSPACE_NAME = "Canvas"
# Longest suffix first, so ``.canvas.html`` wins over a plain ``.html`` reading.
SUFFIXES: dict[CanvasKind, str] = {
    "html": ".canvas.html",
    "drawio": ".drawio",
    "mermaid": ".mmd",
    "svg": ".svg",
}
# Per-canvas JSON a canvas page saves through the SDK, beside the canvases.
STATE_DIR = ".state"
MAX_CANVAS_BYTES = 2_000_000
MAX_STATE_BYTES = 500_000
# Bounds what the system prompt lists.
MAX_PROMPT_CANVASES = 20

_CONVERSATION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CanvasError(Exception):
    """A canvas request was refused; the message is safe to show the caller."""


@dataclass(slots=True)
class CanvasInfo:
    name: str
    kind: CanvasKind
    path: str
    bytes: int
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "bytes": self.bytes,
            "updated_at": self.updated_at.isoformat(),
            "embed": embed_block(self.path),
        }


# --------------------------------------------------------------------------
# workspace and paths
# --------------------------------------------------------------------------
def canvas_root() -> Path:
    """The canvas workspace's folder (it has no subdir)."""
    return Path(get_settings().workspaces_dir) / CANVAS_WORKSPACE_SLUG


async def ensure_canvas_workspace(session: AsyncSession) -> Workspace:
    """The built-in canvas workspace, created (with its folder) if missing."""
    ws = (
        await session.execute(select(Workspace).where(Workspace.slug == CANVAS_WORKSPACE_SLUG))
    ).scalar_one_or_none()
    if ws is None:
        now = utcnow()
        ws = Workspace(
            name=WORKSPACE_NAME,
            slug=CANVAS_WORKSPACE_SLUG,
            kind="local",
            branch="main",
            cloned_at=now,
            last_synced_at=now,
        )
        session.add(ws)
        await session.commit()
        logger.info("Created the %r workspace for conversation canvases", WORKSPACE_NAME)
    canvas_root().mkdir(parents=True, exist_ok=True)
    return ws


def check_conversation_id(conversation_id: str) -> str:
    value = (conversation_id or "").strip().lower()
    if not _CONVERSATION_ID_RE.match(value):
        raise CanvasError("conversation_id must be the conversation's id (a UUID).")
    return value


def canvas_name(raw: str) -> str:
    """A file-safe canvas name: ``Cycle de vie TCP`` → ``cycle-de-vie-tcp``."""
    for suffix in SUFFIXES.values():
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    name = slugify(raw)[:64].strip("-")
    if not name:
        raise CanvasError("A canvas needs a name with letters or digits.")
    return name


def kind_for(path: str) -> CanvasKind | None:
    lower = path.lower()
    for kind, suffix in SUFFIXES.items():
        if lower.endswith(suffix):
            return kind
    return None


def canvas_path(conversation_id: str, name: str, kind: CanvasKind) -> str:
    return f"{conversation_id}/{name}{SUFFIXES[kind]}"


def embed_block(path: str) -> str:
    """What a reply writes to show the canvas inline."""
    return f"```canvas\n{path}\n```"


def _info(conversation_id: str, file: Path) -> CanvasInfo | None:
    kind = kind_for(file.name)
    if kind is None or not file.is_file():
        return None
    stat = file.stat()
    return CanvasInfo(
        name=file.name[: -len(SUFFIXES[kind])],
        kind=kind,
        path=f"{conversation_id}/{file.name}",
        bytes=stat.st_size,
        updated_at=datetime.fromtimestamp(stat.st_mtime, UTC),
    )


def list_canvases(conversation_id: str) -> list[CanvasInfo]:
    """This conversation's canvases, most recently changed first."""
    folder = canvas_root() / check_conversation_id(conversation_id)
    if not folder.is_dir():
        return []
    found = [info for f in folder.iterdir() if (info := _info(conversation_id, f)) is not None]
    return sorted(found, key=lambda c: c.updated_at, reverse=True)


def find_canvas(conversation_id: str, name: str) -> CanvasInfo | None:
    wanted = canvas_name(name)
    for info in list_canvases(conversation_id):
        if info.name == wanted:
            return info
    return None


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------
def read_canvas(info: CanvasInfo) -> str:
    return fs.read_text(canvas_root(), info.path)


def write_canvas(conversation_id: str, name: str, kind: CanvasKind, content: str) -> CanvasInfo:
    """Create or replace a canvas. A same-named canvas of another kind is replaced."""
    conversation_id = check_conversation_id(conversation_id)
    if kind not in SUFFIXES:
        raise CanvasError(f"kind must be one of: {', '.join(SUFFIXES)}.")
    if len(content.encode("utf-8")) > MAX_CANVAS_BYTES:
        raise CanvasError(f"A canvas is limited to {MAX_CANVAS_BYTES // 1_000_000} MB.")
    name = canvas_name(name)
    previous = find_canvas(conversation_id, name)
    path = canvas_path(conversation_id, name, kind)
    fs.write_text(canvas_root(), path, content)
    if previous is not None and previous.path != path:
        fs.delete_file(canvas_root(), previous.path)
    info = _info(conversation_id, canvas_root() / path)
    assert info is not None
    return info


def apply_edits(content: str, edits: list[dict[str, str]]) -> str:
    """Apply ``{"find", "replace"}`` edits in order; each ``find`` must match once."""
    if not edits:
        raise CanvasError("Give at least one edit.")
    for i, edit in enumerate(edits, start=1):
        find = edit.get("find") or ""
        if not find:
            raise CanvasError(f"Edit {i}: 'find' is empty.")
        count = content.count(find)
        if count != 1:
            where = "not found" if count == 0 else f"found {count} times; make it unique"
            raise CanvasError(f"Edit {i}: the 'find' text was {where}.")
        content = content.replace(find, edit.get("replace") or "", 1)
    return content


def _state_path(conversation_id: str, name: str) -> str:
    return f"{check_conversation_id(conversation_id)}/{STATE_DIR}/{canvas_name(name)}.json"


def read_state(conversation_id: str, name: str) -> Any:
    try:
        return json.loads(fs.read_text(canvas_root(), _state_path(conversation_id, name)))
    except (FileNotFoundError, ValueError):
        return None


def write_state(conversation_id: str, name: str, state: Any) -> None:
    text = json.dumps(state, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_STATE_BYTES:
        raise CanvasError(f"A canvas state is limited to {MAX_STATE_BYTES // 1_000} KB.")
    fs.write_text(canvas_root(), _state_path(conversation_id, name), text)


def remove_conversation_canvases(conversation_id: str) -> None:
    """Drop a deleted conversation's canvas folder. Never raises."""
    try:
        folder = canvas_root() / check_conversation_id(conversation_id)
    except CanvasError:
        return
    shutil.rmtree(folder, ignore_errors=True)


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------
async def conversation_public_id(
    session: AsyncSession, kind: ConversationKind, container_id: int
) -> str | None:
    row: Topic | Chat | None = (
        await session.get(Topic, container_id)
        if kind == "topic"
        else await session.get(Chat, container_id)
    )
    return row.public_id if row is not None else None


async def conversation_exists(session: AsyncSession, conversation_id: str) -> bool:
    chat = await session.execute(select(Chat.id).where(Chat.public_id == conversation_id))
    if chat.first() is not None:
        return True
    topic = await session.execute(select(Topic.id).where(Topic.public_id == conversation_id))
    return topic.first() is not None


async def label_conversation_folders(session: AsyncSession, nodes: list[FileNode]) -> None:
    """Label each top-level conversation folder with its conversation's title.

    The folders are named after an id, which tells the user nothing; the label
    (``Topic: <title>`` / ``Chat: <title>``) is shown when hovering one.
    """
    folders = {n.path: n for n in nodes if n.type == "dir" and _CONVERSATION_ID_RE.match(n.path)}
    for public_id, label in (await conversation_labels(session, list(folders))).items():
        folders[public_id].label = label


async def conversation_labels(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """``public_id`` → ``Topic: <title>`` / ``Chat: <title>`` for the ids that exist."""
    if not ids:
        return {}
    labels: dict[str, str] = {}
    for model, kind in ((Topic, "Topic"), (Chat, "Chat")):
        rows = await session.execute(
            select(model.public_id, model.title).where(model.public_id.in_(ids))
        )
        for public_id, title in rows:
            labels[public_id] = f"{kind}: {title}"
    return labels


def canvas_prompt(conversation_id: str) -> str:
    """What a turn is told about drawing in this conversation."""
    lines = [
        "Canvases: you can illustrate this conversation with visuals the user sees inline "
        "(interactive HTML, draw.io diagrams, Mermaid, SVG). Reach for one when a picture, a "
        "diagram or a small simulation explains better than prose. "
        f"This conversation's conversation_id is {conversation_id}. Call canvas_guide once "
        "before your first canvas, then write_canvas (or edit_canvas to change one), and "
        "paste the `embed` block the tool returns into your reply where the visual belongs.",
    ]
    canvases = list_canvases(conversation_id)
    if canvases:
        lines.append("Canvases of this conversation (read_canvas / edit_canvas take the name):")
        for c in canvases[:MAX_PROMPT_CANVASES]:
            lines.append(f"- {c.name} ({c.kind}, embed path: {c.path})")
    return "\n".join(lines)
