"""Topic and chat turns run in the background, detached from their request.

A turn is framed by stream.started / stream.ended, runs in a task of its own,
and holds the queue of messages the user sends while it runs
(``services/turn_steering.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from precursor.backend.services import turn_steering
from precursor.backend.services.events import (
    bind_client_detached,
    publish_stream_ended,
    publish_stream_ended_chat,
    publish_stream_started,
    publish_stream_started_chat,
)

logger = logging.getLogger(__name__)

ContainerKind = Literal["topic", "chat"]


# Turns running in the background, per container. A turn is not tied to the
# request that started it: closing or reloading the page only stops the relay of
# its events, while the turn itself runs on and persists its result. Stopping
# one is explicit (``cancel_running_turns``).
_RUNNING_TURNS: dict[tuple[str, int], set[asyncio.Task[Any]]] = {}
_END_OF_TURN: dict[str, str] = {"event": "__end__"}


def running_turns() -> list[tuple[str, int]]:
    """Containers with a turn still generating."""
    return [key for key, tasks in _RUNNING_TURNS.items() if tasks]


async def cancel_running_turns(kind: ContainerKind, container_id: int) -> bool:
    """Stop the turns generating in a container; True when one was running.

    Waits (briefly) for them to unwind, so the caller can persist what the user
    kept without racing the turn's own final write.
    """
    tasks = [t for t in _RUNNING_TURNS.get((kind, container_id), ()) if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    return bool(tasks)


def track(kind: ContainerKind, container_id: int, task: asyncio.Task[Any]) -> None:
    """Count ``task`` as a turn running in this conversation until it ends.

    Stop and clear-history cancel what is tracked here (``cancel_running_turns``),
    so a turn started outside a request (a self wakeup) registers too.
    """
    key = (kind, container_id)
    _RUNNING_TURNS.setdefault(key, set()).add(task)

    def forget(done: asyncio.Task[Any]) -> None:
        tasks = _RUNNING_TURNS.get(key)
        if tasks is not None:
            tasks.discard(done)
            if not tasks:
                _RUNNING_TURNS.pop(key, None)
        if not done.cancelled() and done.exception() is not None:
            logger.error("Turn in %s %s failed", kind, container_id, exc_info=done.exception())

    task.add_done_callback(forget)


def lifecycle_stream(
    kind: ContainerKind,
    container_id: int,
    inner: AsyncIterator[dict[str, str]],
) -> AsyncIterator[dict[str, str]]:
    """Run a turn in the background and relay its events to the caller.

    The turn is framed by stream.started / stream.ended publishing. It runs in a
    task of its own, so a client that disconnects (a page reload, a closed tab)
    no longer cancels it: the relay stops, the turn finishes and persists. From
    then on its bus events go to every window, the originating one included —
    that window, reloaded, has the same client id and would otherwise ignore the
    message.changed / stream.ended that bring the result in.

    Messages the user sends meanwhile wait in the turn's own steering queue,
    open from the moment the turn is started until it ends. The first event
    names the turn (``turn`` with its ``turn_id``), so the client addresses its
    messages to this turn and no other.
    """

    async def run(
        queue: asyncio.Queue[dict[str, str]],
        listener: dict[str, bool],
        steer: turn_steering.SteerQueue,
    ) -> None:
        # The task runs in its own context copy: these bindings are the turn's own.
        bind_client_detached(listener)
        turn_steering.bind_current(steer)
        if kind == "topic":
            await publish_stream_started(container_id)
        else:
            await publish_stream_started_chat(container_id)
        try:
            try:
                async for evt in inner:
                    if not listener["detached"]:
                        queue.put_nowait(evt)
            finally:
                # Close the turn's generator here, in its own task, so its
                # cleanup runs now rather than whenever it is collected.
                aclose = getattr(inner, "aclose", None)
                if aclose is not None:
                    await aclose()
        finally:
            turn_steering.close_queue(steer)
            if kind == "topic":
                await publish_stream_ended(container_id)
            else:
                await publish_stream_ended_chat(container_id)
            queue.put_nowait(_END_OF_TURN)

    async def relay() -> AsyncIterator[dict[str, str]]:
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        listener = {"detached": False}
        steer = turn_steering.open_queue(kind, container_id)
        task = asyncio.create_task(run(queue, listener, steer), name=f"turn-{kind}-{container_id}")
        track(kind, container_id, task)
        try:
            yield {"event": "turn", "data": json.dumps({"turn_id": steer.turn_id})}
            while True:
                evt = await queue.get()
                if evt is _END_OF_TURN:
                    return
                yield evt
        finally:
            # Detach only: the turn carries on without a listener, and its
            # events from now on reach every window.
            listener["detached"] = True

    return relay()
