"""Skill CRUD + migrate/export endpoints.

Skills are addressed by ``name`` (the on-disk folder / slash-command name).
Content lives in shared ``<copilot_home>/skills/<name>/SKILL.md`` files; this
router is a thin layer over ``services.skills``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas import SkillCreate, SkillRead, SkillUpdate
from precursor.backend.services import skills as skills_service
from precursor.backend.services.skills import ResolvedSkill, SkillError

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _to_read(skill: ResolvedSkill) -> SkillRead:
    return SkillRead(
        name=skill.name,
        description=skill.description,
        instructions=skill.instructions,
        enabled=skill.enabled,
        active=skill.active,
        legacy=skill.legacy,
    )


def _bad_request(exc: SkillError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=list[SkillRead])
async def list_skills(session: AsyncSession = Depends(get_session)) -> list[SkillRead]:
    return [_to_read(s) for s in await skills_service.reconcile_and_list(session)]


@router.post("", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillCreate, session: AsyncSession = Depends(get_session)
) -> SkillRead:
    try:
        skill = await skills_service.create_skill(
            session, payload.name, payload.description, payload.instructions
        )
    except SkillError as exc:
        raise _bad_request(exc) from exc
    return _to_read(skill)


@router.get("/{name}", response_model=SkillRead)
async def get_skill(name: str, session: AsyncSession = Depends(get_session)) -> SkillRead:
    skill = await skills_service.get_resolved(session, name)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    return _to_read(skill)


@router.patch("/{name}", response_model=SkillRead)
async def update_skill(
    name: str,
    payload: SkillUpdate,
    session: AsyncSession = Depends(get_session),
) -> SkillRead:
    data = payload.model_dump(exclude_unset=True)
    try:
        skill = await skills_service.update_skill(
            session,
            name,
            new_name=data.get("name"),
            description=data.get("description", skills_service.UNSET),
            instructions=data.get("instructions", skills_service.UNSET),
            enabled=data.get("enabled"),
        )
    except SkillError as exc:
        if str(exc) == "Skill not found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found") from exc
        raise _bad_request(exc) from exc
    return _to_read(skill)


@router.post("/{name}/migrate", response_model=SkillRead)
async def migrate_skill(name: str, session: AsyncSession = Depends(get_session)) -> SkillRead:
    try:
        skill = await skills_service.migrate_skill(session, name)
    except SkillError as exc:
        raise _bad_request(exc) from exc
    return _to_read(skill)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(name: str, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await skills_service.delete_skill(session, name)
    except SkillError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/{name}/export",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def export_skill(
    name: str, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    skill = await skills_service.get_resolved(session, name)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    body = skills_service.render_skill_file(skill.name, skill.description, skill.instructions)
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{skill.name}.SKILL.md"',
        },
    )
