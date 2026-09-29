"""Summary templates: how a live session's recap is written.

A template is the instruction the recap is generated from — its sections, tone
and length. A handful ship with Precursor (``builtin/``); any
``*.summary.yaml`` in the definitions folder adds one, or replaces the built-in
that carries the same ``id``. Unlike agents and workflows, templates have no
database side and are always read from the folder, whatever the definitions
source.

A template file with errors is never used: it's reported with the others' so
the picker can say why, and a built-in of the same id stays available.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AppSetting
from precursor.backend.schemas.definitions import (
    SUMMARY_FILE_SUFFIX,
    SUMMARY_NAME_MAX,
    SummaryDefinition,
)
from precursor.backend.schemas.definitions_api import DefinitionIssue
from precursor.backend.services.app_settings import (
    DEFAULT_LIVE_SUMMARY_TEMPLATE,
    LIVE_SUMMARY_LANGUAGE_KEY,
    LIVE_SUMMARY_TEMPLATE_KEY,
)
from precursor.backend.services.definitions import overlay
from precursor.backend.services.definitions.checker import check_definitions
from precursor.backend.services.definitions.exporter import render_document
from precursor.backend.services.definitions.loader import DefinitionSet, parse_definition

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
# Where new template files go, relative to the definitions folder.
FOLDER = "summaries"

_FILE_HEADER = (
    "# A summary template for live sessions (see Live → Summary).\n"
    "# Its `id` is how the picker remembers it; the id of a built-in template\n"
    "# replaces that template until this file is deleted.\n"
)


@dataclass(frozen=True)
class SummaryTemplate:
    id: str
    name: str
    description: str | None
    prompt: str
    # Relative to the definitions folder; ``None`` for a built-in.
    path: str | None = None
    # A built-in with this id ships with Precursor (a file may replace it).
    builtin: bool = False


@dataclass(frozen=True)
class Catalog:
    templates: list[SummaryTemplate]
    # Errors and warnings about template files, which are left out meanwhile.
    problems: list[DefinitionIssue]

    def find(self, template_id: str | None) -> SummaryTemplate | None:
        return next((t for t in self.templates if t.id == template_id), None)

    def default(self) -> SummaryTemplate:
        return self.find(DEFAULT_LIVE_SUMMARY_TEMPLATE) or self.templates[0]


def _from_definition(defn: SummaryDefinition, path: str | None, builtin: bool) -> SummaryTemplate:
    return SummaryTemplate(
        id=defn.id,
        name=defn.name,
        description=defn.description,
        prompt=defn.prompt,
        path=path,
        builtin=builtin,
    )


@cache
def builtin_templates() -> dict[str, tuple[SummaryTemplate, str]]:
    """Every built-in template by id, with the text of its file."""
    out: dict[str, tuple[SummaryTemplate, str]] = {}
    for path in sorted(BUILTIN_DIR.glob(f"*{SUMMARY_FILE_SUFFIX}")):
        data = path.read_bytes()
        loaded = parse_definition(path.name, "summary", data)
        if not isinstance(loaded.definition, SummaryDefinition):
            raise RuntimeError(f"built-in summary template {path.name} is invalid: {loaded.issues}")
        defn = loaded.definition
        out[defn.id] = (_from_definition(defn, None, True), data.decode("utf-8"))
    if DEFAULT_LIVE_SUMMARY_TEMPLATE not in out:
        raise RuntimeError(f"the '{DEFAULT_LIVE_SUMMARY_TEMPLATE}' summary template is missing")
    return out


def build_catalog(dset: DefinitionSet) -> Catalog:
    builtins = builtin_templates()
    by_id = {ident: template for ident, (template, _) in builtins.items()}
    for f, defn in dset.summaries():
        # An id carried by two files is ambiguous: the check reports both.
        if dset.find("summary", f.raw_id) is f:
            by_id[defn.id] = _from_definition(defn, f.path, defn.id in builtins)
    summary_paths = {f.path for f in dset.files if f.kind == "summary"}
    problems = [i for i in check_definitions(dset) if i.path in summary_paths]
    default = by_id.pop(DEFAULT_LIVE_SUMMARY_TEMPLATE)
    rest = sorted(by_id.values(), key=lambda t: (t.name.casefold(), t.id))
    return Catalog(templates=[default, *rest], problems=problems)


async def load_catalog() -> Catalog:
    return build_catalog(await asyncio.to_thread(overlay.current_definitions))


# --- Last used ----------------------------------------------------------------


async def remember(session: AsyncSession, template_id: str, language: str) -> None:
    """Record what the recap was just written with (the caller commits)."""
    values = ((LIVE_SUMMARY_TEMPLATE_KEY, template_id), (LIVE_SUMMARY_LANGUAGE_KEY, language))
    for key, value in values:
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)


# --- Files --------------------------------------------------------------------


class TemplateFileError(Exception):
    pass


def _write_new(target: Path, text: str) -> None:
    """Write ``text`` to a file that must not exist yet: never over one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise TemplateFileError(f"{target.name} already exists") from exc


