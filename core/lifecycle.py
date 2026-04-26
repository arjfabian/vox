"""
VOX Agent Lifecycle
Internal Name: THE STATE MACHINE

Defines the operational states an Agent can occupy and the
pending-event queue used during PAUSED state (master offline).

State transitions:
  BOOTING  → ACTIVE   : successful _bootstrap() + boot()
  ACTIVE   → PAUSED   : master sent master_offline event
  PAUSED   → ACTIVE   : master sent master_online event (resume)
  ACTIVE   → STOPPED  : explicit stop_agent() call
  *        → FAILED   : unrecoverable provisioning error
"""

import asyncio
from collections import deque
from enum import Enum, auto
from typing import Any, Dict, List, NamedTuple


class AgentState(Enum):
    BOOTING = auto()
    ACTIVE  = auto()
    PAUSED  = auto()
    STOPPED = auto()
    FAILED  = auto()

    def __str__(self) -> str:
        return self.name


class PendingEvent(NamedTuple):
    """A snapshot of an emit() call held during PAUSED state."""
    event_name: str
    kwargs:     Dict[str, Any]


class EventQueue:
    """
    Bounded FIFO queue for events received while an Agent is paused.

    Enforces a hard cap (default 256) to prevent unbounded memory growth
    during extended master outages. Events beyond the cap are dropped and
    logged as WARN — they are never silently discarded.
    """

    DEFAULT_CAPACITY = 256

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._cap:   int               = capacity
        self._queue: deque[PendingEvent] = deque()
        self._dropped: int             = 0

    def enqueue(self, event_name: str, kwargs: Dict[str, Any]) -> bool:
        """
        Attempt to enqueue an event.
        :return: True if enqueued, False if dropped due to capacity.
        """
        if len(self._queue) >= self._cap:
            self._dropped += 1
            return False
        self._queue.append(PendingEvent(event_name, kwargs))
        return True

    def drain(self) -> List[PendingEvent]:
        """Returns all pending events in FIFO order and clears the queue."""
        events = list(self._queue)
        self._queue.clear()
        return events

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def dropped(self) -> int:
        return self._dropped