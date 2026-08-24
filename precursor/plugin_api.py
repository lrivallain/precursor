"""The supported import surface for Precursor plugins.

Plugins should import from ``precursor.plugin_api`` rather than reaching into
``precursor.backend.*``: this module is the contract we keep stable, while the
internals behind it move freely between releases. It bundles the plugin
registry, the FastAPI dependencies a plugin needs to talk to the database and
settings, and the GitHub helpers core already maintains.

A plugin declares itself with an entry point::

    [project.entry-points."precursor.plugins"]
    my_plugin = "my_pkg.plugin:register"

and implements ``register(registry: PluginRegistry) -> None``.
"""

from __future__ import annotations

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal, get_session
from precursor.backend.models import Topic
from precursor.backend.plugins import FrontendExtension, PluginRegistry
from precursor.backend.schemas.issues import IssueComment, IssueDetail, IssueLabel
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import (
    GitHubClient,
    GitHubInsufficientScopeError,
    GitHubRepoNotAccessibleError,
)
from precursor.backend.services.github_context import (
    require_github_repo,
    require_github_token,
)

#: Bumped when a backwards-incompatible change lands in this module. Plugins can
#: assert against it to fail loudly rather than mysteriously.
PLUGIN_API_VERSION = 1

__all__ = [
    "PLUGIN_API_VERSION",
    "FrontendExtension",
    "GitHubClient",
    "GitHubInsufficientScopeError",
    "GitHubRepoNotAccessibleError",
    "IssueComment",
    "IssueDetail",
    "IssueLabel",
    "PluginRegistry",
    "SessionLocal",
    "Settings",
    "Topic",
    "get_session",
    "get_settings",
    "require_github_repo",
    "require_github_token",
    "resolve_github_token",
    "resolve_global_github_repo",
    "resolve_issue_associations_enabled",
]
