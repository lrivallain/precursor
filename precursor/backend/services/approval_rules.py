"""Approval rules: standing allow / ask answers in front of the approval policy.

A rule names a tool (``server`` and ``tool`` globs) and optionally constrains its
arguments. ``allow`` lets a matching call run without asking; ``ask`` makes it
wait even where the policy would let it through. Rules scoped to a conversation
beat global ones, and at the same level ``ask`` beats ``allow``, so a narrow
"ask" can carve an exception out of a broad "allow". The first match decides;
no match means the policy decides.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models.approval_rule import (
    RULE_ASK,
    RULE_SCOPE_GLOBAL,
    ApprovalRule,
)

ContainerKind = Literal["topic", "chat"]

OP_EQUALS = "equals"
OP_GLOB = "glob"
OP_UNDER = "under"
ARG_OPS = (OP_EQUALS, OP_GLOB, OP_UNDER)

MAX_CLAUSES = 5


def normalize_path(value: str) -> str:
    """A lexically normalised ``/``-separated path (``a/../b`` → ``b``, no trailing slash)."""
    return posixpath.normpath(value.replace("\\", "/").strip())


def glob_escape(name: str) -> str:
    """``name`` as a glob that matches only itself."""
    return re.sub(r"([*?\[])", r"[\1]", name)


def path_is_under(candidate: str, base: str) -> bool:
    """Whether ``candidate`` is ``base`` or inside it, after normalising both."""
    cand, root = normalize_path(candidate), normalize_path(base)
    if cand == root:
        return True
    return cand.startswith(root if root.endswith("/") else root + "/")


@dataclass(frozen=True, slots=True)
class ArgClause:
    arg: str
    op: str
    value: str

    def matches(self, arguments: dict[str, Any]) -> bool:
        found: Any = arguments
        for part in self.arg.split("."):
            if not isinstance(found, dict) or part not in found:
                return False
            found = found[part]
        if isinstance(found, bool) or not isinstance(found, (str, int, float)):
            return False
        text = str(found)
        if self.op == OP_EQUALS:
            return text == self.value
        if self.op == OP_GLOB:
            return fnmatchcase(text, self.value)
        if self.op == OP_UNDER:
            return isinstance(found, str) and path_is_under(text, self.value)
        return False


@dataclass(frozen=True, slots=True)
class CompiledRule:
    id: int
    effect: str
    server: str
    tool: str
    clauses: tuple[ArgClause, ...] = ()
    note: str | None = None
    # Lower beats higher: 0 = this conversation, 1 = global.
    rank: int = 1

    def matches(self, server: str, raw_tool: str, arguments: dict[str, Any]) -> bool:
        return (
            fnmatchcase(server, self.server)
            and fnmatchcase(raw_tool, self.tool)
            and all(c.matches(arguments) for c in self.clauses)
        )

    def sort_key(self) -> tuple[int, int, int]:
        return (self.rank, 0 if self.effect == RULE_ASK else 1, self.id)


def parse_clauses(raw: str | None) -> tuple[ArgClause, ...]:
    """Clauses from the stored JSON.

    A malformed clause turns the whole rule into one that never matches: a bad
    row must not widen what its rule allows.
    """
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    out: list[ArgClause] = []
    for item in data:
        if (
            isinstance(item, dict)
            and isinstance(item.get("arg"), str)
            and item.get("op") in ARG_OPS
            and isinstance(item.get("value"), str)
        ):
            out.append(ArgClause(item["arg"], item["op"], item["value"]))
        else:
            return (ArgClause("", "never", ""),)
    return tuple(out)


def compile_rule(row: ApprovalRule) -> CompiledRule:
    return CompiledRule(
        id=row.id,
        effect=row.effect,
        server=row.server,
        tool=row.tool,
        clauses=parse_clauses(row.arg_match),
        note=row.note,
        rank=1 if row.scope == RULE_SCOPE_GLOBAL else 0,
    )


def parse_arguments(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_match(
    rules: Iterable[CompiledRule], server: str, raw_tool: str, arguments: str
) -> CompiledRule | None:
    """The rule that decides this call, or ``None`` to fall back to the policy."""
    parsed = parse_arguments(arguments)
    for rule in sorted(rules, key=CompiledRule.sort_key):
        if rule.matches(server, raw_tool, parsed):
            return rule
    return None


def clauses_json(clauses: Iterable[ArgClause]) -> str:
    return json.dumps([{"arg": c.arg, "op": c.op, "value": c.value} for c in clauses])


# -- Persistence -----------------------------------------------------------


async def load_for_conversation(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> tuple[CompiledRule, ...]:
    """The enabled rules that apply to a conversation: its own plus the global ones."""
    owner = ApprovalRule.topic_id if kind == "topic" else ApprovalRule.chat_id
    rows = await session.execute(
        select(ApprovalRule).where(
            ApprovalRule.enabled.is_(True),
            or_(ApprovalRule.scope == RULE_SCOPE_GLOBAL, owner == container_id),
        )
    )
    return tuple(compile_rule(r) for r in rows.scalars().all())


async def create_rule(
    session: AsyncSession,
    *,
    effect: str,
    server: str,
    tool: str,
    clauses: Iterable[ArgClause] = (),
    scope: str = RULE_SCOPE_GLOBAL,
    topic_id: int | None = None,
    chat_id: int | None = None,
    note: str | None = None,
    created_from_approval_id: int | None = None,
) -> ApprovalRule:
    """Add a rule, or return the identical one that already exists."""
    clause_text = clauses_json(clauses)
    existing = await session.execute(
        select(ApprovalRule).where(
            ApprovalRule.effect == effect,
            ApprovalRule.server == server,
            ApprovalRule.tool == tool,
            ApprovalRule.arg_match == clause_text,
            ApprovalRule.scope == scope,
            ApprovalRule.topic_id.is_(None)
            if topic_id is None
            else ApprovalRule.topic_id == topic_id,
            ApprovalRule.chat_id.is_(None) if chat_id is None else ApprovalRule.chat_id == chat_id,
        )
    )
    row = existing.scalars().first()
    if row is not None:
        if not row.enabled:
            row.enabled = True
            await session.commit()
        return row
    row = ApprovalRule(
        effect=effect,
        server=server,
        tool=tool,
        arg_match=clause_text,
        scope=scope,
        topic_id=topic_id,
        chat_id=chat_id,
        enabled=True,
        note=(note or "").strip() or None,
        hit_count=0,
        created_from_approval_id=created_from_approval_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def touch_rule(rule_id: int) -> None:
    """Count a rule hit. Uses its own session: the turn's may be mid-stream."""
    from precursor.backend.db import SessionLocal

    async with SessionLocal() as ws:
        row = await ws.get(ApprovalRule, rule_id)
        if row is not None:
            row.hit_count = (row.hit_count or 0) + 1
            row.last_used_at = datetime.now(UTC)
            await ws.commit()
