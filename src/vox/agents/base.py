"""VOXAgent — declarative execution unit.

Agent = configuration + mounted capabilities + explicit roles.
No auto-discovery, no implicit filesystem magic.
"""

import asyncio
import importlib
import uuid
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import dotenv_values

from vox.observability import VOXForensicLogger, VOXLogSource
from vox.agents.lifecycle import AgentState, EventQueue
from vox.agents.memory import VOXAgentMemory
from vox.agents.store import VOXAgentStore
from vox.roles import VOXRole


RATE_LIMIT_MAX_CALLS = 100
RATE_LIMIT_WINDOW = 60


class AgentProvisionError(Exception):
    pass


class VOXAgent:

    def __init__(
        self,
        agent_dir: Path,
        logger: VOXForensicLogger,
        orchestrator: Any = None,
    ) -> None:
        self.dir = agent_dir
        self.orchestrator = orchestrator
        self.logger = logger

        self.config: Dict[str, Any] = {}
        self.roles: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.event_router: dict[str, list[Any]] = {}

        self.commands: set[str] = set()
        self.events: set[str] = set()

        self._state = AgentState.BOOTING
        self._tasks: List[asyncio.Task] = []
        self._event_queue = EventQueue()

        self.master: Optional[VOXAgent]
        self.children: Dict[str, VOXAgent]

        self.memory = VOXAgentMemory(self.dir)
        self.store = VOXAgentStore(self.dir)

        self._bootstrap()
        self._state = AgentState.STOPPED

    @property
    def name(self) -> str:
        return self.config.get("name") or "UnknownAgent"

    @property
    def id(self) -> str:
        return self.config.get("id")

    @property
    def master_id(self) -> Optional[str]:
        return self.config.get("master_id")

    @property
    def must_start(self) -> bool:
        return self.config.get("autostart", False)

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == AgentState.ACTIVE

    @property
    def is_paused(self) -> bool:
        return self._state == AgentState.PAUSED

    @state.setter
    def state(self, value: AgentState) -> None:
        self._state = value

    @property
    def log_source(self) -> VOXLogSource:
        return VOXLogSource(
            source_type="agent",
            source_name=self.name.lower(),
            source_uuid=self.id,
        )

    def log_agent_info(self, msg: str) -> None:
        self.logger.info(msg, extra={"vox_source": self.log_source})

    def log_agent_ok(self, msg: str) -> None:
        self.logger.ok(msg, extra={"vox_source": self.log_source})

    def log_agent_warning(self, msg: str) -> None:
        self.logger.warning(msg, extra={"vox_source": self.log_source})

    def log_agent_error(self, msg: str) -> None:
        self.logger.error(msg, extra={"vox_source": self.log_source})

    def health_check(self) -> bool:
        return len(self.roles) > 0

    def get_all_commands(self) -> list[str]:
        return sorted(self.commands)

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.name,
            "master_id": self.master_id,
            "autostart": self.must_start,
            "health": self.health_check(),
            "capabilities": list(self.capabilities.keys()),
            "commands": sorted(self.commands),
            "events": sorted(self.events),
        }

    def get_public_surface(self) -> Dict[str, str]:
        combined: Dict[str, str] = {}
        for role in self.roles.values():
            capabilities = role.get_capabilities()
            public = {
                name: handler
                for name, handler in capabilities.items()
                if name.startswith("cmd_")
            }
            combined.update(public)
        return combined

    def get_safe_path(self, sub_dir: str, filename: str) -> Path:
        sandbox_root = (self.dir / "assets").resolve()
        target_dir = (sandbox_root / sub_dir).resolve()
        clean_name = filename.replace("/", "")
        final_path = (target_dir / clean_name).resolve()
        if not str(final_path).startswith(str(sandbox_root)):
            raise PermissionError("Sandbox escape attempt")
        return final_path

    def _bootstrap(self) -> None:
        if not self._load_manifest():
            raise AgentProvisionError("Missing or invalid agent.yml")
        self.logger = self.logger.get_child(self.name.lower())
        if not self._load_env():
            self.logger.warning(f"[{self.dir.name}] No .env found (optional)")
        if not self._validate_identity(["name", "id"]):
            raise AgentProvisionError("Invalid agent identity")
        self.logger.ok(f"Identity verified: {self.id}")
        self._mount_capabilities()
        self._load_roles()
        if not self.roles:
            raise AgentProvisionError("No roles loaded")
        self.logger.ok("Agent ready")

    def _load_manifest(self) -> bool:
        manifest_path = self.dir / "agent.yml"
        if not manifest_path.exists():
            self.logger.error(f"Missing agent.yml in {self.dir}")
            return False
        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}
            self.config.update({
                "name": data.get("name"),
                "id": data.get("id"),
                "master_id": data.get("master_id"),
                "autostart": data.get("autostart", False),
                "capabilities": data.get("capabilities", []),
                "roles": data.get("roles", []),
                "personality": data.get("personality", {}),
            })
            return True
        except Exception as e:
            self.logger.error(f"Manifest load error: {e}")
            return False

    def _load_env(self) -> bool:
        dotenv_path = self.dir / ".env"
        if not dotenv_path.exists():
            return False
        try:
            env_data = dotenv_values(dotenv_path)
            self.config.update(env_data)
            return True
        except Exception as e:
            self.logger.error(f"Env load error: {e}")
            return False

    def _validate_identity(self, required: List[str]) -> bool:
        missing = [k for k in required if not self.config.get(k)]
        for m in missing:
            self.logger.error(f"Missing identity field: {m}")
        return len(missing) == 0

    def _mount_capabilities(self) -> None:
        self.logger.info("Mounting capabilities")
        for cap_id in self.config.get("capabilities", []):
            self.logger.info(f"Attempting to load capability '{cap_id}'")
            cap = self.orchestrator._get_capability_instance(cap_id)
            if not cap:
                self.logger.error(f"Missing capability: {cap_id}")
                continue
            bound = cap.mount(self, self.config)
            bound.logger = self.logger
            short = cap_id.split(".")[-1]
            self.capabilities[short] = bound
            setattr(self, f"cap_{short}", bound)
            self.logger.ok(f"Mounted capability: {cap_id}")

    def _load_roles(self) -> None:
        roles_dir = self.dir / "roles"
        if not roles_dir.exists():
            self.logger.warning(f"No roles directory found for {self.name}")
            return
        role_files = [
            file
            for file in roles_dir.glob("*.py")
            if not file.name.startswith("_") and not file.name.endswith("_new.py")
        ]
        if not role_files:
            self.logger.warning(f"No role modules found for {self.name}")
            return
        for role_file in role_files:
            role_name = role_file.stem
            try:
                module = importlib.import_module(
                    f"vox.agents.{self.name.lower()}.roles.{role_name}",
                )
                role_class = next(
                    (obj for obj in module.__dict__.values()
                     if isinstance(obj, type)
                     and issubclass(obj, VOXRole)
                     and obj is not VOXRole),
                    None,
                )
                if role_class is None:
                    raise ImportError(f"No VOXRole subclass found in {role_name}")
                role = role_class(self)
                self.roles[role_name] = role
                setattr(self, role_name, role)
                self._register_role_routes(role)
                self.logger.ok(f"Loaded role: {role_name}")
            except Exception as exc:
                self.logger.error(f"Role load failed [{role_name}]: {exc}")

    def _register_role_routes(self, role: Any) -> None:
        handlers = getattr(role, "_handlers", {})
        for route_name in handlers.keys():
            is_command = route_name.startswith("cmd_")
            if is_command:
                self.commands.add(route_name)
            else:
                self.events.add(route_name)
            if route_name not in self.event_router:
                self.event_router[route_name] = []
            if role not in self.event_router[route_name]:
                self.event_router[route_name].append(role)
                self.logger.info(f"Route registered: {route_name}")

    async def boot(self) -> bool:
        if self._state == AgentState.ACTIVE:
            return True
        self._state = AgentState.BOOTING
        self.logger.info("Booting agent...")
        ok = True
        for cap in self.capabilities.values():
            try:
                await cap.boot()
            except Exception as e:
                self.logger.error(f"Capability boot failed [{cap}]: {e}")
                ok = False
        if not ok:
            self._state = AgentState.FAILED
            self.logger.error("Agent boot FAILED — one or more capabilities failed")
            return False
        self._state = AgentState.ACTIVE
        await self.emit("on_boot")
        self.logger.ok("Agent ACTIVE")
        return True

    def pause(self) -> None:
        if self._state == AgentState.ACTIVE:
            self._state = AgentState.PAUSED
            self.logger.warning("Agent paused")

    def resume(self) -> None:
        if self._state != AgentState.PAUSED:
            return
        self._state = AgentState.ACTIVE
        pending = self._event_queue.drain()
        self.logger.ok(f"Resuming with {len(pending)} queued events")

        async def _drain():
            for evt in pending:
                await self.emit(evt.event_name, **evt.kwargs)

        task = asyncio.create_task(_drain())
        self._tasks.append(task)

    async def shutdown(self) -> None:
        self._state = AgentState.STOPPED
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self.roles.clear()
        self.capabilities.clear()
        self.logger.warning("Agent shutdown complete")

    async def emit(self, event_name: str, **kwargs) -> None:
        if self._state == AgentState.PAUSED:
            self._event_queue.enqueue(event_name, kwargs)
            return
        if self._state != AgentState.ACTIVE:
            return
        targets = self.event_router.get(event_name, [])
        if not targets:
            return
        for role in targets:
            try:
                await role.handle_event(event_name, **kwargs)
            except Exception as exc:
                self.logger.error(
                    f"Role dispatch failure [{event_name}]: {exc}"
                )
