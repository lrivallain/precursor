"""Application settings sourced from environment variables / .env."""

from __future__ import annotations

import os
import sys
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_source_checkout() -> bool:
    """True when the package is imported from a working copy of the repo.

    A checkout keeps its state *beside the code* (``./precursor.db``,
    ``./.precursor``) so every worktree is an isolated sandbox. An installed
    wheel has no such home — and is typically started by a launcher whose
    working directory is ``/`` — so it must fall back to a per-user directory
    instead of scattering databases wherever it happened to be launched from.
    """
    root = Path(__file__).resolve().parents[2]
    return (root / "pyproject.toml").is_file() and (root / "frontend" / "package.json").is_file()


def user_data_dir() -> Path:
    """The per-user state directory for an installed Precursor."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Precursor"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "Precursor"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "precursor"


def _default_data_dir() -> str:
    return ".precursor" if is_source_checkout() else str(user_data_dir())


def _default_database_url() -> str:
    if is_source_checkout():
        return "sqlite+aiosqlite:///./precursor.db"
    # as_posix() keeps the URL valid on Windows, where a native path would
    # inject backslashes into the SQLAlchemy URL.
    return f"sqlite+aiosqlite:///{(user_data_dir() / 'precursor.db').as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRECURSOR_",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"
    # The app writes `<logs_dir>/precursor.log` itself, so the file is the same
    # one whether the process was started in a terminal, by the supervisor, or
    # by a launchd/systemd login item. It is size-rotated because nothing else
    # will ever prune it — a service manager's own stdio capture is unbounded,
    # which is how those files reach tens of megabytes.
    log_file_max_bytes: int = 5 * 1024 * 1024
    log_file_backups: int = 3
    # Seconds uvicorn waits for in-flight requests (e.g. long-lived SSE chat
    # streams) to finish on shutdown before force-closing them. Kept small so
    # Ctrl-C releases the listening port promptly instead of hanging on an open
    # stream and leaving the port unusable for a TIME_WAIT window.
    shutdown_grace_seconds: int = 3
    # Comma-separated string in env (pydantic-settings JSON-decodes list fields
    # too eagerly to accept a bare value). Parsed via `cors_origins` below.
    cors_origins_raw: str = Field(default="", validation_alias="PRECURSOR_CORS_ORIGINS")

    @cached_property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    # Database. In a source checkout this stays repo-relative (one DB per
    # worktree); an installed wheel resolves to the per-user data directory so a
    # launcher-started process finds the same database every time.
    database_url: str = Field(default_factory=_default_database_url)

    # On-disk data directory for working copies (e.g. Workspace git
    # clones), the blob store, logs and the supervisor's runtime state.
    # Relative paths resolve against the process working directory.
    data_dir: str = Field(default_factory=_default_data_dir)

    @cached_property
    def workspaces_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "workspaces")

    # Attachment bytes live on disk as content-addressed files here (keyed by
    # SHA-256, sharded two levels deep) rather than as BLOBs in the DB, so the
    # database file stays small and cheap to back up / copy.
    @cached_property
    def blobs_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "blobs")

    # draw.io diagram editing is served from a self-hosted copy of the
    # diagrams.net webapp so ``.drawio`` files never leave the machine. The
    # release archive is ~53 MB (~150 MB extracted), so it is fetched on demand
    # into the data dir instead of being bundled in the wheel.
    drawio_version: str = "v31.3.1"
    drawio_download_url: str = (
        "https://github.com/jgraph/drawio/releases/download/{version}/draw.war"
    )

    @cached_property
    def drawio_dir(self) -> str:
        return str(Path(self.data_dir).resolve() / "drawio")

    # Skills live as ``<copilot_home>/skills/<name>/SKILL.md`` files shared with
    # the GitHub Copilot CLI and other tools. An explicit override (handy for
    # tests / non-standard setups) wins; otherwise we resolve the Copilot home
    # the same way the CLI does: COPILOT_HOME → XDG_CONFIG_HOME/copilot → ~/.copilot.
    skills_dir_override: str = Field(default="", validation_alias="PRECURSOR_SKILLS_DIR")

    @cached_property
    def skills_dir(self) -> str:
        if self.skills_dir_override.strip():
            return str(Path(self.skills_dir_override).expanduser().resolve())
        copilot_home = os.environ.get("COPILOT_HOME", "").strip()
        if copilot_home:
            base = Path(copilot_home)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
            base = Path(xdg) / "copilot" if xdg else Path.home() / ".copilot"
        return str((base / "skills").expanduser())

    # LLM — the active provider and its credentials live in the app settings
    # (Settings → Model), not in the environment, so they can be changed at
    # runtime without a restart. See services/llm/registry.py. The prompt budget
    # (max input / tool-result tokens) lives there too — see
    # ``services/app_settings.py``.

    # Scheduler — drives recurring "scheduled" topics. Single in-process ticker
    # + a small worker pool; see services/scheduler.py.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: int = 30
    scheduler_concurrency: int = 2
    # One-shot reminders (services/reminder_ticker.py) — how often to poll for
    # due reminders. Gated by the same ``scheduler_enabled`` flag.
    reminder_poll_seconds: int = 30

    # Tool-result retention (services/tool_result_retention.py) — how often the
    # sweep that trims full TOOL-result content runs. Gated by
    # ``scheduler_enabled``; the retention *window* is a Settings → System value.
    tool_result_retention_poll_seconds: int = 86_400

    # Live transcript retention (services/live_transcript_retention.py) — how
    # often the sweep that deletes old Live transcript segments runs. Gated by
    # ``scheduler_enabled``; the retention *window* is a Settings → System value.
    live_transcript_retention_poll_seconds: int = 86_400

    # Agent-event retention (services/agent_event_retention.py) — how often the
    # sweep that prunes the archived agent timeline runs. Gated by
    # ``scheduler_enabled``; the retention *levers* are Settings → System values.
    agent_event_retention_poll_seconds: int = 86_400

    # WorkIQ token keep-alive (services/mcp/workiq_keepalive.py) — a background
    # ticker that silently refreshes the WorkIQ preview OAuth token before it
    # expires, so the hosted session survives without frequent interactive
    # re-sign-in. Only does work while preview is on and tokens exist.
    workiq_keepalive_enabled: bool = True
    workiq_keepalive_poll_seconds: int = 60
    # There is deliberately no refresh-margin setting: how far ahead of expiry a
    # token is renewed follows from its own lifetime (see
    # ``workiq_preview.renewal_lead_seconds``), which is the only thing that
    # decides how many retries fit before the cliff.
    # Stop keeping a credential warm once it has gone this long without a tool
    # call. Refreshing a server nobody uses is wasted work, and — worse — when
    # its refresh token eventually lapses the keep-alive raises a sign-in prompt
    # for tools the user never asked for. The idle clock is seeded at process
    # start, so a freshly started app still keeps everything warm for one window
    # rather than going cold until the first call. Set to 0 to always keep warm.
    workiq_keepalive_idle_after_seconds: int = 21_600
    # Even for an *idle* credential, surface a genuine lapse proactively: once its
    # stored access token has actually expired, the keep-alive probes it once and,
    # if it now needs an interactive sign-in, raises the re-authenticate banner
    # (and flags the turn path to fast-fail the doomed connect). This trades a
    # little of the anti-nag silence above for not discovering a dead session as a
    # slow, silent stall on the next request. Set False to keep idle credentials
    # completely silent until the user touches them.
    workiq_keepalive_surface_idle_lapse: bool = True

    # WorkIQ interactive re-auth UX (services/mcp/workiq_preview.py). We always
    # pre-fill the Entra account picker with the last signed-in user
    # (``login_hint``) so re-auth skips account selection. When this is on we
    # additionally attempt a non-interactive ``prompt=none`` authorization first:
    # it completes with zero clicks if the browser still holds a live Entra SSO
    # session, and only falls back to the visible prompt when Entra reports
    # interaction is required. Turn off to always show the interactive prompt.
    workiq_silent_reauth_enabled: bool = True

    # WorkIQ hands-free auto re-auth (services/mcp/workiq_preview.py +
    # frontend McpAuthBanner). When a background tick / turn parks WorkIQ in
    # needs_auth, the SPA first attempts the silent ``prompt=none`` pass in an
    # invisible iframe with no clicks — if the browser still holds a live Entra
    # SSO session the banner never appears. Only when a silent pass can't
    # complete (interaction genuinely required, or framing/cookies block it) does
    # the visible "Sign in" banner take over. Turn off to always require the
    # manual click.
    workiq_auto_reauth_enabled: bool = True

    # WorkIQ loopback redirect port fallback (services/mcp/workiq_preview.py).
    # Every WorkIQ credential redirects to a *fixed* loopback port, so two of them
    # signing in at once — or any unrelated process squatting the port — used to
    # fail the sign-in outright. Entra ignores the port of a public client's
    # loopback redirect (only host and path must match the registration), so when
    # the preferred port is taken we can listen on an ephemeral one instead. Turn
    # off to keep the strict "port busy" failure.
    workiq_loopback_port_fallback: bool = True

    # WorkIQ cross-credential renewal (routers/mcp.py). Precursor's WorkIQ servers
    # authenticate as two different Entra clients (the preview app and the Agent
    # 365 one), so each needs its own sign-in. Right after any one of them
    # succeeds the browser holds a hot Entra SSO cookie — the cheapest moment to
    # renew the others, where a ``prompt=none`` pass usually completes with zero
    # clicks. When on, a successful sign-in re-announces any sibling credential
    # still parked in ``needs_auth`` so the SPA spends that cookie immediately
    # instead of prompting again minutes later. Turn off to renew each credential
    # only when its own server is next used.
    workiq_chain_reauth_enabled: bool = True

    # WorkIQ auth tracing (services/mcp/auth_trace.py). Every decision taken
    # about a WorkIQ credential — keep-alive verdicts, silent refreshes and the
    # Entra error that refused them, each leg of a re-auth — reports to the
    # dedicated ``precursor.mcp.auth`` logger, whose level this sets
    # *independently* of the app-wide ``log_level``. It defaults to DEBUG because
    # a sign-in lapse is rare, only reproducible in the wild, and useless to
    # diagnose after the fact: the trace has to already be running when it
    # happens. The channel is quiet outside an auth episode, so this costs
    # nothing in normal use. Raise it to ``info`` for transitions only, or
    # ``warning`` to silence it; the in-memory ring buffer behind
    # ``GET /api/mcp/auth/diagnostics`` is unaffected either way.
    workiq_auth_log_level: str = "debug"

    # Microsoft Agent 365 tenant (services/mcp/agent365.py). The hosted
    # ``workiq-teams`` / ``workiq-user`` MCP endpoints embed a tenant GUID in
    # their URL — Entra rejects the ``common``/``organizations`` aliases there.
    # Leave blank to let Precursor discover it from the ``tid`` claim of the
    # WorkIQ preview token the user already signed in with; set it explicitly
    # (or from Settings) to pin a specific tenant.
    workiq_tenant_id: str = ""

    # Command runner (cmd-runner MCP) — runs bash/python/node either inside a
    # throwaway Docker "jail" (default) or, when the jail is disabled, directly
    # on the host with full local disk access. Its settings (jail, image, network,
    # limits) are Settings → System values; only the scratch dir below is derived
    # from ``data_dir``. See services/cmd_runner.py.

    # MCP client — the chat/tool loop keeps each enabled server's session warm
    # across turns instead of re-spawning/initialising it every message (that
    # cold start dominated time-to-first-token). A warm session is released
    # after this many seconds with no tool calls; set to 0 to disable pooling
    # and open a fresh session per turn.
    mcp_idle_ttl_seconds: int = 600
    # MCP warm-up (services/mcp/warmup.py) — a background task that connects the
    # enabled servers shortly after startup so the first prompt doesn't pay
    # connect + initialize + list_tools for all of them at once. It runs one
    # server at a time (several stdio servers spawn ``npx``, and a startup
    # thundering herd is exactly what this removes), never blocks startup or a
    # request, and never initiates an interactive sign-in.
    mcp_warmup_enabled: bool = True
    # Grace period before the first server is warmed, so startup work (schema
    # migration, plugin hydration, the SPA's own first requests) gets the machine
    # to itself.
    mcp_warmup_delay_seconds: float = 5.0
    # Pause between servers, keeping the sweep politely serialised.
    mcp_warmup_gap_seconds: float = 1.0
    # GitHub MCP (remote) advertises one tool group per toolset. Requesting
    # "all" floods the prompt with hundreds of tools, slowing the first token.
    # Comma-separated list sent as the ``X-MCP-Toolsets`` header; use "all" to
    # restore the full catalogue.
    github_mcp_toolsets: str = "context,repos,issues,pull_requests,users"

    @cached_property
    def cmd_runner_scratch_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "cmd-runner" / "scratch")

    # Browser channel for the built-in ``playwright`` MCP server (``--browser``).
    # One of ``msedge`` (default), ``chromium``, ``chrome``, ``firefox``,
    # ``webkit``. Defaults to Microsoft Edge because authenticated Entra scraping
    # relies on Edge's corporate SSO/WAM broker to establish the session (as in
    # the reference CSU cockpit scrapers). Set ``chromium`` on machines without
    # Edge installed.
    playwright_browser: str = Field(
        default="msedge", validation_alias="PRECURSOR_PLAYWRIGHT_BROWSER"
    )

    # Optional override for the built-in ``playwright`` MCP server's browser
    # profile directory (passed as ``--user-data-dir``). Empty (the default)
    # means *don't* pin a directory, so ``@playwright/mcp`` uses its own shared,
    # machine-wide persistent profile (e.g. ``~/Library/Caches/ms-playwright/
    # mcp-msedge-profile`` on macOS). That reuses any interactive Entra/SSO
    # sign-in already onboarded there — including via other Playwright-MCP tools
    # (the Copilot CLI, etc.) — instead of forcing a fresh sign-in into an
    # app-specific profile. Set a path only to pin an isolated profile.
    playwright_profile_dir: str = Field(
        default="", validation_alias="PRECURSOR_PLAYWRIGHT_PROFILE_DIR"
    )

    # Agents mode — long-running Copilot SDK agent sessions. There is no env-level
    # on/off: installing the optional ``agents`` extra (a ~150 MB payload carrying
    # the native Copilot CLI) *is* the opt-in, so the feature follows the
    # capability probe and the DB app setting (Settings → Agents) is the one
    # control on top of it. The SDK persists each session's state under
    # ``agents_home`` (its ``COPILOT_HOME``). The per-agent defaults it starts
    # from (model, approval policy, system prompt, watchdog timeout) and the
    # workflow step seeds are Settings values too — see
    # ``services/app_settings.py``.

    # --- Fleet orchestration governance -------------------------------------
    # Max agents the concurrency governor lets execute a turn at once. Extra
    # ready agents queue on a semaphore so a big fleet can't stampede the host
    # or the model rate limit. 0/negative disables the cap (unbounded).
    agents_max_concurrent: int = 3
    # Base backoff (seconds) for auto-retry of a ``failed`` agent. Grows
    # exponentially per attempt (base * 2**(retry_count-1)); the scheduler ticker
    # re-runs agents once ``next_retry_at`` is due.
    agents_retry_backoff_seconds: int = 60

    @cached_property
    def agents_home(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "agents" / "copilot-home")

    # Backup (services/backup.py) — periodic copy of the SQLite DB + blob store
    # into a plain folder the user picks (e.g. a OneDrive-synced directory).
    # The on/off, target dir and retention are Settings → Backup values so they
    # can be changed at runtime; only the ticker's cadence is process-level. It
    # polls on ``backup_poll_seconds`` and runs a backup once
    # ``backup_interval_seconds`` has elapsed since the last success.
    backup_interval_seconds: int = 86_400
    backup_poll_seconds: int = 3_600

    # GitHub CLI account pinning (services/github_auth.py). With several
    # accounts signed in via `gh auth login`, `gh auth token` returns whichever
    # one is *active* — which makes the effective token depend on unrelated
    # shell state. Naming the login here passes `--user`, so Precursor always
    # resolves the same account without a prior `gh auth switch`.
    github_cli_user: str = ""

    # Update checks (services/updates.py). "stable" tracks tagged releases;
    # "nightly" tracks the rolling prerelease built from main. Empty means
    # "match the running build" — a dev version follows nightly, a tagged one
    # follows stable.
    update_repo: str = "lrivallain/precursor"
    update_channel: str = ""
    # Extras carried across a self-update, so `precursor service update` doesn't
    # silently drop the kanban plugin (or Agents mode) the user installed with.
    update_extras: str = "kanban"
    update_check_ttl_seconds: int = 900
    # What the tray does when a *background* check finds a new build.
    # "prompt" raises an actionable notification with an "Update and restart"
    # button; "notify" is a plain toast; "off" leaves the menu to say it. A
    # dialog you didn't ask for is intrusive by nature, so it has to be
    # refusable without turning update checks off altogether.
    update_notify: str = "prompt"

    @cached_property
    def logs_dir(self) -> str:
        return str(Path(self.data_dir).resolve() / "logs")

    @cached_property
    def runtime_state_file(self) -> str:
        """Where the supervisor records the running instance (pid, port, url)."""
        return str(Path(self.data_dir).resolve() / "runtime.json")


@lru_cache
def get_settings() -> Settings:
    return Settings()
