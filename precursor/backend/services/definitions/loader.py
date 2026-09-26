"""Read a definitions folder into validated agent and workflow definitions.

Parsing is cached per file on its stat fingerprint, so re-checking a folder
only re-reads the files that changed. Loading never raises for a bad file: every
problem becomes a :class:`DefinitionIssue` on that file, because one typo must
not hide the rest of the folder.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from precursor.backend.schemas.definitions import (
    AGENT_FILE_SUFFIX,
    WORKFLOW_FILE_SUFFIX,
    AgentDefinition,
    WorkflowDefinition,
)
from precursor.backend.schemas.definitions_api import DefinitionIssue, DefinitionKind

MAX_DEFINITION_BYTES = 1_000_000

Definition = AgentDefinition | WorkflowDefinition


@dataclass(frozen=True)
class LoadedFile:
    path: str
    kind: DefinitionKind
    content_hash: str
    # The ``id`` value when the YAML parses to a mapping carrying a string id,
    # even if the rest fails validation — so a broken file still links to its row
    # and still counts in the duplicate-id check.
    raw_id: str | None
    definition: Definition | None
    issues: tuple[DefinitionIssue, ...] = ()

    @property
    def name(self) -> str | None:
        if isinstance(self.definition, AgentDefinition):
            return self.definition.title
        if isinstance(self.definition, WorkflowDefinition):
            return self.definition.name
        return None


@dataclass(frozen=True)
class DefinitionSet:
    root: Path
    exists: bool
    files: tuple[LoadedFile, ...] = ()
    by_path: dict[str, LoadedFile] = field(default_factory=dict)
    by_id: dict[str, tuple[LoadedFile, ...]] = field(default_factory=dict)

    def find(self, kind: DefinitionKind, ident: str | None) -> LoadedFile | None:
        """The one file carrying ``ident``, if it is of ``kind``.

        An id used by two files is ambiguous (the check reports it), so it
        resolves to nothing rather than to whichever file was read first.
        """
        if not ident:
            return None
        files = self.by_id.get(ident, ())
        if len(files) == 1 and files[0].kind == kind:
            return files[0]
        return None

    def agents(self) -> list[tuple[LoadedFile, AgentDefinition]]:
        return [(f, f.definition) for f in self.files if isinstance(f.definition, AgentDefinition)]

    def workflows(self) -> list[tuple[LoadedFile, WorkflowDefinition]]:
        return [
            (f, f.definition) for f in self.files if isinstance(f.definition, WorkflowDefinition)
        ]


def kind_for(name: str) -> DefinitionKind | None:
    if name.startswith("."):
        return None
    if name.endswith(AGENT_FILE_SUFFIX):
        return "agent"
    if name.endswith(WORKFLOW_FILE_SUFFIX):
        return "workflow"
    return None


# --- YAML -------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """``safe_load`` that refuses duplicate keys.

    PyYAML keeps the *last* of two equal keys without a word, so a file with two
    ``prompt:`` blocks would quietly lose the first. In a hand-edited file that
    is a bug to report, not a value to pick.
    """


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
        except TypeError:  # unhashable key; construct_mapping reports it
            continue
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _yaml_error(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
    if mark is not None:
        return f"not valid YAML (line {mark.line + 1}, column {mark.column + 1}): {problem}"
    return f"not valid YAML: {problem}"


# --- Validation errors ------------------------------------------------------


def _location(loc: tuple[int | str, ...], raw: dict[str, Any]) -> str | None:
    """``("steps", 1, "context", "from")`` → ``steps[brief].context.from``."""
    parts: list[str] = []
    node: Any = raw
    for item in loc:
        if isinstance(item, int):
            label: str = str(item)
            if isinstance(node, list) and 0 <= item < len(node):
                node = node[item]
                key = node.get("key") if isinstance(node, dict) else None
                if isinstance(key, str) and key:
                    label = key
            else:
                node = None
            if parts:
                parts[-1] += f"[{label}]"
            else:
                parts.append(f"[{label}]")
            continue
        parts.append(item)
        node = node.get(item) if isinstance(node, dict) else None
    return ".".join(parts) or None


def _clean_message(msg: str) -> str:
    return msg.removeprefix("Value error, ")


def _validation_issues(
    path: str, exc: ValidationError, raw: dict[str, Any]
) -> list[DefinitionIssue]:
    return [
        DefinitionIssue(
            severity="error",
            path=path,
            location=_location(tuple(err["loc"]), raw),
            message=_clean_message(err["msg"]),
        )
        for err in exc.errors()
    ]


# --- Parsing ----------------------------------------------------------------


def parse_definition(path: str, kind: DefinitionKind, data: bytes) -> LoadedFile:
    """Parse one file's bytes. ``path`` is only used to label issues."""
    digest = hashlib.sha256(data).hexdigest()

    def failed(message: str, raw_id: str | None = None) -> LoadedFile:
        issue = DefinitionIssue(severity="error", path=path, message=message)
        return LoadedFile(path, kind, digest, raw_id, None, (issue,))

    if len(data) > MAX_DEFINITION_BYTES:
        return failed(f"file is larger than {MAX_DEFINITION_BYTES // 1_000_000} MB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return failed("file is not UTF-8 text")
    try:
        raw = yaml.load(text, Loader=_StrictLoader)  # _StrictLoader is a SafeLoader
    except yaml.YAMLError as exc:
        return failed(_yaml_error(exc))
    if raw is None:
        return failed("file is empty")
    if not isinstance(raw, dict):
        return failed("expected a mapping (key: value pairs) at the top level")

    raw_id = raw.get("id") if isinstance(raw.get("id"), str) else None
    declared = raw.get("kind")
    if declared != kind:
        suffix = AGENT_FILE_SUFFIX if kind == "agent" else WORKFLOW_FILE_SUFFIX
        found = "no `kind`" if declared is None else f"`kind: {declared}`"
        return failed(f"a *{suffix} file needs `kind: {kind}`, found {found}", raw_id)

    model: type[Definition] = AgentDefinition if kind == "agent" else WorkflowDefinition
    try:
        definition = model.model_validate(raw)
    except ValidationError as exc:
        return LoadedFile(
            path, kind, digest, raw_id, None, tuple(_validation_issues(path, exc, raw))
        )
    return LoadedFile(path, kind, digest, raw_id, definition)


# --- Folder -----------------------------------------------------------------

_cache: dict[str, tuple[tuple[int, int, int, int], LoadedFile]] = {}
_cache_lock = threading.Lock()


def _scan(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Hidden folders (.git, editor state) never hold definitions.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if kind_for(name) is not None:
                found.append(Path(dirpath) / name)
    return found


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_file(root: Path, path: Path) -> LoadedFile:
    rel = _relative(root, path)
    kind = kind_for(path.name)
    assert kind is not None
    if path.is_symlink():
        try:
            path.resolve(strict=True).relative_to(root.resolve())
        except (OSError, ValueError):
            issue = DefinitionIssue(
                severity="error",
                path=rel,
                message="symlink points outside the definitions folder (or nowhere)",
            )
            return LoadedFile(rel, kind, "", None, None, (issue,))
    try:
        stat = path.stat()
    except OSError as exc:
        issue = DefinitionIssue(severity="error", path=rel, message=f"cannot read file: {exc}")
        return LoadedFile(rel, kind, "", None, None, (issue,))

    key = str(path)
    # The inode and ctime catch an atomic replace or an edit that lands in the
    # same mtime tick with the same size.
    fingerprint = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and hit[0] == fingerprint:
        return hit[1]
    try:
        data = path.read_bytes()
    except OSError as exc:
        issue = DefinitionIssue(severity="error", path=rel, message=f"cannot read file: {exc}")
        return LoadedFile(rel, kind, "", None, None, (issue,))
    loaded = parse_definition(rel, kind, data)
    with _cache_lock:
        _cache[key] = (fingerprint, loaded)
    return loaded


def load_definitions(root: Path) -> DefinitionSet:
    """Load every ``*.agent.yaml`` / ``*.workflow.yaml`` under ``root``."""
    if not root.is_dir():
        return DefinitionSet(root=root, exists=False)
    paths = _scan(root)
    files = tuple(_load_file(root, p) for p in paths)
    seen = {str(p) for p in paths}
    prefix = str(root) + os.sep
    with _cache_lock:
        for stale in [k for k in _cache if k.startswith(prefix) and k not in seen]:
            del _cache[stale]
    by_id: dict[str, list[LoadedFile]] = {}
    for f in files:
        if f.raw_id:
            by_id.setdefault(f.raw_id, []).append(f)
    return DefinitionSet(
        root=root,
        exists=True,
        files=files,
        by_path={f.path: f for f in files},
        by_id={k: tuple(v) for k, v in by_id.items()},
    )