def _free_path(dset: DefinitionSet, root: Path, stem: str) -> str:
    taken = {p.lower() for p in dset.by_path}
    n = 1
    while True:
        rel = f"{FOLDER}/{stem if n == 1 else f'{stem}-{n}'}{SUMMARY_FILE_SUFFIX}"
        if rel.lower() not in taken and not (root / rel).exists():
            return rel
        n += 1


def _free_id(dset: DefinitionSet, base: str) -> str:
    builtins = builtin_templates()
    n = 1
    while True:
        suffix = "-copy" if n == 1 else f"-copy-{n}"
        ident = f"{base[: 64 - len(suffix)]}{suffix}"
        if ident not in builtins and not dset.by_id.get(ident):
            return ident
        n += 1


def _custom_text(builtin_text: str) -> str:
    """A built-in's file, as it's saved to the folder to be edited."""
    lines = builtin_text.splitlines(keepends=True)
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    return _FILE_HEADER + "".join(lines)


def template_file(template_id: str, *, duplicate: bool = False) -> tuple[str, str, bool]:
    """The file to edit ``template_id`` in: ``(id, path relative to the folder, created)``.

    A template that already has a file opens as it is. A built-in is saved to
    the folder under its own id, so the copy replaces it. ``duplicate`` saves
    any template as a new one, under a new id, to be adapted.
    """
    root = overlay.definitions_root()
    dset = overlay.current_definitions()
    catalog = build_catalog(dset)
    template = catalog.find(template_id)
    if template is None:
        raise LookupError(f"No summary template '{template_id}'")

    if duplicate:
        ident = _free_id(dset, template.id)
        suffix = " (copy)"
        name = f"{template.name[: SUMMARY_NAME_MAX - len(suffix)]}{suffix}"
        doc: dict[str, object] = {"kind": "summary", "id": ident, "name": name}
        if template.description:
            doc["description"] = template.description
        doc["prompt"] = template.prompt.rstrip("\n") + "\n"
        rel = _free_path(dset, root, ident)
        _write_new(root / rel, render_document(doc, header=_FILE_HEADER))
        overlay.invalidate()
        return ident, rel, True

    if template.path is not None:
        return template.id, template.path, False
    # A file already carries the id but isn't usable (errors, or a second file
    # with that id): open it to fix, rather than adding a third.
    carriers = dset.by_id.get(template.id, ())
    existing = [f for f in carriers if f.kind == "summary"]
    if existing:
        return template.id, existing[0].path, False
    if carriers:
        # Ids are unique across the folder: a second file with an agent's or a
        # workflow's id would stop that one from running.
        raise TemplateFileError(
            f"{carriers[0].path} already uses the id '{template.id}', so this template can't "
            "be saved under it; use “New from this one” instead"
        )
    rel = _free_path(dset, root, template.id)
    _write_new(root / rel, _custom_text(builtin_templates()[template.id][1]))
    overlay.invalidate()
    return template.id, rel, True
