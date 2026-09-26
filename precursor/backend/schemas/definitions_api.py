"""API payloads for the definitions folder: the integrity check and the export.

Separate from :mod:`precursor.backend.schemas.definitions`, which is the file
format itself. These describe what Precursor reports *about* a folder of such
files.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DefinitionKind = Literal["agent", "workflow"]


class DefinitionSource(BaseModel):
    """Where an agent's or workflow's declaration comes from, in files mode.

    ``file``: declared by ``path``. ``invalid``: ``path`` is its file but has
    errors, so it can't run. ``none``: no file carries its id, so it still runs
    from the database.
    """

    state: Literal["file", "invalid", "none"]
    path: str | None = None
    message: str | None = None
    # Permissions the file grants beyond what was accepted; while non-empty it
    # can't start a run (``POST /api/definitions/accept`` clears it).
    review: list[str] = []
    # SHA-256 of the file as reported, to accept exactly the reviewed version.
    content_hash: str | None = None


class DefinitionAcceptRequest(BaseModel):
    kind: DefinitionKind
    # The agent's or workflow's database id (an agent's public id works too).
    id: int | str
    # The ``content_hash`` of the version that was reviewed. When given and the
    # file changed since, the accept is refused rather than accepting it unseen.
    content_hash: str | None = None


class DefinitionIssue(BaseModel):
    """One problem found in the folder.

    ``error`` means Precursor can't use the file (or the workflow can't run);
    ``warning`` flags something that works but probably isn't what was meant,
    such as a role that doesn't exist on this instance.
    """

    severity: Literal["error", "warning"]
    # Relative to the definitions folder; null for folder-wide findings.
    path: str | None = None
    # Where in the file, e.g. ``steps[brief].context.from``.
    location: str | None = None
    message: str


class DefinitionFileSummary(BaseModel):
    path: str
    kind: DefinitionKind
    # Taken from the file even when it fails validation, as long as it parses.
    id: str | None = None
    # The agent's ``title`` or the workflow's ``name``, when the file is valid.
    name: str | None = None
    valid: bool
    # SHA-256 of the file's bytes: what a run will record as "the version used".
    content_hash: str
    # Whether an agent or workflow in the database carries this id. Null when
    # the check ran without a database (the ``precursor validate`` CLI).
    linked: bool | None = None


class UnlinkedAgent(BaseModel):
    id: int
    public_id: str
    title: str
    archived: bool = False


class UnlinkedWorkflow(BaseModel):
    id: int
    name: str
    archived: bool = False


class DefinitionsDatabaseLinks(BaseModel):
    """How the database lines up with the folder, row by row.

    Step-private (inline) agents never get a file of their own, so they are
    left out: their prompt lives in the workflow step.
    """

    linked_agents: int = 0
    linked_workflows: int = 0
    unlinked_agents: list[UnlinkedAgent] = []
    unlinked_workflows: list[UnlinkedWorkflow] = []


class DefinitionsCheckReport(BaseModel):
    root: str
    exists: bool
    ok: bool
    error_count: int = 0
    warning_count: int = 0
    files: list[DefinitionFileSummary] = []
    issues: list[DefinitionIssue] = []
    database: DefinitionsDatabaseLinks | None = None


class ExportedDefinition(BaseModel):
    kind: DefinitionKind
    path: str
    id: str
    name: str
    # The database row the file was written from.
    source_id: int


class DefinitionsExportResult(BaseModel):
    root: str
    written: list[ExportedDefinition] = []
    # Rows that already have a file, left alone (``overwrite`` rewrites them).
    skipped: list[ExportedDefinition] = []
    # Warnings about settings that were normalised or could not be carried,
    # and errors for files written as-is that still need fixing by hand.
    issues: list[DefinitionIssue] = []


class DefinitionFileReport(BaseModel):
    """What the check says about one file opened in the Files section."""

    # False when the file isn't a definition file inside the definitions folder.
    in_definitions: bool
    # Relative to the definitions folder, as the check reports it.
    path: str | None = None
    kind: DefinitionKind | None = None
    valid: bool | None = None
    issues: list[DefinitionIssue] = []


# --- Migration (database → files, and back) --------------------------------


class DefinitionsWorkspaceRef(BaseModel):
    """The workspace holding the definitions folder, for a link to Files."""

    id: int
    slug: str
    name: str


class MigrationItem(BaseModel):
    """What the migration does with one agent or workflow.

    ``create``: no file yet, one is written. ``regenerate``: its file differs
    from the database (or has errors) and is rewritten from it — the database
    is the source until the switch. ``unchanged``: its file already says the
    same, and is left exactly as it is (comments included). ``new_from_disk``:
    a file no agent or workflow here carries; it's added after the switch, and
    its permissions wait for review.
    """

    kind: DefinitionKind
    # The database row; null for ``new_from_disk``.
    id: int | None = None
    name: str
    path: str | None = None
    action: Literal["create", "regenerate", "unchanged", "new_from_disk"]
    reason: str | None = None


class MigrationPreview(BaseModel):
    source: Literal["database", "files"]
    # PRECURSOR_DEFINITIONS_SOURCE=files: files mode is forced, so it can't be
    # switched back from the app.
    forced: bool = False
    folder: str
    workspace: DefinitionsWorkspaceRef | None = None
    # A definition file in the folder, for an "Open in Files" link.
    sample_path: str | None = None
    items: list[MigrationItem] = []
    issues: list[DefinitionIssue] = []
    # Why it can't run now (a workflow mid-run, two files sharing an id…).
    blockers: list[str] = []
    ready: bool = False
    # Files would be rewritten, or some definition has errors: the migration
    # needs ``acknowledge`` to go ahead.
    needs_confirmation: bool = False


class MigrationRequest(BaseModel):
    acknowledge: bool = False


class MigrationResult(BaseModel):
    ok: bool
    source: Literal["database", "files"]
    # A copy of the database taken first (SQLite), to go back to if needed.
    snapshot: str | None = None
    created: int = 0
    regenerated: int = 0
    unchanged: int = 0
    added_from_disk: int = 0
    issues: list[DefinitionIssue] = []
    # Filled when verification failed: the switch was not made.
    mismatches: list[str] = []


class RevertResult(BaseModel):
    ok: bool
    source: Literal["database", "files"]
    snapshot: str | None = None
    agents: int = 0
    workflows: int = 0
    # Declarations that couldn't be copied back (file missing or broken); the
    # database keeps its older values for those.
    skipped: list[str] = []
