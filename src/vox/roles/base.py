"""
core/roles/base.py — VOX Role contract (v2)

Roles are explicit behavioral modules attached to a workload.
They do NOT self-discover; they declare capabilities.
"""

import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Command marker — used by the @command decorator
# ---------------------------------------------------------------------------

_COMMAND_MARKER = "_vox_command_meta"


def command(name: str, description: str = "", **metadata):
    """
    Decorator that marks a method as an explicit command handler.

    The handler is routed under the clean command name (no prefix).
    Usage::

        class MyRole(VOXRole):
            @command("do_thing", description="Does a thing")
            async def do_thing(self, ...): ...
    """

    def decorator(fn):
        fn._vox_command_meta = (name, description, metadata)
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CommandInfo:
    """Metadata for an explicitly registered command handler."""

    handler: Callable[..., Awaitable[Any]]
    description: str = ""
    params: dict[str, str] = field(default_factory=dict)
    sample_prompts: list | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorkloadHostDeadError(Exception):
    pass


# ---------------------------------------------------------------------------
# VOXRole
# ---------------------------------------------------------------------------


class VOXRole:
    """
    Explicit role contract.

    Responsibilities:
    - Handle events
    - Expose command handlers explicitly via ``@command`` decorator
    - Access workload via safe weakref

    Class variables:
      REQUIRES: set[str] — capability IDs this role must be mounted with.
      PREFERRED_MODEL: str | None — which Ollama model this role prefers,
          or None to use the workload's default.
    """

    REQUIRES: set[str] = set()  # noqa: RUF012
    PREFERRED_MODEL: str | None = None

    def __init__(self, workload: Any) -> None:
        self._workload_ref = weakref.ref(workload)
        self._handlers: dict[str, Callable[..., Awaitable[None]]] = {}
        self._commands: dict[str, CommandInfo] = {}
        self._discover_commands()

    def _discover_commands(self) -> None:
        """Scans the class hierarchy for ``@command``-decorated methods."""
        for attr_name in dir(self.__class__):
            attr = self.__class__.__dict__.get(attr_name)
            meta = getattr(attr, _COMMAND_MARKER, None)
            if meta:
                name, description, metadata = meta
                bound = getattr(self, attr_name)
                self._handlers[name] = bound
                self._commands[name] = CommandInfo(
                    handler=bound,
                    description=description,
                    **metadata,
                )

    @property
    def workload(self) -> Any:
        inst = self._workload_ref()
        if inst is None:
            raise WorkloadHostDeadError("Workload host is gone.")
        return inst

    def on(self, event: str):
        """
        Register an event handler.

        Use this for non-command events (``on_boot``, ``inbound_message``,
        etc.). For command handlers prefer the ``@command`` decorator.
        """

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

    def get_commands(self) -> dict[str, CommandInfo]:
        """Returns {command_name: CommandInfo} for all registered commands."""
        return dict(self._commands)
