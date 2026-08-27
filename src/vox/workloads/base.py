"""VOXWorkload — declarative execution unit.

Workload = configuration + mounted capabilities + explicit roles.
No auto-discovery, no implicit filesystem magic.
"""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vox.observability import VOXForensicLogger, VOXLogSource
from vox.provider import CapabilityProviderProtocol
from vox.roles import CommandInfo, VOXRole
from vox.security import RateLimiter, RateLimitError
from vox.security.vault import VaultAccessError
from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer
from vox.workloads.capability_binder import CapabilityBinder
from vox.workloads.lifecycle import EventQueue, WorkloadState
from vox.workloads.loader import (  # noqa: F401 — re-exported for public API
    WorkloadLoader,
    WorkloadProvisionError,
)
from vox.workloads.memory import VOXWorkloadMemory
from vox.workloads.store import VOXWorkloadStore


class VOXWorkload:
    GLOBAL_WORKLOAD_KEYS: tuple[str, ...] = ("TELEGRAM_USER_ID",)

    def __init__(
        self,
        persona_dir: Path,
        logger: VOXForensicLogger,
        orchestrator: CapabilityProviderProtocol | None = None,
        global_env: dict[str, Any] | None = None,
    ) -> None:
        self.dir = persona_dir
        self._capability_provider = orchestrator
        self.logger = logger

        self.config: dict[str, Any] = {}
        self._global_env = global_env or {}
        self.roles: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.event_router: dict[str, list[Any]] = {}

        self.commands: set[str] = set()
        self.events: set[str] = set()
        self._capability_commands: dict[str, Callable] = {}
        self._system_capabilities: set[str] = {"comm.gateway"}

        self._state = WorkloadState.BOOTING
        self._tasks: list[asyncio.Task] = []
        self._event_queue = EventQueue()

        self.memory = VOXWorkloadMemory(self.dir)
        self.store = VOXWorkloadStore(self.dir)
        self._vault = None
        self._degraded = False

        self._loader = WorkloadLoader(self.dir, self.logger, self._global_env)
        self._ast_analyzer = ASTWorkloadAnalyzer()
        self._capability_binder = CapabilityBinder(self, self.logger)

        self._bootstrap()

        self._rate_limiter = RateLimiter(
            max_calls=self.config.get("rate_limit_max_calls", 30),
            window_seconds=self.config.get("rate_limit_window", 60),
        )

        self._state = WorkloadState.IDLE

    @property
    def orchestrator(self) -> CapabilityProviderProtocol | None:
        return self._capability_provider

    @property
    def name(self) -> str:
        return self.config.get("name") or "UnknownWorkload"

    @property
    def id(self) -> str:
        return self.config.get("id")

    @property
    def master_id(self) -> str | None:
        return self.config.get("master_id")

    @property
    def must_start(self) -> bool:
        return self.config.get("autostart", False)

    @property
    def conversational(self) -> bool:
        return self.config.get("conversational", False)

    @property
    def state(self) -> WorkloadState:
        return self._state

    @state.setter
    def state(self, value: WorkloadState) -> None:
        if self._state.can_transition_to(value):
            self._state = value
        else:
            raise RuntimeError(
                f"Invalid state transition: {self._state} \u2192 {value}"
            )

    @property
    def log_source(self) -> VOXLogSource:
        return VOXLogSource(
            source_type="workload",
            source_name=self.name.lower(),
            source_uuid=self.id,
        )

    def log_workload_info(self, message: str, *args, **kwargs):
        """Safe wrapper to preserve compatibility with existing role extensions."""
        if hasattr(self, "logger") and self.logger:
            self.logger.info(f"[{self.id}] {message}", *args, **kwargs)
        else:
            print(f"[{self.__class__.__name__}] {message}")

    @property
    def rate_limiter_utilization(self) -> float:
        return self._rate_limiter.get_utilization()

    def health_check(self) -> bool:
        return len(self.roles) > 0 and not self._degraded

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.name,
            "master_id": self.master_id,
            "autostart": self.must_start,
            "health": self.health_check(),
            "degraded": self._degraded,
            "rate_limiter_utilization_pct": round(self.rate_limiter_utilization, 1),
            "capabilities": list(self.capabilities.keys()),
            "commands": sorted(self.commands),
            "events": sorted(self.events),
            "subordinates": [
                child.describe()
                for child in self._capability_provider.get_children(self.id)
            ],
        }

    def get_command_map(self) -> dict[str, Any]:
        combined: dict[str, CommandInfo] = {}
        for role in self.roles.values():
            for name, info in role.get_commands().items():
                combined[name] = info
        return combined

    def get_safe_path(self, sub_dir: str, filename: str) -> Path:
        sandbox_root = (self.dir / "assets").resolve()
        target_dir = (sandbox_root / sub_dir).resolve()
        clean_name = filename.replace("/", "")
        final_path = (target_dir / clean_name).resolve()
        if not final_path.is_relative_to(sandbox_root):
            raise PermissionError("Sandbox escape attempt")
        return final_path

    # ------------------------------------------------------------------
    # Bootstrap — orchestrates loader, roles, and capability binder
    # ------------------------------------------------------------------

    def _bootstrap(self) -> None:
        config = self._loader.load_and_validate()
        self.config = config
        self.logger = self.logger.get_child(self.name.lower())
        self._capability_binder.logger = self.logger
        self._capability_binder.init_vault_sync()
        self._load_roles()
        self._capability_binder.discover_and_mount(
            self.dir / "roles", self._system_capabilities
        )
        if not self.roles:
            self.logger.warning(
                f"[{self.name}] Workload DEGRADED \u2014 No active roles available."
            )
            self._degraded = True
            return
        self.logger.ok("Workload ready")

    def _load_roles(self) -> None:
        import importlib.util

        roles_dir = self.dir / "roles"
        if not roles_dir.exists():
            self.logger.warning(f"No roles directory found for {self.name}")
            return
        role_files = [
            file for file in roles_dir.glob("*.py") if not file.name.startswith("_")
        ]
        if not role_files:
            self.logger.warning(f"No role modules found for {self.name}")
            return
        for role_file in role_files:
            role_name = role_file.stem
            try:
                spec_name = (
                    f"vox_runtime.personas.{self.name.lower()}.roles.{role_name}"
                )
                spec = importlib.util.spec_from_file_location(spec_name, role_file)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot create spec for {role_file}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec_name] = module
                spec.loader.exec_module(module)
                role_class = next(
                    (
                        obj
                        for obj in module.__dict__.values()
                        if isinstance(obj, type)
                        and issubclass(obj, VOXRole)
                        and obj is not VOXRole
                    ),
                    None,
                )
                if role_class is None:
                    raise ImportError(f"No VOXRole subclass found in {role_name}")
                role = role_class(self)
                self.roles[role_name] = role
                setattr(self, role_name, role)
                self._register_role_routes(role)
                self.logger.ok(f"Loaded role: {role_name}")
            except Exception as exc:  # noqa: BLE001 — defensive catch at role boundary
                self.logger.error(f"Role load failed [{role_name}]: {exc}")

    def _register_role_routes(self, role: Any) -> None:
        handlers = getattr(role, "_handlers", {})
        command_names = set(role.get_commands().keys())
        for route_name in handlers:
            is_command = route_name in command_names
            if is_command:
                self.commands.add(route_name)
            else:
                self.events.add(route_name)
            if route_name not in self.event_router:
                self.event_router[route_name] = []
            if role not in self.event_router[route_name]:
                self.event_router[route_name].append(role)
                self.logger.info(f"Route registered: {route_name}")

    # ------------------------------------------------------------------
    # Lifecycle — state transitions
    # ------------------------------------------------------------------

    async def boot(self) -> bool:
        if self._state == WorkloadState.ACTIVE:
            return True
        if not self._state.can_transition_to(WorkloadState.BOOTING):
            self.logger.error(f"Cannot boot from state {self._state}")
            return False
        self._state = WorkloadState.BOOTING
        self.logger.info("Booting workload...")

        await self._capability_binder.init_vault()
        try:
            await self._capability_binder.inject_vault_secrets()
        except VaultAccessError as e:
            self.logger.error(str(e))
            self._state = WorkloadState.FAILED
            return False

        await self.store.init_db()
        await self.memory.init_db()

        ok = True
        for cap in self.capabilities.values():
            try:
                await cap.initialize()
                await cap.boot()
            except Exception as e:  # noqa: BLE001 — defensive catch at capability boundary
                self.logger.error(f"Capability boot failed [{cap}]: {e}")
                ok = False
        if not ok:
            self._state = WorkloadState.FAILED
            self.logger.error(
                "Workload boot FAILED \u2014 one or more capabilities failed"
            )
            return False
        self._state = WorkloadState.ACTIVE
        await self.emit("on_boot")
        self.logger.ok("Workload ACTIVE")
        return True

    async def pause(self) -> None:
        if not self._state.can_transition_to(WorkloadState.PAUSING):
            self.logger.warning(f"Cannot pause from state {self._state}")
            return
        self._state = WorkloadState.PAUSING
        self.logger.warning("Workload pausing...")
        self._state = WorkloadState.PAUSED
        self.logger.warning("Workload paused")

    async def resume(self) -> None:
        if not self._state.can_transition_to(WorkloadState.RESUMING):
            self.logger.warning(f"Cannot resume from state {self._state}")
            return
        self._state = WorkloadState.RESUMING
        self.logger.info("Workload resuming...")
        pending = self._event_queue.drain()
        self._state = WorkloadState.ACTIVE
        self.logger.ok(f"Resumed with {len(pending)} queued events")
        for evt in pending:
            await self.emit(evt.event_name, **evt.kwargs)

    async def stop(self) -> None:
        if not self._state.can_transition_to(WorkloadState.STOPPING):
            self.logger.warning(f"Cannot stop from state {self._state}")
            return
        self._state = WorkloadState.STOPPING
        self.logger.warning("Workload stopping...")
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self._state = WorkloadState.STOPPED
        self.logger.warning("Workload stopped")

    async def shutdown(self) -> None:
        self._state = WorkloadState.STOPPING
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for cap in self.capabilities.values():
            try:
                await cap.shutdown()
            except Exception as e:  # noqa: BLE001 — defensive catch at shutdown boundary
                self.logger.error(f"Capability shutdown failed [{cap}]: {e}")
        self.roles.clear()
        self.capabilities.clear()
        self._state = WorkloadState.STOPPED
        self.logger.warning("Workload shutdown complete")

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def emit(self, event_name: str, **kwargs) -> None:
        if self._state in (WorkloadState.PAUSED, WorkloadState.PAUSING):
            self._event_queue.enqueue(event_name, kwargs)
            return
        if self._state != WorkloadState.ACTIVE:
            return

        try:
            self._rate_limiter.check_limit()
        except RateLimitError:
            self.logger.critical(
                "Rate limit exceeded for Workload %s \u2014 dropping event '%s'",
                self.name,
                event_name,
            )
            return

        targets = self.event_router.get(event_name, [])
        for role in targets:
            try:
                await role.handle_event(event_name, **kwargs)
            except Exception as exc:  # noqa: BLE001 — defensive catch at event dispatch
                self.logger.error(f"Role dispatch failure [{event_name}]: {exc}")
        handler = self._capability_commands.get(event_name)
        if handler:
            try:
                await handler(**kwargs)
            except Exception as exc:  # noqa: BLE001 — defensive catch at event dispatch
                self.logger.error(
                    f"Capability command dispatch failure [{event_name}]: {exc}"
                )
