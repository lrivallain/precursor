"""CRUD for approval rules (see ``services/approval_rules.py``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Topic
from precursor.backend.models.approval_rule import ApprovalRule
from precursor.backend.schemas import ApprovalRuleCreate, ApprovalRuleRead, ApprovalRuleUpdate
from precursor.backend.schemas.approval import RuleScope
from precursor.backend.services import approval_rules as rules_service

router = APIRouter(prefix="/api/approval-rules", tags=["approvals"])


async def _get_or_404(rule_id: int, session: AsyncSession) -> ApprovalRule:
    row = await session.get(ApprovalRule, rule_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    return row


@router.get("", response_model=list[ApprovalRuleRead])
async def list_rules(
    scope: RuleScope | None = None,
    topic_id: int | None = Query(default=None),
    chat_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[ApprovalRuleRead]:
    stmt = select(ApprovalRule)
    if scope is not None:
        stmt = stmt.where(ApprovalRule.scope == scope)
    if topic_id is not None:
        stmt = stmt.where(ApprovalRule.topic_id == topic_id)
    if chat_id is not None:
        stmt = stmt.where(ApprovalRule.chat_id == chat_id)
    rows = await session.execute(stmt.order_by(ApprovalRule.id))
    return [ApprovalRuleRead.model_validate(r) for r in rows.scalars().all()]


@router.post("", response_model=ApprovalRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: ApprovalRuleCreate, session: AsyncSession = Depends(get_session)
) -> ApprovalRuleRead:
    if payload.topic_id is not None and await session.get(Topic, payload.topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    if payload.chat_id is not None and await session.get(Chat, payload.chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    row = await rules_service.create_rule(
        session,
        effect=payload.effect,
        server=payload.server,
        tool=payload.tool,
        clauses=[rules_service.ArgClause(c.arg, c.op, c.value) for c in payload.arg_match],
        scope=payload.scope,
        topic_id=payload.topic_id,
        chat_id=payload.chat_id,
        note=payload.note,
    )
    return ApprovalRuleRead.model_validate(row)


@router.patch("/{rule_id}", response_model=ApprovalRuleRead)
async def update_rule(
    rule_id: int, payload: ApprovalRuleUpdate, session: AsyncSession = Depends(get_session)
) -> ApprovalRuleRead:
    row = await _get_or_404(rule_id, session)
    fields = payload.model_fields_set
    if payload.effect is not None:
        row.effect = payload.effect
    if payload.server is not None:
        row.server = payload.server
    if payload.tool is not None:
        row.tool = payload.tool
    if payload.arg_match is not None:
        row.arg_match = rules_service.clauses_json(
            rules_service.ArgClause(c.arg, c.op, c.value) for c in payload.arg_match
        )
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if "note" in fields:
        row.note = (payload.note or "").strip() or None
    await session.commit()
    await session.refresh(row)
    return ApprovalRuleRead.model_validate(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)) -> None:
    row = await _get_or_404(rule_id, session)
    await session.delete(row)
    await session.commit()
