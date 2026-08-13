"""Transfer router — YAML export and import of agents and workflows.

Export is a plain file download (``GET .../export``); import is deliberately two
calls, because the interesting decision — what to do when an incoming agent's
name already exists here — can only be offered once the collisions are known.
``/preview`` reports them and writes nothing; ``/import`` applies them along with
the caller's per-agent choice of replace / create / link.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import AgentSession
from precursor.backend.schemas.transfer import (
    TransferDocument,
    TransferImportRequest,
    TransferImportResult,
    TransferParseRequest,
    TransferPreview,
)
from precursor.backend.services.agents import transfer as transfer_svc
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import publish_agent_changed, publish_workflow_changed

router = APIRouter(prefix="/api/transfer", tags=["transfer"])


async def _require_enabled(session: AsyncSession) -> None:
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")


def _yaml_response(doc: TransferDocument) -> PlainTextResponse:
    return PlainTextResponse(
        transfer_svc.dump_yaml(doc),
        media_type="application/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="{transfer_svc.suggested_filename(doc)}"'
        },
    )


@router.get("/workflows/{workflow_id}", response_class=PlainTextResponse)
async def export_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    """Download a workflow and the agents its steps reference, as YAML."""
    workflow = await transfer_svc.load_workflow(session, workflow_id)
    return _yaml_response(await transfer_svc.export_workflow(session, workflow))


@router.get("/agents/{agent_id}", response_class=PlainTextResponse)
async def export_agent(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    """Download a single agent's definition as YAML."""
    agent: AgentSession | None = None
    if agent_id.isdigit():
        agent = await session.get(AgentSession, int(agent_id))
    if agent is None:
        agent = (
            await session.execute(
                select(AgentSession).where(AgentSession.copilot_session_id == agent_id)
            )
        ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent session not found")
    if agent.inline:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This agent belongs to a workflow step — export the workflow instead",
        )
    return _yaml_response(await transfer_svc.export_agent(session, agent))


@router.post("/preview", response_model=TransferPreview)
async def preview_import(
    payload: TransferParseRequest, session: AsyncSession = Depends(get_session)
) -> TransferPreview:
    """Report what importing this file would create, replace or collide with.

    Read-only: nothing is written, so the UI can safely call this the moment a
    file is dropped and show the conflict choices before the user commits.
    """
    await _require_enabled(session)
    doc = transfer_svc.parse_document(payload.content)
    return await transfer_svc.preview_document(session, doc)


@router.post("/import", response_model=TransferImportResult)
async def apply_import(
    payload: TransferImportRequest, session: AsyncSession = Depends(get_session)
) -> TransferImportResult:
    """Apply a previewed file with one resolution per reported conflict."""
    await _require_enabled(session)
    doc = transfer_svc.parse_document(payload.content)
    result = await transfer_svc.import_document(session, doc, payload.resolutions)
    if result.workflow_id is not None:
        await publish_workflow_changed(result.workflow_id)
    for agent_id in (
        result.created_agent_ids
        + result.replaced_agent_ids
        + ([result.agent_id] if result.agent_id else [])
    ):
        await publish_agent_changed(agent_session_id=agent_id, topic_id=None, chat_id=None)
    return result
