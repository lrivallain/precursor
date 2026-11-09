"""On-demand loading of MCP tool definitions.

Every enabled MCP server contributes its tool schemas to each model request.
With a few large servers that is ~100k tokens of JSON on every round — more
than the whole per-minute input quota of a small deployment, and the bulk of
the cost and latency of a short question.

Instead, a turn starts with a small set of tools in full, and the rest are only
*named* in a compact index in the system prompt. The model calls
``search_tools`` to load what it needs; the matches are offered with their full
schemas from the next round on. The initial set is:

* tools the system prompt itself tells the model to call;
* every tool of a server the user pinned with "Always load";
* tools the conversation called recently, so a follow-up question doesn't need
  another search;
* everything, when the whole catalogue is small enough that deferring would
  save nothing.

Routing is untouched: ``ActiveTools.tool_to_server`` keeps the full catalogue,
so a call to a tool the model named straight from the index still runs (and
loads it), and parked approvals resume as before.

The advertised order is stable — initial tools in catalogue order, later loads
appended — so prompt caching keeps the tool prefix across rounds.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from precursor.backend.services.llm.base import ChatMessage, ToolDef

SEARCH_TOOLS_NAME = "search_tools"
# Below this estimated schema size, deferring tools would save less than it
# costs the model in an extra search round.
DEFER_MIN_SCHEMA_TOKENS = 8_000
# Tools called in this many most recent distinct calls stay loaded.
RECENT_TOOL_LIMIT = 24
_SEARCH_DEFAULT_LIMIT = 8
_SEARCH_MAX_LIMIT = 20
_DESCRIPTION_PREVIEW_CHARS = 300
_CHARS_PER_TOKEN = 3
_WORD_RE = re.compile(r"[a-z0-9]+")

SEARCH_TOOLS_DEF = ToolDef(
    name=SEARCH_TOOLS_NAME,
    description=(
        "Load MCP tools that are listed under 'Tools available on demand' but not "
        "yet loaded. Pass exact tool names in `names` when you know them, or "
        "describe the task in `query` to find matching tools. Matching tools "
        "become callable on your next step. Load several related tools at once "
        "rather than one search per tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords describing the capability you need.",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact tool names to load, with or without the server prefix.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _SEARCH_MAX_LIMIT,
                "description": f"Maximum number of tools to load (default {_SEARCH_DEFAULT_LIMIT}).",
            },
        },
    },
)


@dataclass(frozen=True, slots=True)
class ToolLoadingPolicy:
    """The user's on-demand loading settings."""

    defer: bool = True
    always_load: frozenset[str] = frozenset()


def _schema_tokens(tool: Any) -> int:
    size = len(tool.qualified_name) + len(tool.description or "")
    size += len(json.dumps(tool.input_schema, default=str))
    return size // _CHARS_PER_TOKEN


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _recent_tool_names(history: Sequence[ChatMessage]) -> list[str]:
    names: list[str] = []
    for message in reversed(history):
        for call in reversed(message.tool_calls or []):
            name = (call.get("function") or {}).get("name")
            if name and name not in names:
                names.append(name)
                if len(names) >= RECENT_TOOL_LIMIT:
                    return names
    return names


def _mentioned(tool: Any, prompt: str) -> bool:
    """Whether the system prompt tells the model to call ``tool``."""
    if re.search(rf"\b{re.escape(tool.qualified_name)}\b", prompt):
        return True
    # Precursor's own prompts name its tools without the server prefix; other
    # servers' raw names are too generic ("search", "list") to match safely.
    return tool.server == "precursor" and bool(re.search(rf"\b{re.escape(tool.name)}\b", prompt))


