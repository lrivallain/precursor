"""Messages the user sends while a topic or chat turn is still running.

A running turn holds a :class:`SteerQueue`. The user's message waits there, and
the tool loop takes it at the next round boundary: after the round's tool
results, or after a final answer, when it carries on instead of ending. The
message reaches the model on its next call, so the user can change the turn's
course without stopping it.

Once the loop has taken an empty queue at the end of the turn, the queue is
closed: a message sent after that is refused (``enqueue`` returns ``None``) and
the client sends it as the next turn instead.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Literal

ContainerKind = Literal["topic", "chat"]


@dataclass(frozen=True, slots=True)
class SteerItem:
    """A queued message; ``id`` lets the client match it to its echo."""

    id: str
    content: str


@dataclass(slots=True)
class SteerQueue:
    items: list[SteerItem] = field(default_factory=list)
    closed: bool = False

    def take(self, *, final: bool = False) -> list[SteerItem]:
        """Take every waiting message; on the turn's ``final`` check, close if none.

        Taking and closing happen in one step with no ``await`` between them,
        so a message is either taken by this turn or refused, never lost.
        """
        items, self.items = self.items, []
        if final and not items:
            self.closed = True
        return items


_QUEUES: dict[tuple[str, int], SteerQueue] = {}


def open_queue(kind: ContainerKind, container_id: int) -> SteerQueue:
    """Start accepting messages for the turn about to run in this container."""
    queue = SteerQueue()
    _QUEUES[(kind, container_id)] = queue
    return queue


def queue_for(kind: ContainerKind, container_id: int) -> SteerQueue | None:
    return _QUEUES.get((kind, container_id))


def close_queue(kind: ContainerKind, container_id: int, queue: SteerQueue) -> None:
    """Stop accepting messages; anything still waiting is dropped.

    The client knows which of its messages were echoed back, and puts the
    others back in the composer.
    """
    queue.closed = True
    queue.items.clear()
    if _QUEUES.get((kind, container_id)) is queue:
        del _QUEUES[(kind, container_id)]


def enqueue(kind: ContainerKind, container_id: int, content: str) -> SteerItem | None:
    """Queue ``content`` for the running turn; ``None`` when no turn takes it."""
    queue = _QUEUES.get((kind, container_id))
    if queue is None or queue.closed:
        return None
    item = SteerItem(id=secrets.token_hex(8), content=content)
    queue.items.append(item)
    return item
