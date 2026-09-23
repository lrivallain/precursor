"""The env surface and the app-settings surface must not overlap.

``config.Settings`` sets ``env_prefix="PRECURSOR_"``, so *every* field declared on
it silently becomes a ``PRECURSOR_*`` environment variable. When a field also has
a Settings-panel control, that produces two ways to set one value — and the env
twin is invisible: it lands in no documentation and no ``.env.example``, so the
only way to discover it is to read ``config.py``.

That is how ``PRECURSOR_AGENTS_ENABLED`` came to exist, alongside 23 others. This
test pins the split so it can't creep back: anything the user can change in the
UI is owned by the DB, and ``Settings`` keeps only what must be known before the
database exists (host, port, database URL, data dir, ticker cadences…) plus a
short, justified allow-list.
"""

from __future__ import annotations

import re
from pathlib import Path

from precursor.backend.config import Settings
from precursor.backend.schemas.settings import SettingsPayload

_SETTINGS_PANEL = Path(__file__).resolve().parents[1] / "frontend/src/components/SettingsPanel.tsx"

# The two deliberate exceptions. Both describe *the machine or tenant the app runs
# in* rather than a preference, both are documented (reference/configuration.md,
# and `.env.example` for the tenant), and both are legitimately useful before
# anyone opens the UI — pinning an Entra tenant, or naming a browser channel that
# actually exists on this host. Everything else with a UI control is DB-owned.
_DELIBERATE_ENV_TWINS = {"playwright_browser", "workiq_tenant_id"}


def test_no_env_field_shadows_a_settings_panel_control() -> None:
    env_fields = set(Settings.model_fields)
    ui_fields = set(SettingsPayload.model_fields)

    overlap = sorted((env_fields & ui_fields) - _DELIBERATE_ENV_TWINS)
    assert not overlap, (
        "These are settable from the Settings panel *and* declared on "
        f"config.Settings, so env_prefix exposes an undocumented PRECURSOR_* twin "
        f"of each: {overlap}. Keep the factory default as a module constant next "
        "to its resolve_* helper instead of as a field on Settings."
    )


def test_the_deliberate_exceptions_still_exist() -> None:
    """Stop the allow-list outliving what it excuses.

    If one of these is ever moved to the DB (or renamed), the entry silently
    starts excusing nothing — and would quietly re-admit a name collision later.
    """
    stale = sorted(_DELIBERATE_ENV_TWINS - set(Settings.model_fields))
    assert not stale, f"allow-list entries no longer on config.Settings: {stale}"


def test_system_panel_copy_does_not_point_at_the_environment() -> None:
    """The panel's own blurb must describe the split the tests above enforce.

    It outlived the split once (#252), telling readers these values "default to
    the server's environment / .env" — including the ``PRECURSOR_CMD_RUNNER_*``
    twins that were removed precisely because they could switch the sandbox off.
    """
    source = _SETTINGS_PANEL.read_text(encoding="utf-8")
    start = source.index("function SystemTab(")
    end = source.index("\nfunction ", start + 1)
    system_tab = re.sub(r"\s+", " ", source[start:end])

    env_claim = re.compile(
        r"\b(defaults?|falls? back|comes?)\s+(to|from)\s+(the\s+)?(server'?s\s+)?"
        r"(environment|\.env)\b|overridden here|PRECURSOR_",
        re.IGNORECASE,
    )
    found = env_claim.search(system_tab)
    assert found is None, (
        f"Settings → System copy implies an env fallback ({found.group(0)!r}), but "
        "every field on that panel is DB-only."
    )
