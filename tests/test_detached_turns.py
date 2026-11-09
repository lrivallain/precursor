"""Turns outlive the stream that started them (``turn_engine.lifecycle_stream``).

Reloading the page used to cancel the turn mid-flight: nothing after the user's
prompt was ever saved. Now the turn runs in a background task and the stream
only relays its events, so:

* a client that goes away doesn't stop the turn, which still runs to its end;
* once nobody listens, its events reach every window (the reloaded one has the
  same client id and would otherwise ignore them);
* stopping is explicit, through ``cancel_running_turns``;
* ``running_turns`` lets a reconnecting window learn what is still generating.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from precursor.backend.services import turn_engine
from precursor.backend.services.events import (
    get_bus,
    get_current_client_id,
    set_current_client_id,
)


async def _drain(queue: asyncio.Queue) -> list[dict]:  # type: ignore[type-arg]
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def test_turn_runs_on_after_the_client_disconnects() -> None:
    release = asyncio.Event()
    finished = asyncio.Event()
    client_ids: list[str | None] = []

    async def inner() -> AsyncIterator[dict[str, str]]:
        yield {"event": "delta", "data": "a"}
        await release.wait()
        client_ids.append(get_current_client_id())
        yield {"event": "delta", "data": "b"}
        finished.set()

    set_current_client_id("window-1")
    try:
        async with get_bus().subscribe() as bus:
            stream = turn_engine.lifecycle_stream("chat", 9001, inner())
            assert await anext(stream) == {"event": "delta", "data": "a"}
            assert turn_engine.running_turns() == [("chat", 9001)]
            # The page reloads: the response stream is closed.
            await stream.aclose()
            release.set()
            await asyncio.wait_for(finished.wait(), 5)
            for _ in range(50):
                if not turn_engine.running_turns():
                    break
                await asyncio.sleep(0.01)
            assert turn_engine.running_turns() == []
            events = await _drain(bus)
    finally:
        set_current_client_id(None)

    # Events raised after the disconnect are broadcast to every window.
    assert client_ids == [None]
    ended = [e for e in events if e["type"] == "stream.ended"]
    assert ended and ended[-1]["chat_id"] == 9001
    assert ended[-1]["client_id"] is None


async def test_cancel_running_turns_stops_the_turn() -> None:
    cleaned_up = asyncio.Event()

    async def inner() -> AsyncIterator[dict[str, str]]:
        try:
            yield {"event": "delta", "data": "a"}
            await asyncio.sleep(60)
            yield {"event": "delta", "data": "never"}
        finally:
            cleaned_up.set()

    stream = turn_engine.lifecycle_stream("topic", 9002, inner())
    assert await anext(stream) == {"event": "delta", "data": "a"}
    assert await turn_engine.cancel_running_turns("topic", 9002) is True
    assert cleaned_up.is_set()
    assert turn_engine.running_turns() == []
    assert await turn_engine.cancel_running_turns("topic", 9002) is False
    await stream.aclose()


async def test_attached_client_receives_every_event_then_the_stream_ends() -> None:
    async def inner() -> AsyncIterator[dict[str, str]]:
        for i in range(3):
            yield {"event": "delta", "data": str(i)}

    events = [e async for e in turn_engine.lifecycle_stream("chat", 9003, inner())]
    assert [e["data"] for e in events] == ["0", "1", "2"]
