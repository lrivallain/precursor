"""Assistant Role CRUD endpoints.

A Role is a persistent persona (system prompt) a user attaches to a discussion,
identified by its name. Where it is stored follows the definitions source (the
database, or a ``roles/*.role.yaml`` file). The ``default`` role is protected:
it cannot be deleted or renamed, and no other role may take its name. Nothing
is renamed from the app — everything refers to a role by name — so changing a
name means editing the file and what uses it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
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
from precursor.backend.schemas import RoleCreate, RoleRead, RoleUpdate
from precursor.backend.schemas.definitions import DEFAULT_ROLE_NAME
from precursor.backend.services import roles as roles_service
from precursor.backend.services.definitions import overlay
from precursor.backend.services.roles import RoleFileError, RoleInfo

router = APIRouter(prefix="/api/roles", tags=["roles"])

# Columns that name a role; cleared when it is deleted.
_REFERENCES = (
    Topic.role,
    Chat.role,
    MeetingSession.role,
    AgentSession.role,
    AgentBlueprint.role,
    Workflow.role,
    Collection.default_role,
)


def _read(role: RoleInfo) -> RoleRead:
    return RoleRead(
        name=role.name,
        system_prompt=role.system_prompt,
        is_default=role.is_default,
        description=role.description,
        path=role.path,
    )


async def _get(session: AsyncSession, name: str) -> RoleInfo:
    role = await roles_service.find_role(session, name)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return role


async def _row(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(select(Role).where(func.lower(Role.name) == name.lower()))
    return result.scalars().first()


@router.get("", response_model=list[RoleRead])
async def list_roles(session: AsyncSession = Depends(get_session)) -> list[RoleRead]:
    return [_read(r) for r in await roles_service.list_roles(session)]


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate, session: AsyncSession = Depends(get_session)
) -> RoleRead:
    if payload.name.lower() == DEFAULT_ROLE_NAME:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "'default' is reserved for the built-in role."
        )
    if await roles_service.find_role(session, payload.name):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A role named '{payload.name}' already exists."
        )
    if overlay.files_mode():
        try:
            roles_service.create_role_file(payload.name, payload.system_prompt)
        except RoleFileError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    else:
        session.add(Role(name=payload.name, system_prompt=payload.system_prompt, is_default=False))
        await session.commit()
    return _read(await _get(session, payload.name))


@router.get("/{name:path}", response_model=RoleRead)
async def get_role(name: str, session: AsyncSession = Depends(get_session)) -> RoleRead:
    return _read(await _get(session, name))


@router.patch("/{name:path}", response_model=RoleRead)
async def update_role(
    name: str, payload: RoleUpdate, session: AsyncSession = Depends(get_session)
) -> RoleRead:
    role = await _get(session, name)
    if payload.name is not None and payload.name != role.name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A role can't be renamed from the app: everything refers to it by name. "
            "Edit its file and what uses it.",
        )
    if payload.system_prompt is not None:
        if role.path is not None:
            try:
                roles_service.save_role_file(role, payload.system_prompt)
            except RoleFileError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        elif overlay.files_mode():
            # The built-in default, before its file exists.
            try:
                roles_service.create_role_file(role.name, payload.system_prompt)
            except RoleFileError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        else:
            row = await _row(session, role.name)
            if row is not None:
                row.system_prompt = payload.system_prompt
                await session.commit()
    return _read(await _get(session, name))


@router.delete("/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(name: str, session: AsyncSession = Depends(get_session)) -> None:
    role = await _get(session, name)
    if role.is_default:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The default role cannot be deleted.")
    # Whatever used the role reverts to the default; cleared here rather than
    # by a foreign key, which SQLite doesn't enforce.
    for column in _REFERENCES:
        await session.execute(
            update(column.class_)
            .where(func.lower(column) == role.name.lower())
            .values({column.key: None})
        )
    row = await _row(session, role.name)
    if row is not None:
        await session.delete(row)
    await session.commit()
    if role.path is not None:
        roles_service.delete_role_file(role)
