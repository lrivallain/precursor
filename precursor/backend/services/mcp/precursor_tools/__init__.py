"""Tool groups of the built-in Precursor MCP server (see ``precursor_server``).

Importing a group registers its tools on the shared registrar (``_core.mcp``).
The package imports every group here, in a fixed order, because that order is
the order ``tools/list`` advertises the tools in. Add a new group to this list.
"""

# isort: skip_file
from precursor.backend.services.mcp.precursor_tools import (  # noqa: F401
    _core,
    conversations,
    search,
    knowledge,
    state,
    actions,
    schedules,
    workspaces,
    step_plans,
    basic,
)