@dataclass
class ToolLoadout:
    """Which MCP tools a turn advertises in full, growing as the model loads more."""

    active: Any
    deferred: bool
    loaded: list[str] = field(default_factory=list)

    @property
    def catalog(self) -> list[Any]:
        # Read live: a mid-turn re-list (``ActiveTools._resync``) updates it.
        return list(self.active.tools)

    def is_loaded(self, qualified_name: str) -> bool:
        return not self.deferred or qualified_name in self.loaded

    def load(self, names: Iterable[str]) -> list[str]:
        """Mark ``names`` loaded; returns the ones that were newly added."""
        known = {t.qualified_name for t in self.catalog}
        added = [n for n in dict.fromkeys(names) if n in known and n not in self.loaded]
        self.loaded.extend(added)
        return added

    def provider_tools(self, exclude: Collection[str] = ()) -> list[ToolDef]:
        """MCP tools to advertise this round, as provider tool definitions."""
        by_name = {t.qualified_name: t for t in self.catalog}
        names = self.loaded if self.deferred else list(by_name)
        out = []
        for name in names:
            tool = by_name.get(name)
            if tool is not None and name not in exclude:
                out.append(
                    ToolDef(
                        name=tool.qualified_name,
                        description=tool.description,
                        parameters=tool.input_schema,
                    )
                )
        return out

    def meta_tools(self) -> list[ToolDef]:
        return [SEARCH_TOOLS_DEF] if self.deferred and self.unloaded_count() else []

    def unloaded_count(self) -> int:
        return sum(1 for t in self.catalog if t.qualified_name not in self.loaded)

    def index(self) -> str:
        """System-prompt block naming the tools that can be loaded on demand."""
        if not self.deferred:
            return ""
        by_server: dict[str, list[str]] = {}
        for tool in self.catalog:
            if tool.qualified_name not in self.loaded:
                by_server.setdefault(tool.server, []).append(tool.qualified_name)
        if not by_server:
            return ""
        lines = [
            "## Tools available on demand",
            "These MCP tools are installed but not loaded yet. Their names are listed "
            f"by server; call `{SEARCH_TOOLS_NAME}` with the exact names (or a "
            "keyword query) to load the ones you need before calling them. Never "
            "answer from memory when a listed tool could provide current data.",
        ]
        for server in sorted(by_server):
            names = by_server[server]
            lines.append(f"- {server} ({len(names)}): {', '.join(names)}")
        return "\n".join(lines)

    def search(self, arguments: str) -> str:
        """Run ``search_tools``: load the matches and describe them."""
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        limit = args.get("limit")
        if not isinstance(limit, int) or limit < 1:
            limit = _SEARCH_DEFAULT_LIMIT
        limit = min(limit, _SEARCH_MAX_LIMIT)
        catalog = self.catalog

        picked: list[Any] = []
        requested = args.get("names")
        if isinstance(requested, list):
            wanted = {str(n).strip().lower() for n in requested if str(n).strip()}
            picked = [
                t for t in catalog if t.qualified_name.lower() in wanted or t.name.lower() in wanted
            ]

        query = args.get("query")
        if isinstance(query, str) and query.strip() and len(picked) < limit:
            terms = set(_words(query))
            scored: list[tuple[int, int, Any]] = []
            for position, tool in enumerate(catalog):
                if tool in picked:
                    continue
                name_words = set(_words(tool.qualified_name))
                desc_words = set(_words(tool.description or ""))
                score = sum(
                    (3 if term in name_words else 0)
                    + (2 if term == tool.server.lower() else 0)
                    + (1 if term in desc_words else 0)
                    for term in terms
                )
                if score:
                    scored.append((-score, position, tool))
            scored.sort(key=lambda item: (item[0], item[1]))
            picked += [tool for _, _, tool in scored[: limit - len(picked)]]

        picked = picked[:limit]
        if not picked:
            return (
                "No matching tools. Check the names under 'Tools available on demand' "
                "and try exact names or other keywords."
            )
        self.load(t.qualified_name for t in picked)
        lines = [f"Loaded {len(picked)} tool(s); they are callable now:"]
        for tool in picked:
            description = " ".join((tool.description or "").split())
            if len(description) > _DESCRIPTION_PREVIEW_CHARS:
                description = description[: _DESCRIPTION_PREVIEW_CHARS - 1] + "…"
            lines.append(f"- {tool.qualified_name}: {description}")
        return "\n".join(lines)


def build_loadout(
    active: Any,
    *,
    policy: ToolLoadingPolicy | None,
    history: Sequence[ChatMessage],
    system_prompt: str,
) -> ToolLoadout:
    """The tools a turn starts with, per ``policy``."""
    catalog = list(active.tools)
    if (
        policy is None
        or not policy.defer
        or sum(_schema_tokens(t) for t in catalog) <= DEFER_MIN_SCHEMA_TOKENS
    ):
        return ToolLoadout(active=active, deferred=False)
    recent = set(_recent_tool_names(history))
    initial = [
        t.qualified_name
        for t in catalog
        if t.server in policy.always_load
        or t.qualified_name in recent
        or _mentioned(t, system_prompt)
    ]
    return ToolLoadout(active=active, deferred=True, loaded=initial)


def estimate_advertised_tool_tokens(loadout: ToolLoadout) -> int:
    """Rough token cost of the tool definitions and index a turn starts with."""
    tools = loadout.provider_tools() + loadout.meta_tools()
    size = sum(
        len(t.name) + len(t.description) + len(json.dumps(t.parameters, default=str)) for t in tools
    )
    return (size + len(loadout.index())) // _CHARS_PER_TOKEN
