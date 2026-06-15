"""Skill CRUD + export endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Skill
from precursor.backend.schemas import SkillCreate, SkillRead, SkillUpdate

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Names colliding with built-in slash commands would be confusing in the picker.
_RESERVED_NAMES: frozenset[str] = frozenset(
    {"gh-update", "gh-sync", "gh-create", "gh-close", "notes"}
)


def _check_reserved(name: str) -> None:
    if name in _RESERVED_NAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{name}' is reserved by a built-in command.",
        )


@router.get("", response_model=list[SkillRead])
async def list_skills(session: AsyncSession = Depends(get_session)) -> list[Skill]:
    result = await session.execute(select(Skill).order_by(Skill.name))
    return list(result.scalars().all())


@router.post("", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillCreate, session: AsyncSession = Depends(get_session)) -> Skill:
    _check_reserved(payload.name)
    skill = Skill(**payload.model_dump())
    session.add(skill)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A skill named '{payload.name}' already exists."
        ) from exc
    await session.refresh(skill)
    return skill


@router.get("/{skill_id}", response_model=SkillRead)
async def get_skill(skill_id: int, session: AsyncSession = Depends(get_session)) -> Skill:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    return skill


@router.patch("/{skill_id}", response_model=SkillRead)
async def update_skill(
    skill_id: int,
    payload: SkillUpdate,
    session: AsyncSession = Depends(get_session),
) -> Skill:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != skill.name:
        _check_reserved(data["name"])
    for key, value in data.items():
        setattr(skill, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A skill with that name already exists."
        ) from exc
    await session.refresh(skill)
    return skill


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: int, session: AsyncSession = Depends(get_session)) -> None:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    await session.delete(skill)
    await session.commit()


@router.get(
    "/{skill_id}/export",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def export_skill(
    skill_id: int, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    body_lines = [f"# /{skill.name}", ""]
    if skill.description:
        body_lines += [skill.description.strip(), ""]
    body_lines += ["## Instructions", "", skill.instructions.rstrip(), ""]
    return PlainTextResponse(
        "\n".join(body_lines),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{skill.name}.SKILL.md"',
        },
    )
