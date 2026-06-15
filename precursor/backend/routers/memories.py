"""Memory CRUD endpoints — long-term context injected into every chat."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Memory
from precursor.backend.schemas import MemoryCreate, MemoryRead, MemoryUpdate

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("", response_model=list[MemoryRead])
async def list_memories(session: AsyncSession = Depends(get_session)) -> list[Memory]:
    result = await session.execute(select(Memory).order_by(Memory.kind, Memory.created_at))
    return list(result.scalars().all())


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate, session: AsyncSession = Depends(get_session)
) -> Memory:
    memory = Memory(**payload.model_dump())
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: int,
    payload: MemoryUpdate,
    session: AsyncSession = Depends(get_session),
) -> Memory:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(memory, key, value)
    await session.commit()
    await session.refresh(memory)
    return memory


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: int, session: AsyncSession = Depends(get_session)) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    await session.delete(memory)
    await session.commit()
