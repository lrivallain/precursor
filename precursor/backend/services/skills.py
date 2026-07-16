"""Skill persistence service.

Skills are stored as ``<copilot_home>/skills/<name>/SKILL.md`` files (the shared
GitHub Copilot CLI format: YAML frontmatter with ``name``/``description`` and a
markdown body that *is* the instructions). The ``skills`` table is reduced to an
enablement record for those file-backed skills, plus transitional rows for
legacy skills whose content still lives in the DB until migrated.

Reconciliation rules:

* A *legacy* row (``migrated`` False) keeps its DB content and is always active
  (offered as ``/name``) until migrated. It is shown with a "Migrate" action.
* A file-backed skill is active only when enabled. The default for a freshly
  discovered file (no row, or a disabled row) is **disabled**.
* If a tracked file is renamed or deleted, its enablement row is dropped — the
  enablement status is lost, as designed. The one exception is when discovery
  turns up *no* files at all: an empty/unreadable skills directory is treated as
  a transient condition (e.g. the path isn't mounted yet) and enablement rows are
  preserved rather than silently wiping every skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import Skill

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# Names colliding with built-in slash commands would be confusing in the picker.
RESERVED_NAMES: frozenset[str] = frozenset(
    {"gh-update", "gh-sync", "gh-create", "gh-close", "notes"}
)

# Sentinel marking "argument not provided" so callers can clear a field by
# explicitly passing ``None`` while omission means "leave unchanged".
UNSET: object = object()


class SkillError(Exception):
    """Raised for invalid skill operations (bad name, conflict, not found)."""


@dataclass(slots=True)
class ResolvedSkill:
    """A skill as presented to the API/UI, merging file + DB state."""

    name: str
    description: str | None
    instructions: str
    # True while the skill is a file-backed skill that is turned on, OR a legacy
    # skill (always active until migrated).
    enabled: bool
    # True for un-migrated DB skills (these expose a "Migrate" action).
    legacy: bool
    # Whether the skill is usable as a slash command right now.
    active: bool


# ---------------------------------------------------------------------------
# filesystem helpers
# ---------------------------------------------------------------------------
def skills_root() -> Path:
    return Path(get_settings().skills_dir)


def _skill_dir(name: str) -> Path:
    return skills_root() / name


def _skill_file(name: str) -> Path:
    return _skill_dir(name) / "SKILL.md"


def validate_name(name: str) -> str:
    name = name.strip().lower()
    if not _NAME_RE.match(name):
        raise SkillError(
            "name must start with a letter and only contain lowercase letters, digits, or hyphens"
        )
    return name


def check_reserved(name: str) -> None:
    if name in RESERVED_NAMES:
        raise SkillError(f"'{name}' is reserved by a built-in command.")


def render_skill_file(name: str, description: str | None, instructions: str) -> str:
    """Serialise a skill to the shared SKILL.md format."""
    front: dict[str, str] = {"name": name}
    if description and description.strip():
        front["description"] = description.strip()
    frontmatter = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    body = instructions.rstrip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


def _parse_skill_file(path: Path, folder_name: str) -> tuple[str, str | None, str]:
    """Return ``(name, description, instructions)`` parsed from a SKILL.md."""
    text = path.read_text(encoding="utf-8")
    name = folder_name
    description: str | None = None
    body = text
    if text.startswith("---"):
        # Split frontmatter: opening "---" line, YAML, closing "---" line.
        parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
        if len(parts) >= 3:
            raw_front, body = parts[1], parts[2]
            try:
                data = yaml.safe_load(raw_front) or {}
            except yaml.YAMLError:
                data = {}
            if isinstance(data, dict):
                if isinstance(data.get("name"), str) and data["name"].strip():
                    name = data["name"].strip()
                desc = data.get("description")
                if isinstance(desc, str) and desc.strip():
                    description = desc.strip()
    return name, description, body.strip()


def _discover_files() -> dict[str, tuple[str | None, str]]:
    """Scan the skills dir → ``{name: (description, instructions)}``."""
    root = skills_root()
    found: dict[str, tuple[str | None, str]] = {}
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            name, description, instructions = _parse_skill_file(skill_md, entry.name)
        except (OSError, UnicodeDecodeError):
            continue
        name = name.strip().lower()
        if not _NAME_RE.match(name):
            continue
        # Folder name wins for addressing; ignore frontmatter name mismatches so
        # the slash command always matches the folder the file lives in.
        found.setdefault(entry.name, (description, instructions))
    return found


def write_skill_file(name: str, description: str | None, instructions: str) -> None:
    path = _skill_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skill_file(name, description, instructions), encoding="utf-8")


def delete_skill_dir(name: str) -> None:
    import shutil

    target = _skill_dir(name)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------
async def _rows_by_name(session: AsyncSession) -> dict[str, Skill]:
    rows = (await session.execute(select(Skill))).scalars().all()
    return {r.name: r for r in rows}


async def reconcile_and_list(session: AsyncSession) -> list[ResolvedSkill]:
    """Merge DB rows with on-disk files, dropping orphaned enablement rows."""
    files = _discover_files()
    rows = await _rows_by_name(session)

    # Only reconcile orphaned enablement rows when discovery actually found
    # files. An empty mapping means the skills directory is missing, unreadable,
    # or not yet mounted — a transient condition. Dropping rows here would
    # silently disable every skill, so we preserve enablement until files
    # reappear (see module docstring).
    if files:
        dropped = False
        for name, row in list(rows.items()):
            # A tracked file-backed row whose file vanished loses its enablement.
            if row.migrated and name not in files:
                await session.delete(row)
                del rows[name]
                dropped = True
        if dropped:
            await session.commit()

    resolved: dict[str, ResolvedSkill] = {}

    # Legacy (un-migrated) skills: content from DB, always active.
    for name, row in rows.items():
        if not row.migrated:
            resolved[name] = ResolvedSkill(
                name=name,
                description=row.description,
                instructions=row.instructions or "",
                enabled=True,
                legacy=True,
                active=True,
            )

    # File-backed skills: content from disk, enablement from the (optional) row.
    for name, (description, instructions) in files.items():
        if name in resolved:  # shadowed by a legacy row of the same name
            continue
        db_row = rows.get(name)
        enabled = bool(db_row and db_row.migrated and db_row.enabled)
        resolved[name] = ResolvedSkill(
            name=name,
            description=description,
            instructions=instructions,
            enabled=enabled,
            legacy=False,
            active=enabled,
        )

    return sorted(resolved.values(), key=lambda s: s.name)


async def get_resolved(session: AsyncSession, name: str) -> ResolvedSkill | None:
    for skill in await reconcile_and_list(session):
        if skill.name == name:
            return skill
    return None


async def get_active_instructions(session: AsyncSession, name: str) -> str | None:
    """Instructions for an *active* skill, or ``None`` (used to expand ``/name``)."""
    skill = await get_resolved(session, name)
    if skill is None or not skill.active:
        return None
    return skill.instructions


# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------
async def create_skill(
    session: AsyncSession,
    name: str,
    description: str | None,
    instructions: str,
) -> ResolvedSkill:
    name = validate_name(name)
    check_reserved(name)
    if _skill_file(name).exists():
        raise SkillError(f"A skill named '{name}' already exists.")
    existing = (await session.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()
    if existing is not None and not existing.migrated:
        raise SkillError(f"A skill named '{name}' already exists.")

    write_skill_file(name, description, instructions)
    if existing is None:
        session.add(Skill(name=name, enabled=True, migrated=True, instructions=""))
    else:
        existing.enabled = True
    await session.commit()
    resolved = await get_resolved(session, name)
    assert resolved is not None
    return resolved


async def update_skill(
    session: AsyncSession,
    name: str,
    *,
    new_name: str | None = None,
    description: str | None | object = UNSET,
    instructions: str | None | object = UNSET,
    enabled: bool | None = None,
) -> ResolvedSkill:
    skill = await get_resolved(session, name)
    if skill is None:
        raise SkillError("Skill not found")

    row = (await session.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()

    target_name = name
    if new_name is not None and new_name != name:
        target_name = validate_name(new_name)
        check_reserved(target_name)
        if (await get_resolved(session, target_name)) is not None:
            raise SkillError(f"A skill named '{target_name}' already exists.")

    if skill.legacy:
        # Legacy content stays in the DB until migrated.
        if row is None:  # pragma: no cover - legacy always has a row
            raise SkillError("Skill not found")
        if description is not UNSET:
            row.description = description or None  # type: ignore[assignment]
        if instructions is not UNSET:
            row.instructions = instructions or ""  # type: ignore[assignment]
        if target_name != name:
            row.name = target_name
        await session.commit()
    else:
        # Only (re)write the file when content or the name actually changed —
        # a pure enable/disable toggle must not reformat a file another tool owns.
        content_change = (
            description is not UNSET or instructions is not UNSET or target_name != name
        )
        if content_change:
            final_desc = skill.description if description is UNSET else (description or None)
            final_instr = skill.instructions if instructions is UNSET else (instructions or "")
            if target_name != name:
                delete_skill_dir(name)
            write_skill_file(target_name, final_desc, final_instr)  # type: ignore[arg-type]
        # Track enablement on the row (creating one if needed).
        if row is None:
            row = Skill(name=target_name, migrated=True, instructions="")
            session.add(row)
        else:
            row.name = target_name
            row.migrated = True
            row.instructions = ""
            row.description = None
        if enabled is not None:
            row.enabled = enabled
        await session.commit()

    resolved = await get_resolved(session, target_name)
    assert resolved is not None
    return resolved


async def migrate_skill(session: AsyncSession, name: str) -> ResolvedSkill:
    """Promote a legacy DB skill to a file-backed, enabled skill."""
    row = (await session.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()
    if row is None or row.migrated:
        raise SkillError("No legacy skill to migrate.")
    if _skill_file(name).exists():
        raise SkillError(f"A skill file named '{name}' already exists.")

    write_skill_file(name, row.description, row.instructions or "")
    row.migrated = True
    row.enabled = True
    row.description = None
    row.instructions = ""
    await session.commit()
    resolved = await get_resolved(session, name)
    assert resolved is not None
    return resolved


async def delete_skill(session: AsyncSession, name: str) -> None:
    skill = await get_resolved(session, name)
    if skill is None:
        raise SkillError("Skill not found")
    row = (await session.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()
    if not skill.legacy:
        delete_skill_dir(name)
    if row is not None:
        await session.delete(row)
        await session.commit()
