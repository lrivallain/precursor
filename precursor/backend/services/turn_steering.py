"""Messages the user sends while a topic or chat turn is still running.

Each running turn holds its own :class:`SteerQueue`, under a ``turn_id`` its
stream announces first (a ``turn`` event). A message is addressed to that id,
so with two turns running in one conversation (two windows) each goes to the
turn its window started. It waits in the queue until the tool loop takes it at
the next round boundary: after the round's tool results, or after a final
answer, when the turn carries on instead of ending.

Once the loop has taken an empty queue at the end of the turn, or has no round
left to give it, the queue is closed: a message sent after that is refused
(``enqueue`` returns ``None``) and the client sends it as the next turn instead.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
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
    kind: ContainerKind
    container_id: int
    turn_id: str = field(default_factory=lambda: secrets.token_hex(8))
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

    def close(self) -> None:
        """Refuse new messages; any still waiting stay unread for the client."""
        self.closed = True


_QUEUES: dict[str, SteerQueue] = {}

# The queue of the turn running in this task (set by ``turn_lifecycle``).
_current: ContextVar[SteerQueue | None] = ContextVar("precursor_steer_queue", default=None)


def open_queue(kind: ContainerKind, container_id: int) -> SteerQueue:
    """Start accepting messages for a turn about to run in this container."""
    queue = SteerQueue(kind, container_id)
    _QUEUES[queue.turn_id] = queue
    return queue


def bind_current(queue: SteerQueue) -> None:
    """Make ``queue`` the running turn's own, for the task that runs it."""
    _current.set(queue)


def current_queue() -> SteerQueue | None:
    return _current.get()


def close_queue(queue: SteerQueue) -> None:
    """Stop accepting messages; anything still waiting is dropped.

    The client knows which of its messages were echoed back, and puts the
    others back in the composer.
    """
    queue.closed = True
    queue.items.clear()
    _QUEUES.pop(queue.turn_id, None)


def enqueue(kind: ContainerKind, container_id: int, turn_id: str, content: str) -> SteerItem | None:
    """Queue ``content`` for turn ``turn_id``; ``None`` when that turn takes no more."""
    queue = _QUEUES.get(turn_id)
    if queue is None or queue.closed or (queue.kind, queue.container_id) != (kind, container_id):
        return None
    item = SteerItem(id=secrets.token_hex(8), content=content)
    queue.items.append(item)
    return item
