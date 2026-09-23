"""Guards for the modules split out of ``services.agents.manager`` (#334).

The text protocol, MCP scoping and SDK log filtering live in leaf modules so
text-only callers don't drag in the SDK-facing manager, and ``AgentManager``
delegates cohesive method groups to collaborator modules. These tests pin the
promises that split makes: old ``manager`` imports keep resolving, and none of
the split-out modules ever imports ``manager`` back.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

_LEAF_MODULES = (
    "precursor.backend.services.agents.directives",
    "precursor.backend.services.agents.mcp_scope",
    "precursor.backend.services.agents.sdk_logging",
    "precursor.backend.services.agents.live_session",
    # Collaborators: they reach the manager through a back-reference and only
    # import it for type checking.
    "precursor.backend.services.agents.artifacts",
    "precursor.backend.services.agents.commands",
    "precursor.backend.services.agents.mcp_config",
    "precursor.backend.services.agents.models",
    "precursor.backend.services.agents.permissions",
    "precursor.backend.services.agents.prompting",
    "precursor.backend.services.agents.timeline",
    "precursor.backend.services.agents.usage",
)

_REEXPORTS = {
    "directives": (
        "RESULT_SUMMARY_CAP",
        "parse_agent_command",
        "parse_agent_directives",
        "strip_control_directives",
    ),
    "mcp_scope": (
        "MCP_SCOPE_MAX_LEN",
        "normalize_mcp_scope",
        "parse_mcp_scope",
        "scope_includes_precursor",
    ),
    "live_session": ("_LiveSession",),
    "mcp_config": ("_OAUTH_FALLBACK_TTL", "_OAUTH_REFRESH_MARGIN"),
}


@pytest.mark.parametrize(
    ("leaf", "name"),
    [(leaf, name) for leaf, names in _REEXPORTS.items() for name in names],
)
def test_manager_reexports_the_moved_helpers(leaf: str, name: str) -> None:
    from precursor.backend.services.agents import manager

    module = importlib.import_module(f"precursor.backend.services.agents.{leaf}")
    assert getattr(manager, name) is getattr(module, name)
    assert name in manager.__all__


def test_manager_command_registry_is_the_commands_module_registry() -> None:
    from precursor.backend.services.agents import commands
    from precursor.backend.services.agents.manager import AgentManager

    assert AgentManager._COMMAND_HANDLERS is commands.COMMAND_HANDLERS


def test_leaf_modules_never_import_the_manager() -> None:
    # A fresh interpreter, because this one has almost certainly imported the
    # manager already — and that also catches a cycle closed transitively.
    code = (
        "import sys\n"
        + "".join(f"import {mod}\n" for mod in _LEAF_MODULES)
        + "assert 'precursor.backend.services.agents.manager' not in sys.modules, "
        "'a leaf module imported services.agents.manager'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
