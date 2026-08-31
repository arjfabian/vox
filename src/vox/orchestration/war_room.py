"""VOXWarRoom — async Pub/Sub incident notification system.

Provides an asynchronous in-memory alert queue (VOXWarRoom) and a reactive
dispatcher (VOXWarRoomMaster) that fans out alerts to active workloads and
mirrors them to an external messenger channel.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from vox.messaging.models import VOXMessage

if TYPE_CHECKING:
    from vox.orchestration.base import VOXOrchestrator


# ------------------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------------------


@dataclass
class WarRoomMessage:
    """An alert incident tracked by the war room."""

    message_id: str = field(default_factory=lambda: uuid4().hex)
    source: str = ""
    target: str = "everyone"
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    read_by: dict[str, str] = field(default_factory=dict)

    def to_vox_message(self) -> VOXMessage:
        """Convert to the standard VOXMessage envelope."""
        from uuid import UUID

        return VOXMessage(
            message_id=UUID(self.message_id),
            message_source=self.source,
            emitted_at=self.emitted_at,
            source=UUID(int=0),
            target=UUID(int=0),
            type=self.payload.get("event", "alert"),
            details=self.payload,
        )


# ------------------------------------------------------------------------------
# War Room
# ------------------------------------------------------------------------------


class VOXWarRoom:
    """Async in-memory incident queue with read tracking and history."""

    def __init__(self, max_queue: int = 1024) -> None:
        self._queue: asyncio.Queue[WarRoomMessage] = asyncio.Queue(maxsize=max_queue)
        self._history: list[WarRoomMessage] = []
        self._lock: asyncio.Lock = asyncio.Lock()

    async def publish(self, message: WarRoomMessage) -> None:
        async with self._lock:
            self._history.append(message)
        await self._queue.put(message)

    async def acknowledge(self, message_id: str, workload_name: str) -> bool:
        """Mark an alert as read; True if the message was found."""
        async with self._lock:
            for msg in self._history:
                if msg.message_id == message_id:
                    msg.read_by[workload_name] = datetime.now(timezone.utc).isoformat()
                    return True
            return False

    def get_unread(self, workload_name: str) -> list[WarRoomMessage]:
        return [msg for msg in self._history if workload_name not in msg.read_by]

    def get_history(
        self,
        since: str | None = None,
    ) -> list[WarRoomMessage]:
        if since is None:
            return list(self._history)
        return [msg for msg in self._history if msg.emitted_at >= since]

    def flush_to_disk(self) -> None:
        """Drain remaining queued alerts into history (panic path).

        Persistence to the external channel is the messenger's responsibility;
        this only guarantees no alert is lost in memory.
        """
        while not self._queue.empty():
            try:
                msg = self._queue.get_nowait()
                self._history.append(msg)
            except asyncio.QueueEmpty:
                break


# ------------------------------------------------------------------------------
# Master Dispatcher
# ------------------------------------------------------------------------------


class VOXWarRoomMaster:
    """Reactive dispatcher for war room alerts.

    Runs a background task that drains the war room queue, fans out alerts to
    all active workloads, and mirrors them to an external channel via a
    plain-text broadcast callback.
    """

    def __init__(
        self,
        war_room: VOXWarRoom,
        broadcast_fn: Callable[[str], Awaitable[bool]],
        orchestrator: VOXOrchestrator,
    ) -> None:
        self._war_room = war_room
        self._broadcast = broadcast_fn
        self._orc = orchestrator
        self._dispatch_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self._war_room._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._fanout_to_workloads(msg)
            except Exception:  # noqa: BLE001, S110 — fanout must not block
                pass

            try:
                text = self._format_alert(msg)
                await self._broadcast(text)
            except Exception:  # noqa: BLE001, S110 — broadcast must not block
                pass

    @staticmethod
    def _format_alert(msg: WarRoomMessage) -> str:
        return (
            f"\U0001f514 Alert: {msg.payload.get('event', 'alert')} "
            f"from {msg.source}\n\n"
            f"{json.dumps(msg.payload, indent=2, default=str)}"
        )

    async def _fanout_to_workloads(self, msg: WarRoomMessage) -> None:
        workloads = list(self._orc.active_workloads.values())
        for workload in workloads:
            try:
                asyncio.create_task(workload.emit("on_war_room_alert", message=msg))
            except Exception:  # noqa: BLE001, S112 — resilient fanout
                continue
