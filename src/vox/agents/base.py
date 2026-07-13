"""VOXAgent — declarative execution unit.

Agent = configuration + mounted capabilities + explicit roles.
No auto-discovery, no implicit filesystem magic.
"""

import ast
import asyncio
import importlib
import yaml
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import dotenv_values

from vox.provider import CapabilityProviderProtocol
from vox.observability import VOXForensicLogger, VOXLogSource
from vox.agents.lifecycle import AgentState, EventQueue
from vox.agents.memory import VOXAgentMemory
from vox.agents.store import VOXAgentStore
from vox.roles import VOXRole
from vox.security import AgentVault


class AgentProvisionError(Exception):
    pass


class VOXAgent:

    def __init__(
        self,
        agent_dir: Path,
        logger: VOXForensicLogger,
        orchestrator: CapabilityProviderProtocol | None = None,
    ) -> None:
        self.dir = agent_dir
        self._capability_provider = orchestrator
        self.logger = logger

        self.config: Dict[str, Any] = {}
        self.roles: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.event_router: dict[str, list[Any]] = {}

        self.commands: set[str] = set()
        self.events: set[str] = set()
        self._capability_commands: Dict[str, Callable] = {}

        self._state = AgentState.BOOTING
        self._tasks: List[asyncio.Task] = []
        self._event_queue = EventQueue()

        self.memory = VOXAgentMemory(self.dir)
        self.store = VOXAgentStore(self.dir)
        self._vault: AgentVault | None = None
        self._degraded = False

        self._bootstrap()
        self._state = AgentState.IDLE

    @property
    def orchestrator(self) -> CapabilityProviderProtocol | None:
        return self._capability_provider

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
    def conversational(self) -> bool:
        return self.config.get("conversational", False)

    @property
    def state(self) -> AgentState:
        return self._state

    @state.setter
    def state(self, value: AgentState) -> None:
        if self._state.can_transition_to(value):
            self._state = value
        else:
            raise RuntimeError(
                f"Invalid state transition: {self._state} → {value}"
            )

    @property
    def log_source(self) -> VOXLogSource:
        return VOXLogSource(
            source_type="agent",
            source_name=self.name.lower(),
            source_uuid=self.id,
        )

    def log_agent_info(self, message: str, *args, **kwargs):
        """Safe wrapper to preserve compatibility with existing role extensions."""
        if hasattr(self, 'logger') and self.logger:
            self.logger.info(f"[{self.id}] {message}", *args, **kwargs)
        else:
            print(f"[{self.__class__.__name__}] {message}")

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
            "capabilities": list(self.capabilities.keys()),
            "commands": sorted(self.commands),
            "events": sorted(self.events),
            "subordinates": [
                child.describe()
                for child in self._capability_provider.get_children(self.id)
            ],
        }

    def get_command_map(self) -> Dict[str, Any]:
        combined: Dict[str, "CommandInfo"] = {}
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

    def _bootstrap(self) -> None:
        if not self._load_manifest():
            raise AgentProvisionError("Missing or invalid agent.yml")
        self.logger = self.logger.get_child(self.name.lower())
        if not self._load_env():
            self.logger.warning(f"[{self.dir.name}] No .env found (optional)")
        if not self._validate_identity(["name", "id"]):
            raise AgentProvisionError("Invalid agent identity")
        self.logger.ok(f"Identity verified: {self.id}")
        self._load_roles()
        self._discover_and_mount_capabilities()
        if not self.roles:
            self.logger.warning(
                f"[{self.name}] Agent DEGRADED — No active roles available."
            )
            self._degraded = True
            return
        self.logger.ok("Agent ready")

    MANIFEST_SCHEMA: Dict[str, tuple] = {
        "name":         (str,   True,  "Agent display name"),
        "id":           (str,   True,  "Unique agent UUID"),
        "master_id":    (str,   False, "Parent agent UUID (empty = root)"),
        "autostart":       (bool,  False, "Start on orchestrator boot"),
        "conversational":  (bool,  False, "Allow free-form LLM conversation"),
        "roles":        (list,  False, "Explicit role allow-list"),
        "personality":  (dict,  False, "Personality config dict"),
    }

    def _validate_manifest(self, data: dict) -> None:
        errors: list[str] = []
        warnings: list[str] = []
        for field, (expected_type, required, desc) in self.MANIFEST_SCHEMA.items():
            val = data.get(field)
            if val is None:
                if required:
                    errors.append(f"Missing required field '{field}' ({desc})")
                continue
            if not isinstance(val, expected_type):
                errors.append(
                    f"Field '{field}' must be {expected_type.__name__}, "
                    f"got {type(val).__name__}"
                )
        unknown = set(data.keys()) - set(self.MANIFEST_SCHEMA.keys())
        for field in sorted(unknown):
            warnings.append(f"Unknown field '{field}' in agent.yml")
        if errors:
            raise AgentProvisionError(
                f"Manifest validation failed for {self.dir.name}: " +
                "; ".join(errors)
            )
        for w in warnings:
            self.logger.warning(f"[manifest] {w}")

    def _load_manifest(self) -> bool:
        manifest_path = self.dir / "agent.yml"
        if not manifest_path.exists():
            self.logger.error(f"Missing agent.yml in {self.dir}")
            return False
        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}
            self._validate_manifest(data)
            self.config.update({
                "name": data.get("name"),
                "id": data.get("id"),
                "master_id": data.get("master_id"),
                "autostart": data.get("autostart", False),
                "roles": data.get("roles", []),
                "personality": data.get("personality", {}),
            })
            return True
        except AgentProvisionError:
            raise
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

    # ------------------------------------------------------------------
    # Capability discovery — AST-based role scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _is_capabilities_chain(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "capabilities":
            inner = node.value
            return isinstance(inner, ast.Attribute) or isinstance(inner, ast.Name)
        return False

    @staticmethod
    def _scan_role_capabilities(role_file: Path) -> set[str]:
        cap_ids: set[str] = set()
        try:
            with open(role_file) as f:
                tree = ast.parse(f.read())
        except SyntaxError:
            return cap_ids
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == "REQUIRES":
                    if isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                cap_ids.add(elt.value)
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                slice_val = node.slice
                if isinstance(slice_val, ast.Constant) and isinstance(slice_val.value, str):
                    if VOXAgent._is_capabilities_chain(node.value):
                        cap_ids.add(slice_val.value)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "get":
                    if VOXAgent._is_capabilities_chain(func.value):
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            cap_ids.add(node.args[0].value)
        return cap_ids

    def _discover_and_mount_capabilities(self) -> None:
        needed: set[str] = set()
        roles_dir = self.dir / "roles"
        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue
                needed |= self._scan_role_capabilities(role_file)
        if not needed:
            return
        self.logger.info(f"Capabilities required by roles: {sorted(needed)}")
        for cap_id in sorted(needed):
            cap = self._capability_provider.get_capability_instance(cap_id)
            if not cap:
                self.logger.warning(
                    f"Security warning: capability '{cap_id}' not found — "
                    f"roles depending on it will be disabled"
                )
                self._degraded = True
                continue
            bound = cap.mount(self, self.config)
            bound.logger = self.logger
            self.capabilities[cap_id] = bound
            missing_params = bound.validate_params()
            if missing_params:
                self.logger.warning(
                    f"Capability '{cap_id}' missing required params: {missing_params} — "
                    f"agent will be degraded"
                )
                self._degraded = True
            self.logger.ok(f"Mounted capability: {cap_id}")
            exposed = getattr(type(cap), "EXPOSED_COMMANDS", [])
            for cmd_def in exposed:
                cmd_name = cmd_def["name"]
                method_name = cmd_def.get("method", cmd_name)
                handler = getattr(bound, method_name)
                self._capability_commands[cmd_name] = handler
                self.commands.add(cmd_name)
                self.logger.info(f"  Exposed command: {cmd_name} (via {cap_id}.{method_name})")
        missing = needed - set(self.capabilities.keys())
        if missing:
            self._degraded = True
            self._disable_roles_with_missing_capabilities(missing)

    def _load_roles(self) -> None:
        import importlib.util
        import sys

        roles_dir = self.dir / "roles"
        if not roles_dir.exists():
            self.logger.warning(f"No roles directory found for {self.name}")
            return
        role_files = [
            file
            for file in roles_dir.glob("*.py")
            if not file.name.startswith("_")
        ]
        if not role_files:
            self.logger.warning(f"No role modules found for {self.name}")
            return
        for role_file in role_files:
            role_name = role_file.stem
            try:
                # Generamos un nombre de espacio único para evitar colisiones en sys.modules
                spec_name = f"vox_runtime.agents.{self.name.lower()}.roles.{role_name}"
                
                # Cargamos el rol apuntando directamente al archivo en disco
                spec = importlib.util.spec_from_file_location(spec_name, role_file)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot create spec for {role_file}")
                
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec_name] = module
                spec.loader.exec_module(module)

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

    def _disable_roles_with_missing_capabilities(self, missing: set[str]) -> None:
        for role_name, role in list(self.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())
            if requires & missing:
                self.roles.pop(role_name)
                self.logger.warning(
                    f"Role '{role_name}' disabled — missing capabilities: "
                    f"{sorted(requires & missing)}"
                )

    def _register_role_routes(self, role: Any) -> None:
        handlers = getattr(role, "_handlers", {})
        command_names = set(role.get_commands().keys())
        for route_name in handlers.keys():
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
    # Vault integration
    # ------------------------------------------------------------------

    async def _init_vault(self) -> None:
        try:
            self._vault = AgentVault(self.dir, self.id, config=self.config)
        except RuntimeError as e:
            self.logger.warning(f"Vault unavailable: {e}")
            self._vault = None

    def _inject_vault_secrets(self) -> None:
        if self._vault is None:
            return
        for cap_id, bound in self.capabilities.items():
            sensitive = getattr(type(bound._capability), "SENSITIVE_PARAMS", set())
            missing = []
            for key in sensitive:
                vault_val = self._vault.get(cap_id, key)
                if vault_val is not None:
                    bound._params[key] = vault_val
                    self.logger.info(f"Injected vault secret: {cap_id}.{key}")
                elif bound._params.get(key) is None:
                    missing.append(key)
            if not missing:
                continue
            self.logger.warning(
                f"Sensitive params missing for {cap_id}: {', '.join(missing)}. "
                f"Run 'python tools/provision_vault.py --agent {self.name.lower()}' "
                f"to provision them."
            )
            self._disable_roles_for_capability(cap_id)

    def _disable_roles_for_capability(self, cap_id: str) -> None:
        for role_name, role in list(self.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())
            if cap_id in requires:
                self.roles.pop(role_name)
                self.logger.warning(f"Role '{role_name}' disabled due to missing secrets")

    async def boot(self) -> bool:
        if self._state == AgentState.ACTIVE:
            return True
        if not self._state.can_transition_to(AgentState.BOOTING):
            self.logger.error(f"Cannot boot from state {self._state}")
            return False
        self._state = AgentState.BOOTING
        self.logger.info("Booting agent...")

        await self._init_vault()
        self._inject_vault_secrets()

        ok = True
        for cap in self.capabilities.values():
            try:
                await cap.initialize()
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

    async def pause(self) -> None:
        if not self._state.can_transition_to(AgentState.PAUSING):
            self.logger.warning(f"Cannot pause from state {self._state}")
            return
        self._state = AgentState.PAUSING
        self.logger.warning("Agent pausing...")
        self._state = AgentState.PAUSED
        self.logger.warning("Agent paused")

    async def resume(self) -> None:
        if not self._state.can_transition_to(AgentState.RESUMING):
            self.logger.warning(f"Cannot resume from state {self._state}")
            return
        self._state = AgentState.RESUMING
        self.logger.info("Agent resuming...")
        pending = self._event_queue.drain()
        self._state = AgentState.ACTIVE
        self.logger.ok(f"Resumed with {len(pending)} queued events")
        for evt in pending:
            await self.emit(evt.event_name, **evt.kwargs)

    async def stop(self) -> None:
        if not self._state.can_transition_to(AgentState.STOPPING):
            self.logger.warning(f"Cannot stop from state {self._state}")
            return
        self._state = AgentState.STOPPING
        self.logger.warning("Agent stopping...")
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self._state = AgentState.STOPPED
        self.logger.warning("Agent stopped")

    async def shutdown(self) -> None:
        self._state = AgentState.STOPPING
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for cap in self.capabilities.values():
            try:
                await cap.shutdown()
            except Exception as e:
                self.logger.error(f"Capability shutdown failed [{cap}]: {e}")
        self.roles.clear()
        self.capabilities.clear()
        self._state = AgentState.STOPPED
        self.logger.warning("Agent shutdown complete")

    async def emit(self, event_name: str, **kwargs) -> None:
        if self._state in (AgentState.PAUSED, AgentState.PAUSING):
            self._event_queue.enqueue(event_name, kwargs)
            return
        if self._state != AgentState.ACTIVE:
            return
        targets = self.event_router.get(event_name, [])
        for role in targets:
            try:
                await role.handle_event(event_name, **kwargs)
            except Exception as exc:
                self.logger.error(
                    f"Role dispatch failure [{event_name}]: {exc}"
                )
        handler = self._capability_commands.get(event_name)
        if handler:
            try:
                await handler(**kwargs)
            except Exception as exc:
                self.logger.error(
                    f"Capability command dispatch failure [{event_name}]: {exc}"
                )
