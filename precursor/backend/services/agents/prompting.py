"""The system-message preamble appended to every agent SDK session.

Combines the role persona, the operator's custom prompt, long-term memory, the
agent's scratchpad index, its topic binding and the autonomy/skills/suggestions
directives. Stateless; must not import ``manager`` at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentSession, Topic
from precursor.backend.services.agent_state import build_state_index_prompt
from precursor.backend.services.agents.directives import _AUTONOMY_PROTOCOL
from precursor.backend.services.app_settings import resolve_agents_system_prompt
from precursor.backend.services.memories import build_memory_prompt
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.suggestions import SUGGESTIONS_INSTRUCTION

if TYPE_CHECKING:
    from precursor.backend.services.agents.live_session import _Caps

# Appended when an agent has skills switched off. Skills live as files the SDK
# discovers on disk, so there's no kwarg to withhold them — this is a directive,
# not a sandbox. It exists so a focused step ("just translate this") doesn't
# detour through a stored skill that was written for a different context.
_NO_SKILLS_INSTRUCTION = (
    "Do not invoke any stored skill for this task. Solve it directly with your own "
    "reasoning and the material you have been given."
)


async def topic_context(agent: AgentSession) -> str | None:
    """Build a system-message preamble binding the agent to its topic.

    Without this the agent has no idea which topic it's attached to, so a
    request like "summarise the topic description" gets answered from the
    tool's field schema instead of the actual record. We give it the id,
    title and description, and point it at the precursor MCP tools to pull
    the rest on demand (and post results back).
    """
    if not agent.topic_id:
        return None
    async with SessionLocal() as session:
        topic = await session.get(Topic, agent.topic_id)
    if topic is None:
        return None
    lines = [
        "## Bound Precursor topic",
        "",
        f'You are operating on Precursor topic #{topic.id} ("{topic.title}").',
    ]
    description = (topic.description or "").strip()
    if description:
        lines += ["", "Topic description:", description]
    lines += [
        "",
        "Use the `precursor` MCP tools to work with it: `get_topic("
        f"{topic.id})` for metadata, `list_messages({topic.id})` to read the "
        "conversation, `search(...)` to find related content, and "
        f"`post_message({topic.id}, ...)` to write your results back to the "
        "topic. Prefer reading the live topic over assumptions.",
    ]
    return "\n".join(lines)


async def system_preamble(agent: AgentSession, caps: _Caps | None = None) -> str | None:
    """Combined system-message append: role persona + operator custom prompt + memory + topic binding.

    The SDK base prompt isn't ours to set, so each piece is *appended*. The
    agent's Assistant Role persona comes first (it defines who the agent is),
    then the custom prompt (Settings → Agents) as general guidance, long-term
    memory as standing context (matching chat/topic turns), then the topic
    binding so the agent always knows which record it's on.

    ``caps`` supplies the capability toggles — the executing run's immutable
    snapshot, so a definition edit mid-run can't change the preamble the
    session was built with. Identity (topic binding, scratchpad, autonomy)
    always comes from the agent.
    """
    caps = caps or agent
    async with SessionLocal() as session:
        role_prompt = (await resolve_role_prompt(session, caps.role_id)).strip()
        custom = (await resolve_agents_system_prompt(session)).strip()
        # Long-term memory is standing context, not always wanted: a pure
        # transform step ("translate this") is better off not consulting it.
        memory = await build_memory_prompt(session) if caps.use_memory else ""
        # The agent's own scratchpad from previous runs. Only the *key index*
        # goes in the prompt — the bodies stay in the DB until the agent asks
        # for one with ``state_get``, so a large saved cursor costs nothing
        # per turn. Tool-less agents can't call ``state_get``, so telling them
        # what they can't read would just burn context.
        state = (await build_state_index_prompt(session, agent.id) or "") if caps.use_mcp else ""
    persona = (
        f"Active assistant role — adopt this persona for the whole task:\n{role_prompt}"
        if role_prompt
        else ""
    )
    topic = await topic_context(agent)
    autonomy = _AUTONOMY_PROTOCOL if agent.autonomy_enabled else ""
    # Follow-up "suggest" chips are for a human replying turn-by-turn. An
    # autonomous agent drives itself via the control directives and runs
    # unattended, so inviting user-facing follow-ups there just burns tokens
    # and pulls against the "keep going, don't ask" autonomy contract (and the
    # base prompt's "don't offer to continue" tone rule). Only plain agents,
    # which the user converses with, get the suggestions instruction.
    suggestions = "" if agent.autonomy_enabled else SUGGESTIONS_INSTRUCTION
    # Skills are files the SDK discovers on disk, so this is a *directive*
    # rather than a hard sandbox — it tells the agent to solve the task
    # directly instead of reaching for a stored skill.
    skills = "" if caps.use_skills else _NO_SKILLS_INSTRUCTION
    parts = [p for p in (persona, custom, memory, state, topic, autonomy, skills, suggestions) if p]
    return "\n\n".join(parts) if parts else None
