"""Capability system — core abstraction layer.

Defines the interface and runtime binding model for all VOX capabilities.
A capability is split into:
  VOXCapability        — stateless global definition (shared across agents)
  VOXBoundCapability   — per-agent runtime proxy with resolved configuration
"""

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vox.agents.base import VOXAgent
    from vox.observability import VOXForensicLogger


EXPLAIN_WIDTH = 60
PARAM_COL_WIDTH = 22
DESC_COL_WIDTH = 35


class VOXCapability:
    CAPABILITY_NAME: str = ""
    # noqa: RUF012 — mutable defaults are intentional; subclasses override per-agent,
    # and ClassVar would prevent per-instance overrides (e.g. Telegram vs WhatsApp params).
    PARAMS: dict[str, list[Any]] = {}
    SENSITIVE_PARAMS: set[str] = set()

    id: str
    logger: "VOXForensicLogger"

    @property
    def name(self) -> str:
        return self.CAPABILITY_NAME or self.__class__.__name__

    @classmethod
    def get_params(cls) -> list[str]:
        return list(cls.PARAMS.keys())

    @classmethod
    def explain_config(cls) -> str:
        header = f" Requirements for '{cls.__name__}'"
        lines = [f"\n{header:=^{EXPLAIN_WIDTH}}"]
        for param, (desc, default) in cls.PARAMS.items():
            status = f"[Default: {default}]" if default is not None else "[REQUIRED]"
            lines.append(
                f"  • {param:<{PARAM_COL_WIDTH}} | {desc:<{DESC_COL_WIDTH}} {status}"
            )
        lines.append("=" * EXPLAIN_WIDTH)
        return "\n".join(lines)

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def mount(
        self,
        agent: "VOXAgent",
        config: dict[str, Any],
    ) -> "VOXBoundCapability":
        extracted: dict[str, Any] = {}
        for param, (_, default) in self.PARAMS.items():
            extracted[param] = (
                config.get(param) if config.get(param) is not None else default
            )
        return VOXBoundCapability(self, agent, extracted)


class VOXBoundCapability:
    def __init__(
        self,
        capability: VOXCapability,
        agent: "VOXAgent",
        params: dict[str, Any],
    ) -> None:
        object.__setattr__(self, "_capability", capability)
        object.__setattr__(self, "_agent", agent)
        object.__setattr__(self, "_params", params)
        object.__setattr__(self, "_instance_attrs", {})
        object.__setattr__(self, "_frozen", False)

    def log(self, message: str) -> None:
        self.logger.info(f"[{self.name}] {message}")

    def warning(self, message: str) -> None:
        self.logger.warning(f"[{self.name}] {message}")

    def error(self, message: str) -> None:
        self.logger.error(f"[{self.name}] {message}")

    def ok(self, message: str) -> None:
        self.logger.ok(f"[{self.name}] {message}")

    def __setattr__(self, name: str, value: Any) -> None:
        if self._frozen and not name.startswith("_"):
            raise AttributeError("BoundCapability is frozen")
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._instance_attrs[name] = value

    def __getattr__(self, name: str) -> Any:
        if name in self._instance_attrs:
            return self._instance_attrs[name]
        if name in self._params:
            return self._params[name]
        try:
            attr = getattr(self._capability, name)
        except AttributeError:
            raise AttributeError(
                f"Capability [{self._capability.name}] has no attribute '{name}'"
            )
        if not callable(attr):
            return attr
        raw = getattr(type(self._capability), name, None)
        if raw is None:
            return attr
        if inspect.iscoroutinefunction(raw):

            async def _wrapper(*args: Any, **kwargs: Any) -> Any:
                return await raw(self, *args, **kwargs)
        else:

            def _wrapper(*args: Any, **kwargs: Any) -> Any:
                return raw(self, *args, **kwargs)

        return _wrapper

    def get_safe_path(self, sub_dir: str, filename: str) -> "Path":
        return self._agent.get_safe_path(sub_dir, filename)

    async def emit(self, event_name: str, **kwargs) -> None:
        orch = self._agent.orchestrator
        if orch:
            await orch.dispatch_inbound_message(
                source=self._capability.CAPABILITY_NAME,
                payload=kwargs,
            )
        else:
            await self._agent.emit(event_name, **kwargs)

    def get_capability(self, cap_id: str) -> object | None:
        return self._agent.capabilities.get(cap_id)

    def validate_params(self) -> list[str]:
        return [
            param
            for param, (_, default) in self._capability.PARAMS.items()
            if self._params.get(param) is None and default is None
        ]

    async def initialize(self) -> None:
        missing = self.validate_params()
        if missing:
            raise ValueError(f"Missing required params for {self.name}: {missing}")
        self.ok("Capability initialized")

    def freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)
