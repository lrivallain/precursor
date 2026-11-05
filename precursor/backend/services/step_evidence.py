"""Measured facts for assessing a deliverable that isn't plain text.

A step that builds a deck or a report hands over a file path. The assessor model
has no tools, so on its own it can only see the path and must list everything as
an assumption. This module reads what the app can verify for it: whether the
file exists, a few structural checks, a bounded excerpt, and what the draft turn
actually ran. The checks are deterministic, so they can cap a score the model
was too generous with.
"""

from __future__ import annotations

import contextlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import Message, MessageRole

PATH_RE = re.compile(r"(?<![\w./:~-])(~?/(?:[\w.@+%-]+/)*[\w.@+%-]+\.[A-Za-z0-9]{1,6})(?![\w/])")
_MAX_PATHS = 4
_MAX_READ = 400_000
_EXCERPT_CHARS = 2500
_TRACE_CALLS = 12
_TRACE_RESULT_CHARS = 240
_TRACE_ARG_CHARS = 200
_TEXT_SUFFIXES = {".html", ".htm", ".md", ".txt", ".json", ".csv", ".svg", ".xml", ".yml", ".yaml"}
_OFFICE_PARTS = {
    ".pptx": "ppt/slides/slide",
    ".docx": "word/document.xml",
    ".xlsx": "xl/worksheets/",
}
_COUNT_RE = re.compile(r"\b(\d{1,3})[\s-]+(?:slides?|pages?|sections?)\b", re.IGNORECASE)

# Failing one of these caps the score, whatever the model thought of the content.
CAP_MISSING = 25
CAP_BROKEN = 40
CAP_COUNT = 60


@dataclass
class Check:
    label: str
    ok: bool


@dataclass
class Evidence:
    checks: list[Check] = field(default_factory=list)
    excerpts: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    # Readable files (resolved) and the full output of each traced call, for the
    # assessor's read-only tools.
    files: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    cap: int | None = None

    def fail(self, label: str, cap: int) -> None:
        self.checks.append(Check(label, False))
        self.cap = cap if self.cap is None else min(self.cap, cap)

    @property
    def failures(self) -> list[str]:
        return [c.label for c in self.checks if not c.ok]

    def prompt(self) -> str:
        """The blocks appended to an assessor's input; empty when nothing was found."""
        parts: list[str] = []
        if self.checks:
            lines = "\n".join(f"- [{'ok' if c.ok else 'FAILED'}] {c.label}" for c in self.checks)
            parts.append(
                "Verified by the app (measured on this machine, not claimed by the model; "
                "treat them as evidence):\n" + lines
            )
        parts.extend(self.excerpts)
        if self.trace:
            parts.append(
                "What the draft actually ran (tool calls, oldest first):\n"
                + "\n".join(f"{i}. {line}" for i, line in enumerate(self.trace, 1))
            )
        return "\n\n".join(parts)


