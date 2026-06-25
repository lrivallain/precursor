"""Memory CRUD endpoints — long-term context injected into every chat."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Memory
from precursor.backend.schemas import MemoryCreate, MemoryRead, MemoryUpdate
from precursor.backend.services import memories as memory_service

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("", response_model=list[MemoryRead])
async def list_memories(session: AsyncSession = Depends(get_session)) -> list[Memory]:
    result = await session.execute(select(Memory).order_by(Memory.kind, Memory.created_at))
    return list(result.scalars().all())


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate, session: AsyncSession = Depends(get_session)
) -> Memory:
    return await memory_service.create_memory(session, payload)


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: int,
    payload: MemoryUpdate,
    session: AsyncSession = Depends(get_session),
) -> Memory:
    try:
        return await memory_service.update_memory(session, memory_id, payload)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found") from exc


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: int, session: AsyncSession = Depends(get_session)) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    await session.delete(memory)
    await session.commit()
