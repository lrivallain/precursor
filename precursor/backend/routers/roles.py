"""Assistant Role CRUD endpoints.

A Role is a persistent persona (system prompt) a user attaches to a discussion.
The seeded ``default`` role is protected: it cannot be deleted or renamed, and a
second role named ``default`` cannot be created.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import AgentSession, Chat, MeetingSession, Role, Topic, Workspace
from precursor.backend.schemas import RoleCreate, RoleRead, RoleUpdate

router = APIRouter(prefix="/api/roles", tags=["roles"])

# The protected built-in role name. Reserved so users can't shadow it.
_DEFAULT_NAME = "default"


async def _find_by_name(
    session: AsyncSession, name: str, *, exclude_id: int | None = None
) -> Role | None:
    """Case-insensitive name lookup (SQLite's UNIQUE index is case-sensitive)."""
    stmt = select(Role).where(func.lower(Role.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Role.id != exclude_id)
    result = await session.execute(stmt)
    return result.scalars().first()


@router.get("", response_model=list[RoleRead])
async def list_roles(session: AsyncSession = Depends(get_session)) -> list[Role]:
    # Default first, then alphabetical, so the selector lists it at the top.
    result = await session.execute(
        select(Role).order_by(Role.is_default.desc(), func.lower(Role.name))
    )
    return list(result.scalars().all())


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(payload: RoleCreate, session: AsyncSession = Depends(get_session)) -> Role:
    if payload.name.lower() == _DEFAULT_NAME:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "'default' is reserved for the built-in role.",
        )
    if await _find_by_name(session, payload.name):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A role named '{payload.name}' already exists."
        )
    role = Role(name=payload.name, system_prompt=payload.system_prompt, is_default=False)
    session.add(role)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A role named '{payload.name}' already exists."
        ) from exc
    await session.refresh(role)
    return role


@router.get("/{role_id}", response_model=RoleRead)
async def get_role(role_id: int, session: AsyncSession = Depends(get_session)) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return role


@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    session: AsyncSession = Depends(get_session),
) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != role.name:
        if role.is_default:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The default role cannot be renamed.")
        if data["name"].lower() == _DEFAULT_NAME:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "'default' is reserved for the built-in role."
            )
        if await _find_by_name(session, data["name"], exclude_id=role_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"A role named '{data['name']}' already exists."
            )
    for key, value in data.items():
        setattr(role, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A role with that name already exists."
        ) from exc
    await session.refresh(role)
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: int, session: AsyncSession = Depends(get_session)) -> None:
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if role.is_default:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The default role cannot be deleted.")
    # Revert every discussion that used this role back to the default. We clear
    # the references explicitly rather than relying on the DB FK, since SQLite
    # does not enforce ON DELETE SET NULL unless foreign_keys pragma is on — and
    # this codebase manages such cleanups in the API layer (see topics.delete).
    for model in (Topic, Chat, Workspace, MeetingSession, AgentSession):
        await session.execute(update(model).where(model.role_id == role_id).values(role_id=None))
    await session.delete(role)
    await session.commit()
