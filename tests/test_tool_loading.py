"""On-demand MCP tool loading (``services/tool_loading.py``).

A turn advertises only a small set of tool definitions plus a names-only index;
the model loads the rest with ``search_tools``. What has to stay true:

* a small catalogue, or the switch turned off, sends everything as before;
* tools the system prompt names, pinned servers and recently used tools start
  loaded;
* ``search_tools`` loads matches, which are advertised from the next round in a
  stable order;
* a tool named straight from the index still runs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from precursor.backend.services.llm.base import (
    ChatMessage,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
)
from precursor.backend.services.mcp.client import ActiveTools, MCPClientManager, MCPToolDef
from precursor.backend.services.tool_loading import (
    SEARCH_TOOLS_NAME,
    ToolLoadingPolicy,
    build_loadout,
    estimate_advertised_tool_tokens,
)
from precursor.backend.services.turn_engine import ToolResultTurn, run_tool_loop

# Enough schema text per tool that a few dozen tools exceed the deferral floor.
_BULK = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(40)}}


def _tool(server: str, name: str, description: str = "") -> MCPToolDef:
    return MCPToolDef(
        server=server, name=name, description=description or f"{name} tool", input_schema=_BULK
    )


def _catalog() -> list[MCPToolDef]:
    tools = [_tool("github", f"tool_{i}") for i in range(30)]
    tools += [
        _tool("github", "create_issue", "Create a GitHub issue in a repository"),
        _tool("github", "list_pull_requests", "List pull requests of a repository"),
        _tool("azure", "list_vms", "List Azure virtual machines"),
        _tool("precursor", "list_step_plans", "List step plans"),
        _tool("precursor", "get_step_plan", "Read a step plan"),
    ]
    return tools


def _active(tools: list[MCPToolDef]) -> Any:
    return SimpleNamespace(tools=tools)


def _loadout(
    tools: list[MCPToolDef] | None = None,
    *,
    policy: ToolLoadingPolicy | None = None,
    history: list[ChatMessage] | None = None,
    prompt: str = "",
):  # type: ignore[no-untyped-def]
    return build_loadout(
        _active(tools if tools is not None else _catalog()),
        policy=policy if policy is not None else ToolLoadingPolicy(),
        history=history or [],
        system_prompt=prompt,
    )


def test_small_catalogue_is_sent_in_full() -> None:
    tools = [_tool("fetch", "fetch")]
    loadout = build_loadout(
        _active(tools), policy=ToolLoadingPolicy(), history=[], system_prompt=""
    )
    assert not loadout.deferred
    assert [t.name for t in loadout.provider_tools()] == ["fetch__fetch"]
    assert loadout.meta_tools() == []
    assert loadout.index() == ""


def test_switch_off_or_no_policy_sends_everything() -> None:
    for policy in (None, ToolLoadingPolicy(defer=False)):
        loadout = build_loadout(_active(_catalog()), policy=policy, history=[], system_prompt="")
        assert not loadout.deferred
        assert len(loadout.provider_tools()) == len(_catalog())


def test_initial_set_prompt_mentions_pins_and_recent_calls() -> None:
    history = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c", "function": {"name": "azure__list_vms", "arguments": "{}"}}],
        )
    ]
    loadout = _loadout(
        policy=ToolLoadingPolicy(always_load=frozenset({"github"})),
        history=history,
        prompt="Find the plan with list_step_plans(topic_id=1).",
    )
    assert loadout.deferred
    names = {t.name for t in loadout.provider_tools()}
    assert "precursor__list_step_plans" in names  # named by the prompt
    assert "precursor__get_step_plan" not in names
    assert "azure__list_vms" in names  # called recently
    assert "github__create_issue" in names  # pinned server
    assert [t.name for t in loadout.meta_tools()] == [SEARCH_TOOLS_NAME]
    index = loadout.index()
    assert "precursor__get_step_plan" in index
    assert "github__create_issue" not in index


def test_generic_raw_names_of_other_servers_do_not_match_the_prompt() -> None:
    loadout = _loadout(prompt="Use tool_1 if needed; create_issue is great.")
    assert loadout.provider_tools() == []


def test_search_by_names_and_query_loads_matches_in_order() -> None:
    loadout = _loadout()
    assert loadout.provider_tools() == []
    found = loadout.search(json.dumps({"names": ["create_issue"]}))
    assert "github__create_issue" in found
    found = loadout.search(json.dumps({"query": "azure virtual machines"}))
    assert found.splitlines()[1].startswith("- azure__list_vms")
    assert [t.name for t in loadout.provider_tools()][:2] == [
        "github__create_issue",
        "azure__list_vms",
    ]
    assert "github__create_issue" not in loadout.index()


def test_search_without_matches_says_so_and_loads_nothing() -> None:
    loadout = _loadout()
    assert "No matching tools" in loadout.search(json.dumps({"query": "kubernetes"}))
    assert "No matching tools" in loadout.search("not json")
    assert loadout.loaded == []


def test_estimate_counts_only_what_a_turn_starts_with() -> None:
    full = build_loadout(_active(_catalog()), policy=None, history=[], system_prompt="")
    deferred = _loadout()
    assert estimate_advertised_tool_tokens(deferred) < estimate_advertised_tool_tokens(full) / 5


class _Bundle(ActiveTools):
    def __init__(self, tools: list[MCPToolDef]) -> None:
        super().__init__(manager=MCPClientManager())
        for tool in tools:
            self.tools.append(tool)
            self.tool_to_server[tool.qualified_name] = (tool.server, tool.name)
        self.ran: list[str] = []

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        self.ran.append(f"{server}__{raw_name}")
        return {"content": [{"type": "text", "text": "ok"}]}


class _Scripted:
    """Searches, then calls the loaded tool, then answers."""

    name = "fake"

    def __init__(self, script: list[ToolCallRequest | None]) -> None:
        self.script = script
        self.advertised: list[list[str]] = []
        self.systems: list[str] = []

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
        _ = model, reasoning_effort
        self.advertised.append([t.name for t in tools])
        self.systems.append(messages[0].content)
        step = self.script[len(self.advertised) - 1]
        if step is None:
            yield TextDeltaEvent(content="done")
        else:
            yield ToolCallsEvent(calls=[step])
        yield TurnDoneEvent(finish_reason="stop")


async def _drive(
    bundle: _Bundle, provider: _Scripted, policy: ToolLoadingPolicy | None
) -> list[Any]:
    return [
        ev
        async for ev in run_tool_loop(
            active=bundle,
            provider=provider,
            model="m",
            reasoning_effort="",
            system_prompt="sys",
            history=[],
            max_tool_rounds=5,
            max_input_tokens=100_000,
            max_tool_result_tokens=10_000,
            tool_policy=policy,
        )
    ]


async def test_tool_loop_loads_searched_tools_for_the_next_round() -> None:
    bundle = _Bundle(_catalog())
    provider = _Scripted(
        [
            ToolCallRequest(
                id="s", name=SEARCH_TOOLS_NAME, arguments=json.dumps({"names": ["create_issue"]})
            ),
            ToolCallRequest(id="c", name="github__create_issue", arguments="{}"),
            None,
        ]
    )
    events = await _drive(bundle, provider, ToolLoadingPolicy())
    assert provider.advertised[0] == [SEARCH_TOOLS_NAME]
    assert provider.advertised[1] == ["github__create_issue", SEARCH_TOOLS_NAME]
    assert provider.advertised[2] == provider.advertised[1]
    assert "## Tools available on demand" in provider.systems[0]
    assert bundle.ran == ["github__create_issue"]
    results = [ev for ev in events if isinstance(ev, ToolResultTurn)]
    assert "Loaded 1 tool(s)" in results[0].result_text


async def test_tool_named_from_the_index_runs_and_stays_loaded() -> None:
    bundle = _Bundle(_catalog())
    provider = _Scripted([ToolCallRequest(id="c", name="azure__list_vms", arguments="{}"), None])
    await _drive(bundle, provider, ToolLoadingPolicy())
    assert bundle.ran == ["azure__list_vms"]
    assert provider.advertised[1] == ["azure__list_vms", SEARCH_TOOLS_NAME]


async def test_tool_loop_without_policy_advertises_everything() -> None:
    bundle = _Bundle(_catalog())
    provider = _Scripted([None])
    await _drive(bundle, provider, None)
    assert len(provider.advertised[0]) == len(_catalog())
    assert SEARCH_TOOLS_NAME not in provider.advertised[0]
    assert "Tools available on demand" not in provider.systems[0]


def test_trimming_keeps_a_trailing_system_note_after_the_history() -> None:
    from precursor.backend.services.context_budget import trim_messages

    messages = [
        ChatMessage(role="system", content="prompt"),
        ChatMessage(role="user", content="old " * 400),
        ChatMessage(role="assistant", content="answer"),
        ChatMessage(role="system", content="references"),
        ChatMessage(role="user", content="new question"),
    ]
    trimmed = trim_messages(messages, max_input_tokens=60, per_message_max_tokens=1_000)
    assert [m.content for m in trimmed] == ["prompt", "answer", "references", "new question"]
