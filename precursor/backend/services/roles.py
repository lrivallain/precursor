"""Assistant Roles: named personas (system prompts) attached to a discussion.

A role is referenced **by name** (case-insensitively) from topics, chats,
agents, workflows, meetings and collections. Where roles live
follows the definitions source: the ``roles`` table in database mode, one
``roles/<slug>.role.yaml`` file each in files mode. A name nothing defines
resolves to the ``default`` role, so a stale reference never breaks a chat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import (
    AgentBlueprint,
    AgentSession,
    Chat,
    Collection,
    MeetingSession,
    Role,
    Topic,
    Workflow,
)
from precursor.backend.schemas.definitions import (
    DEFAULT_ROLE_ID,
    DEFAULT_ROLE_NAME,
    ROLE_FILE_SUFFIX,
    ROLE_NAME_MAX,
    RoleDefinition,
)
from precursor.backend.services import skills as skills_service
from precursor.backend.services.definitions import overlay, writer
from precursor.backend.services.definitions.exporter import render_document
from precursor.backend.services.slugs import slugify

log = logging.getLogger(__name__)

# Where new role files go, relative to the definitions folder.
FOLDER = "roles"

_FILE_HEADER = (
    "# An assistant role: a persona applied to every turn it is assigned to.\n"
    "# Topics, chats, agents and workflows refer to it by `name`, so renaming it\n"
    "# means updating whatever uses the old name.\n"
)


class RoleFileError(Exception):
    pass


@dataclass(frozen=True)
class RoleInfo:
    name: str
    system_prompt: str
    is_default: bool
    description: str | None = None
    # Relative to the definitions folder; ``None`` for a role held in the database.
    path: str | None = None


async def delete_role(session: AsyncSession, role: RoleInfo) -> None:
    if role.is_default:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The default role cannot be deleted.")
    await writer.clear_role_references(session, role.name)
    for column in (
        Topic.role,
        Chat.role,
        MeetingSession.role,
        AgentSession.role,
        AgentBlueprint.role,
        Workflow.role,
        Collection.default_role,
    ):
        await session.execute(
            update(column.class_)
            .where(func.lower(column) == role.name.lower())
            .values({column.key: None})
        )
    row = await session.scalar(select(Role).where(func.lower(Role.name) == role.name.lower()))
    if row is not None:
        await session.delete(row)
    if role.path is not None:
        try:
            delete_role_file(role)
        except OSError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Couldn't delete {role.path}: {exc}"
            ) from exc
    await session.commit()


def _from_files() -> list[RoleInfo]:
    roles = [
        RoleInfo(
            name=defn.name,
            system_prompt=defn.prompt,
            is_default=defn.name.lower() == DEFAULT_ROLE_NAME,
            description=defn.description,
            path=f.path,
        )
        for f, defn in overlay.current_definitions().roles()
    ]
    # A file with errors isn't in the list; a name two files share keeps the
    # first (the checker reports the clash).
    seen: set[str] = set()
    unique = []
    for role in sorted(roles, key=lambda r: (not r.is_default, r.name.lower(), r.path or "")):
        if role.name.lower() not in seen:
            seen.add(role.name.lower())
            unique.append(role)
    return unique


async def list_roles(session: AsyncSession) -> list[RoleInfo]:
    """Every role, the default first, then alphabetical."""
    if overlay.files_mode():
        roles = _from_files()
        if not any(r.is_default for r in roles):
            roles.insert(0, RoleInfo(DEFAULT_ROLE_NAME, "", True))
        return roles
    rows = (await session.execute(select(Role))).scalars().all()
    return [
        RoleInfo(r.name, r.system_prompt, r.is_default)
        for r in sorted(rows, key=lambda r: (not r.is_default, r.name.lower()))
    ]


async def find_role(session: AsyncSession, name: str | None) -> RoleInfo | None:
    if not name:
        return None
    wanted = name.strip().lower()
    for role in await list_roles(session):
        if role.name.lower() == wanted:
            return role
    return None


async def require_role(session: AsyncSession, name: str | None) -> str | None:
    """Validate a role name from a request and return it as the role spells it.

    ``None`` or an empty name clears the role (the default applies).
    """
    if name is None or not name.strip():
        return None
    role = await find_role(session, name)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Role '{name.strip()}' not found")
    return role.name


async def resolve_role_prompt(session: AsyncSession, name: str | None) -> str:
    """Return the system prompt to inject for the role called ``name``.

    A null name (or one nothing defines any more) resolves to the built-in
    ``default`` role, whose prompt is empty out of the box — so the net effect
    is "inject nothing" unless the user customised it.

    A role prompt may reference skills (``/rewrite``), expanded here so every
    surface that adopts a role — topics, chats, agents, meetings —
    gets the same treatment.
    """
    role = await find_role(session, name)
    if role is None:
        if name:
            log.warning("Role '%s' is not defined; using the default role", name)
        role = await find_role(session, DEFAULT_ROLE_NAME)
    if role is None:
        return ""
    return (await skills_service.expand_references(session, role.system_prompt)).strip()


async def create_role(session: AsyncSession, name: str, prompt: str) -> RoleInfo:
    """Add a role where roles live: a file in files mode, else a table row."""
    if overlay.files_mode():
        create_role_file(name, prompt)
    else:
        session.add(Role(name=name, system_prompt=prompt, is_default=False))
        await session.flush()
    created = await find_role(session, name)
    assert created is not None
    return created


async def export_to_files(session: AsyncSession) -> int:
    """Write a file for every role row that has none; returns how many.

    The migration's roles step. A role whose name a file already carries keeps
    that file: the folder is what the files mode reads.
    """
    overlay.invalidate()
    have = {defn.name.lower() for _, defn in overlay.current_definitions().roles()}
    rows = (await session.execute(select(Role).order_by(Role.id))).scalars().all()
    written = 0
    for row in rows:
        if row.name.lower() in have:
            continue
        create_role_file(row.name, row.system_prompt or "")
        have.add(row.name.lower())
        written += 1
    return written


async def import_from_files(session: AsyncSession) -> int:
    """Copy the role files into the table (switching back); returns how many."""
    overlay.invalidate()
    rows = {r.name.lower(): r for r in (await session.execute(select(Role))).scalars()}
    copied = 0
    for _, defn in overlay.current_definitions().roles():
        row = rows.get(defn.name.lower())
        if row is None:
            row = Role(name=defn.name, system_prompt=defn.prompt, is_default=False)
            session.add(row)
            rows[defn.name.lower()] = row
        else:
            row.system_prompt = defn.prompt
        copied += 1
    await session.flush()
    return copied


async def unfiled_roles(session: AsyncSession) -> list[str]:
    """Names of role rows no valid file carries (they'd be lost to the files mode)."""
    overlay.invalidate()
    have = {defn.name.lower() for _, defn in overlay.current_definitions().roles()}
    rows = (await session.execute(select(Role.name))).scalars().all()
    return [name for name in rows if name.lower() not in have]


# --- Files ------------------------------------------------------------------


def _write_new(target: Path, text: str) -> None:
    """Write ``text`` to a file that must not exist yet: never over one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise RoleFileError(f"{target.name} already exists") from exc


def _document(ident: str, name: str, prompt: str, description: str | None) -> str:
    doc: dict[str, object] = {"kind": "role", "id": ident, "name": name}
    if description:
        doc["description"] = description
    doc["prompt"] = prompt.rstrip("\n") + "\n" if prompt.strip() else ""
    return render_document(doc, header=_FILE_HEADER)


def _free(taken_paths: set[str], root: Path, ids: set[str], stem: str) -> tuple[str, str]:
    n = 1
    while True:
        ident = stem if n == 1 else f"{stem[:60]}-{n}"
        rel = f"{FOLDER}/{ident}{ROLE_FILE_SUFFIX}"
        if rel.lower() not in taken_paths and ident not in ids and not (root / rel).exists():
            return ident, rel
        n += 1


def _plan_new_file(name: str) -> tuple[str, str]:
    """The ``(id, path)`` a new role file would get, without writing it."""
    root = overlay.definitions_root()
    dset = overlay.current_definitions()
    if name.lower() == DEFAULT_ROLE_NAME:
        ident, rel = DEFAULT_ROLE_ID, f"{FOLDER}/{DEFAULT_ROLE_ID}{ROLE_FILE_SUFFIX}"
        if rel.lower() in {p.lower() for p in dset.by_path} or dset.by_id.get(ident):
            raise RoleFileError("the default role already has a file")
    else:
        stem = slugify(name)[:48] or "role"
        if stem == DEFAULT_ROLE_ID:
            stem = "role"
        ident, rel = _free({p.lower() for p in dset.by_path}, root, set(dset.by_id), stem)
    return ident, rel


def create_role_file(name: str, prompt: str, description: str | None = None) -> str:
    """Write a new role file; returns its path relative to the folder."""
    ident, rel = _plan_new_file(name)
    _write_new(
        overlay.definitions_root() / rel,
        _document(ident, name[:ROLE_NAME_MAX], prompt, description),
    )
    overlay.invalidate()
    return rel


@dataclass(frozen=True)
class RoleMigration:
    """What the migration does with one role row."""

    row: Role
    path: str | None
    action: str  # "create" | "unchanged"
    reason: str | None
    proposed: str
    current: str | None


def plan_migration(rows: list[Role]) -> list[RoleMigration]:
    """For each role row: the file it would get, or the one that already carries it."""
    overlay.invalidate()
    dset = overlay.current_definitions()
    root = overlay.definitions_root()
    have = {defn.name.lower(): (f.path, defn) for f, defn in dset.roles()}
    taken = {p.lower() for p in dset.by_path}
    ids = set(dset.by_id)
    out: list[RoleMigration] = []
    for row in rows:
        existing = have.get(row.name.lower())
        if existing is not None:
            path, defn = existing
            differs = defn.prompt.strip() != (row.system_prompt or "").strip()
            try:
                current: str | None = (root / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = None
            out.append(
                RoleMigration(
                    row=row,
                    path=path,
                    action="unchanged",
                    reason=(
                        "The file's prompt differs from the database; the file is kept."
                        if differs
                        else None
                    ),
                    proposed=current or "",
                    current=current,
                )
            )
            continue
        if row.name.lower() == DEFAULT_ROLE_NAME:
            ident, rel = DEFAULT_ROLE_ID, f"{FOLDER}/{DEFAULT_ROLE_ID}{ROLE_FILE_SUFFIX}"
        else:
            stem = slugify(row.name)[:48] or "role"
            if stem == DEFAULT_ROLE_ID:
                stem = "role"
            ident, rel = _free(taken, root, ids, stem)
        taken.add(rel.lower())
        ids.add(ident)
        out.append(
            RoleMigration(
                row=row,
                path=rel,
                action="create",
                reason=None,
                proposed=_document(ident, row.name[:ROLE_NAME_MAX], row.system_prompt or "", None),
                current=None,
            )
        )
    return out


def save_role_file(role: RoleInfo, prompt: str) -> None:
    """Replace the prompt of an existing role file, keeping its other fields."""
    if role.path is None:
        raise RoleFileError("this role has no file")
    loaded = overlay.current_definitions().by_path.get(role.path)
    if loaded is None or not isinstance(loaded.definition, RoleDefinition):
        raise RoleFileError(f"{role.path} has errors; fix the file first")
    defn = loaded.definition
    target = overlay.definitions_root() / role.path
    target.write_text(
        _document(defn.id, defn.name, prompt, defn.description), encoding="utf-8", newline=""
    )
    overlay.invalidate()


def delete_role_file(role: RoleInfo) -> None:
    if role.path is None:
        return
    (overlay.definitions_root() / role.path).unlink(missing_ok=True)
    overlay.invalidate()


def ensure_default_role_file() -> None:
    """Seed ``roles/default.role.yaml`` (empty prompt) when the folder has none."""
    if not overlay.files_mode():
        return
    if any(
        defn.name.lower() == DEFAULT_ROLE_NAME for _, defn in overlay.current_definitions().roles()
    ):
        return
    try:
        create_role_file(
            DEFAULT_ROLE_NAME, "", "Applies when nothing else is chosen; empty injects nothing."
        )
    except RoleFileError:
        log.warning("Couldn't create the default role file", exc_info=True)
