"""Agent execution state machine and event buffering.

This module defines the runtime execution semantics of a VOX agent:
- Its lifecycle states (BOOTING, ACTIVE, PAUSED, STOPPED, FAILED)
- A bounded event buffer used during PAUSED state
"""

from __future__ import annotations

from collections import deque
from enum import Enum, auto
from typing import Any, NamedTuple

_DEFAULT_EVENT_QUEUE_CAPACITY = 256


class AgentState(Enum):
    BOOTING = auto()
    IDLE = auto()
    ACTIVE = auto()
    PAUSING = auto()
    PAUSED = auto()
    RESUMING = auto()
    STOPPING = auto()
    STOPPED = auto()
    FAILED = auto()

    def __str__(self) -> str:
        return self.name

    def can_transition_to(self, target: AgentState) -> bool:
        return _TRANSITIONS.get(self, set()).__contains__(target)


_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.BOOTING: {
        AgentState.IDLE,
        AgentState.ACTIVE,
        AgentState.FAILED,
        AgentState.STOPPING,
    },
    AgentState.IDLE: {AgentState.BOOTING, AgentState.STOPPING},
    AgentState.ACTIVE: {AgentState.PAUSING, AgentState.STOPPING},
    AgentState.PAUSING: {AgentState.PAUSED, AgentState.STOPPING},
    AgentState.PAUSED: {AgentState.RESUMING, AgentState.STOPPING},
    AgentState.RESUMING: {AgentState.ACTIVE, AgentState.STOPPING},
    AgentState.STOPPING: {AgentState.STOPPED},
    AgentState.STOPPED: {AgentState.BOOTING},
    AgentState.FAILED: {AgentState.BOOTING},
}


class PendingEvent(NamedTuple):
    event_name: str
    kwargs: dict[str, Any]


class EventQueue:
    def __init__(self, capacity: int = _DEFAULT_EVENT_QUEUE_CAPACITY) -> None:
        self._capacity: int = capacity
        self._queue: deque[PendingEvent] = deque()
        self._dropped_count: int = 0

    def enqueue(self, event_name: str, kwargs: dict[str, Any]) -> bool:
        if len(self._queue) >= self._capacity:
            self._dropped_count += 1
            return False
        self._queue.append(PendingEvent(event_name, kwargs))
        return True

    def drain(self) -> list[PendingEvent]:
        events = list(self._queue)
        self._queue.clear()
        return events

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def dropped(self) -> int:
        return self._dropped_count
