"""Shell commands stay under the run's approval policy.

Since runtime 1.0.106 the Copilot runtime runs commands it judges read-only
(``date``, ``whoami``) without asking the host, so ``manual`` and ``balanced``
would silently not gate them. A ``preToolUse`` hook answering ``ask`` sends each
shell call back to the permission handler as a ``PermissionRequestHook``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from precursor.backend.services.agents.permissions import (
    PermissionBroker,
    describe_permission,
    permission_signature,
    shell_hook_decision,
    should_auto_approve,
)


@pytest.mark.parametrize("policy", ["manual", "balanced"])
@pytest.mark.parametrize("tool", ["bash", "powershell"])
def test_gated_policies_ask_before_every_shell_command(tool: str, policy: str) -> None:
    decision = shell_hook_decision(tool, policy)
    assert decision is not None
    assert decision["permissionDecision"] == "ask"


def test_autonomous_leaves_shell_to_the_runtime() -> None:
    assert shell_hook_decision("bash", "autonomous") is None


@pytest.mark.parametrize("tool", ["view", "edit", "precursor-list_topics", None])
def test_other_tools_keep_their_own_permission_requests(tool: str | None) -> None:
    """Reads and MCP calls still arrive typed, so ``balanced`` can auto-approve them."""
    assert shell_hook_decision(tool, "manual") is None


class PermissionRequestHook:
    """Same name and fields as the SDK request the hook produces."""

    def __init__(self, tool_name: str, tool_args: object) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.hook_message = "Precursor asks before every shell command"


class PermissionRequestShell:
    def __init__(self, command: str) -> None:
        self.full_command_text = command
        self.intention = None
        self.warning = None


def test_a_hooked_shell_call_is_described_as_a_shell_request() -> None:
    request = PermissionRequestHook("bash", {"command": "whoami", "description": "Who am I"})

    info = describe_permission(request)

    assert info == {
        "type": "shell",
        "title": "Run a shell command",
        "command": "whoami",
        "intention": "Who am I",
    }
    assert not should_auto_approve(request)


def test_approve_for_session_matches_both_request_shapes() -> None:
    """A grant made on one shape must cover the same command in the other."""
    hooked = describe_permission(PermissionRequestHook("bash", {"command": "make test"}))
    native = describe_permission(PermissionRequestShell("make test"))

    assert permission_signature(hooked) == permission_signature(native)


def test_malformed_hook_args_still_describe_a_shell_request() -> None:
    info = describe_permission(PermissionRequestHook("bash", "not a dict"))
    assert info == {"type": "shell", "title": "Run a shell command"}


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("manual", "ask"), ("balanced", "ask"), ("autonomous", None)],
)
async def test_the_hook_reads_the_live_policy(policy: str, expected: str | None) -> None:
    manager = SimpleNamespace(_live={7: SimpleNamespace(approval_policy=policy)})
    hook = PermissionBroker(manager).make_pre_tool_hook(7)  # type: ignore[arg-type]

    decision = await hook({"toolName": "bash", "toolArgs": {"command": "date"}}, None)

    assert (decision or {}).get("permissionDecision") == expected


async def test_the_hook_falls_back_to_the_default_policy_without_a_live_session() -> None:
    hook = PermissionBroker(SimpleNamespace(_live={})).make_pre_tool_hook(7)  # type: ignore[arg-type]

    decision = await hook(SimpleNamespace(toolName="bash"), None)

    assert decision is not None and decision["permissionDecision"] == "ask"
