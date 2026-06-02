"""Capability system — core abstraction layer.

Defines the interface and runtime binding model for all VOX capabilities.
A capability is split into:
  VOXCapability        — stateless global definition (shared across agents)
  VOXBoundCapability   — per-agent runtime proxy with resolved configuration
"""

import inspect
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from vox.agents.base import VOXAgent


EXPLAIN_WIDTH   = 60
PARAM_COL_WIDTH = 22
DESC_COL_WIDTH  = 35


class VOXCapability:

    CAPABILITY_NAME: str = ""
    PARAMS: Dict[str, List[Any]] = {}

    id: str
    logger: "VOXForensicLogger"

    @property
    def name(self) -> str:
        return self.CAPABILITY_NAME or self.__class__.__name__

    @classmethod
    def get_params(cls) -> List[str]:
        return list(cls.PARAMS.keys())

    @classmethod
    def explain_config(cls) -> str:
        header = f" Requirements for '{cls.__name__}'"
        lines  = [f"\n{header:=^{EXPLAIN_WIDTH}}"]
        for param, (desc, default) in cls.PARAMS.items():
            status = f"[Default: {default}]" if default is not None else "[REQUIRED]"
            lines.append(
                f"  • {param:<{PARAM_COL_WIDTH}} | {desc:<{DESC_COL_WIDTH}} {status}"
            )
        lines.append("=" * EXPLAIN_WIDTH)
        return "\n".join(lines)

    @classmethod
    def validate_and_extract(
        cls,
        config: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        extracted: Dict[str, Any] = {}
        missing:   List[str]      = []
        for param, (_, default) in cls.PARAMS.items():
            value = config.get(param)
            if value is None and default is None:
                missing.append(param)
            extracted[param] = value if value is not None else default
        return extracted, missing

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def run(
        self,
        bound: "VOXBoundCapability",
        tool: str,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError(
            f"{self.name} does not implement tool execution"
        )

    def mount(
        self,
        agent: "VOXAgent",
        config: Dict[str, Any],
    ) -> "VOXBoundCapability":
        sanitized, missing = self.validate_and_extract(config)
        if missing:
            raise ValueError(f"Missing params for {self.name}: {missing}")
        return VOXBoundCapability(self, agent, sanitized)


class VOXBoundCapability:

    def __init__(
        self,
        capability: VOXCapability,
        agent: "VOXAgent",
        params: Dict[str, Any],
    ) -> None:
        object.__setattr__(self, "_capability",     capability)
        object.__setattr__(self, "_agent",          agent)
        object.__setattr__(self, "_params",         params)
        object.__setattr__(self, "_instance_attrs", {})
        object.__setattr__(self, "_frozen",         False)

    def log(self, message: str) -> None:
        self.logger.info(f"[{self.name}] {message}")

    def warning(self, message: str) -> None:
        self.logger.warning(f"[{self.name}] {message}")

    def error(self, message: str) -> None:
        self.logger.error(f"[{self.name}] {message}")

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

    async def run(self, tool: str, **kwargs: Any) -> Any:
        return await self._capability.run(self, tool, **kwargs)

    def freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)