class _Page(HTMLParser):
    """Slide count, titles and visible text of an HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.slides = 0
        self.sections = 0
        self.covers = 0
        self.titles: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "section":
            self.sections += 1
        if "slide" in a.get("class", "").split():
            self.slides += 1
            if a.get("data-template") in ("cover", "title"):
                self.covers += 1
            if a.get("data-title"):
                self.titles.append(a["data-title"])
        self._heading = tag in ("h1", "h2") and not self.titles

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("h1", "h2"):
            self._heading = False

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            return
        self.text.append(data.strip())
        if self._heading and len(self.titles) < 12:
            self.titles.append(data.strip())


def _visible(path: Path) -> bool:
    # The app's own workspaces live under a data dir that may itself be hidden.
    workspaces = Path(get_settings().workspaces_dir)
    if path.is_relative_to(workspaces):
        path = Path("/", *path.relative_to(workspaces).parts)
    return not any(part.startswith(".") for part in path.parts[1:])


def _size(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _asked_count(text: str) -> int | None:
    found = {int(m.group(1)) for m in _COUNT_RE.finditer(text)}
    return found.pop() if len(found) == 1 else None


def _inspect_html(path: Path, ev: Evidence, asked: int | None) -> None:
    page = _Page()
    try:
        page.feed(path.read_text(encoding="utf-8", errors="replace")[:_MAX_READ])
    except Exception:
        ev.fail("The HTML file could not be parsed", CAP_BROKEN)
        return
    count = page.slides or page.sections
    if count:
        ev.checks.append(Check(f"The deck holds {count} slide(s)", True))
        # A cover slide is furniture, so "4 slides" may be a cover plus 4.
        if asked is not None and asked not in (count, count - page.covers):
            ev.fail(f"The ask wants {asked} slide(s) but the file holds {count}", CAP_COUNT)
    if page.titles:
        ev.checks.append(Check("Slide titles: " + "; ".join(page.titles[:10]), True))
    body = " ".join(page.text)
    if not body:
        ev.fail("The HTML file has no visible text", CAP_BROKEN)
    else:
        ev.excerpts.append(
            f"Visible text of {path.name} (start):\n<<<FILE\n{body[:_EXCERPT_CHARS]}\nFILE>>>"
        )


def _inspect_text(path: Path, ev: Evidence, asked: int | None) -> None:
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        _inspect_html(path, ev, asked)
        return
    text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_READ]
    if suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8")[: 5 * _MAX_READ])
        except ValueError:
            ev.fail("The JSON file does not parse", CAP_BROKEN)
            return
        ev.checks.append(Check("The JSON file parses", True))
    if suffix == ".md":
        heads = [ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")]
        if heads:
            ev.checks.append(Check("Headings: " + "; ".join(heads[:10]), True))
    ev.excerpts.append(f"Start of {path.name}:\n<<<FILE\n{text[:_EXCERPT_CHARS]}\nFILE>>>")


def _inspect_office(path: Path, ev: Evidence, asked: int | None) -> None:
    marker = _OFFICE_PARTS[path.suffix.lower()]
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        ev.fail(f"{path.name} is not a valid Office file", CAP_BROKEN)
        return
    ev.checks.append(Check(f"{path.name} is a valid Office file", True))
    if path.suffix.lower() == ".pptx":
        count = len([n for n in names if n.startswith(marker) and n.endswith(".xml")])
        ev.checks.append(Check(f"The deck holds {count} slide(s)", True))
        if asked is not None and asked != count:
            ev.fail(f"The ask wants {asked} slide(s) but the file holds {count}", CAP_COUNT)


def inspect_file(raw: str, *, produced: bool, asked: int | None, claimed: bool = True) -> Evidence:
    """What can be checked about one file named in an artifact.

    Contents are only read for a visible path a tool of this plan mentioned, so a
    model-supplied path can't make the app send an unrelated file to the provider.
    """
    ev = Evidence()
    path = Path(raw).expanduser()
    try:
        info = path.stat() if path.is_file() else None
    except OSError:
        info = None
    if info is None:
        # A URL-like path in prose isn't a file claim: it needs a tool that touched it,
        # a deliverable position, or a folder that exists.
        if produced or claimed or path.parent.is_dir():
            ev.fail(f"File not found: {raw}", CAP_MISSING)
        return ev
    when = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
    ev.checks.append(Check(f"File exists: {raw} ({_size(info.st_size)}, modified {when})", True))
    if info.st_size == 0:
        ev.fail(f"{path.name} is empty", CAP_MISSING)
        return ev
    if not (produced and _visible(path.resolve())):
        return ev
    ev.files.append(str(path.resolve()))
    suffix = path.suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            _inspect_text(path, ev, asked)
        elif suffix in _OFFICE_PARTS:
            _inspect_office(path, ev, asked)
    except OSError:
        ev.checks.append(Check(f"{path.name} could not be read", False))
    return ev


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip().strip("`*").strip()
    return ""


def existing_paths(texts: list[str]) -> list[str]:
    """The files named in ``texts`` that exist on this machine."""
    found: list[str] = []
    for text in texts:
        for match in PATH_RE.finditer(text):
            raw = match.group(1)
            if raw not in found and Path(raw).expanduser().is_file():
                found.append(raw)
    return found[:_MAX_PATHS]


def _digest(meta: dict[str, Any], result: str) -> str:
    args = meta.get("arguments")
    if isinstance(args, str):
        with contextlib.suppress(ValueError):
            args = json.loads(args)
    if isinstance(args, dict):
        shown = args.get("command") or args.get("path") or args.get("name") or args
    else:
        shown = args
    shown = " ".join(str(shown).split())[:_TRACE_ARG_CHARS]
    outcome = "ERROR " if meta.get("is_error") else ""
    tail = " ".join(result.split())[:_TRACE_RESULT_CHARS]
    return f"{meta.get('name', 'tool')}({shown}) -> {outcome}{tail}"


async def collect(
    session: AsyncSession,
    *,
    texts: list[str],
    chat_ids: list[int | None],
    ask: str,
    with_trace: bool = True,
    expect_files: bool = False,
) -> Evidence:
    """Verify the files named in ``texts`` and digest what the chats' tools ran.

    A path counts as a claimed deliverable when it is the first line of a text, or
    when ``expect_files`` says the work is known to deliver files.
    """
    ev = Evidence()
    known: list[str] = []
    for chat_id in chat_ids:
        if chat_id is None:
            continue
        rows = (
            await session.execute(
                select(Message)
                .where(Message.chat_id == chat_id, Message.role == MessageRole.TOOL)
                .order_by(Message.id.desc())
                .limit(_TRACE_CALLS)
            )
        ).scalars()
        lines = []
        outputs = []
        for row in rows:
            try:
                meta = json.loads(row.tool_calls) if row.tool_calls else {}
            except ValueError:
                meta = {}
            known.append(f"{row.tool_calls or ''}\n{row.content or ''}")
            lines.append(_digest(meta, row.content or ""))
            outputs.append(row.content or "")
        if with_trace:
            ev.trace.extend(reversed(lines))
            ev.outputs.extend(reversed(outputs))
    corpus = "\n".join(known)
    asked = _asked_count(ask)
    seen: list[str] = []
    for text in texts:
        for match in PATH_RE.finditer(text):
            if match.group(1) not in seen:
                seen.append(match.group(1))
    for raw in seen[:_MAX_PATHS]:
        one = inspect_file(
            raw,
            produced=raw in corpus,
            asked=asked,
            claimed=expect_files or raw in corpus or any(_first_line(t) == raw for t in texts),
        )
        ev.checks.extend(one.checks)
        ev.excerpts.extend(one.excerpts)
        ev.files.extend(f for f in one.files if f not in ev.files)
        if one.cap is not None:
            ev.cap = one.cap if ev.cap is None else min(ev.cap, one.cap)
    return ev
