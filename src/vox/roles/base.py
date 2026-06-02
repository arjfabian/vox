"""
core/roles/base.py — VOX Role contract (v2)

Roles are explicit behavioral modules attached to an agent.
They do NOT self-discover; they declare capabilities.
"""

import weakref
from typing import Any, Awaitable, Callable, Dict


class AgentHostDeadError(Exception):
    pass


class VOXRole:
    """
    Explicit role contract.

    Responsibilities:
    - Handle events
    - Expose command handlers explicitly
    - Access agent via safe weakref
    """

    def __init__(self, agent: Any) -> None:
        self._agent_ref = weakref.ref(agent)
        self._handlers: Dict[str, Callable[..., Awaitable[None]]] = {}

    @property
    def agent(self) -> Any:
        inst = self._agent_ref()
        if inst is None:
            raise AgentHostDeadError("Agent host is gone.")
        return inst

    def on(self, event: str):
        def decorator(fn):
            self._handlers[event] = fn
            return fn
        return decorator

    async def handle_event(self, event: str, **kwargs) -> bool:
        handler = self._handlers.get(event)
        if not handler:
            return False
        await handler(**kwargs)
        return True

    def get_event_map(self) -> Dict[str, Callable]:
        return dict(self._handlers)
