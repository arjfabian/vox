"""
VOX Agent Executive
Internal Name: THE EXECUTIVE

Defines the VOXAgent class, which acts as a secure container for Roles and
Capabilities. It manages internal event routing and enforces security boundaries
between raw input and role execution.

Lifecycle additions (v2):
  - Explicit AgentState tracking via core.lifecycle.AgentState
  - PAUSED state: emit() enqueues events instead of routing them
  - pause() / resume(master_id) for master-coordinated suspension
  - shutdown() for clean task cancellation and capability teardown
  - self._tasks tracking so asyncio tasks are never orphaned
  - sys.modules cleanup in _dynamic_load_role for true hot-reload
"""

import asyncio
import ast
import importlib
import importlib.util
import sys
import yaml
from dotenv import dotenv_values
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.lifecycle import AgentState, EventQueue
from core.logger import log_info, log_ok, log_warn, log_fail
from core.security.input_sanitizer import InputSanitizer, SecurityError
from core.security.rate_limiter import RateLimiter, RateLimitError


class AgentProvisionError(Exception):
    """Raised when an Agent fails to satisfy its blueprint requirements."""
    pass


class VOXAgent:
    """
    Autonomous Execution Unit.
    Manages its own lifecycle, security context, and internal role orchestration.
    """

    # Shared stateless sanitizer — no per-agent state needed
    _GLOBAL_SANITIZER = InputSanitizer()

    def __init__(self, agent_dir: Path, orchestrator: Any = None):
        """
        Initializes the Agent Sandbox.
        :param agent_dir: Path to the agent's filesystem (blueprint).
        :param orchestrator: Reference to the Orchestrator for inter-agent
                             communication and lifecycle management.
        """
        self.dir          = agent_dir
        self.orchestrator = orchestrator
        # Internal State (The Single Source of Truth)
        self.config: Dict[str, Any] = {
            "name":       None,
            "id":         None,
            "master_id":  None,
            "capabilities": []
        }
        self.manifest     = self._load_manifest()

        self.roles:        Dict[str, Any]       = {}
        self.capabilities: Dict[str, Any]       = {}
        self.event_router: Dict[str, List[Any]] = {}
        self.commands:     List[str]            = []

        # Lifecycle State Machine
        self._state        = AgentState.BOOTING
        self._event_queue  = EventQueue()          # holds events while PAUSED
        self._tasks: List[asyncio.Task] = []       # all spawned asyncio tasks

        # Security Middleware (sanitizer is shared; rate limiter is per-agent)
        self._sanitizer    = VOXAgent._GLOBAL_SANITIZER
        self._rate_limiter = RateLimiter(max_calls=100, window_seconds=60)

        # Self-Ignition Sequence
        self._bootstrap()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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
    def state(self) -> AgentState:
        return self._state

    @state.setter
    def state(self, value: AgentState):
        self._state = value

    @property
    def is_active(self) -> bool:
        return self._state == AgentState.ACTIVE

    @property
    def is_paused(self) -> bool:
        return self._state == AgentState.PAUSED

    # -------------------------------------------------------------------------
    # Bootstrap sequence
    # -------------------------------------------------------------------------

    def _bootstrap(self):
        """Standard Ignition Sequence: Identity → Capabilities → Roles."""
        if not self._load_manifest():
            raise AgentProvisionError("Manifest (agent.yml) is missing or corrupted.")

        if not self._load_dotenv():
            raise AgentProvisionError("Environment (.env) is missing or unreadable.")

        required_params = ["name", "id", "AGENT_TELEGRAM_TOKEN"]
        if not self._validate_identity(required_params):
            raise AgentProvisionError("Critical identity parameters are missing.")

        self.log_agent_ok(f"Identity verified: {self.id}")

        self._mount_capabilities()
        self.commands = self._discover_roles()

        if not self.roles:
            raise AgentProvisionError("Functional Failure: No valid roles discovered.")

        self.log_agent_ok("Agent Ready.")

    def _load_manifest(self) -> bool:
        """Parses the YAML blueprint."""
        manifest_path = self.dir / "agent.yml"
        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}
                self.config.update({
                    "name":         data.get("name"),
                    "id":           data.get("id"),
                    "master_id":    data.get("master_id"),
                    "capabilities": data.get("capabilities", [])
                })
                self.manifest = data
                return True
        except Exception as e:
            log_fail(f"Blueprint Error [{self.dir.name}]: {e}")
            return False

    def _load_dotenv(self) -> bool:
        """Loads secrets into the internal config without polluting os.environ."""
        dotenv_path = self.dir / ".env"
        if not dotenv_path.exists():
            return False

        secrets = dotenv_values(dotenv_path)
        self.config.update(secrets)

        if "AGENT_TELEGRAM_TOKEN" in secrets:
            self.config["AGENT_TELEGRAM_TOKEN"] = secrets["AGENT_TELEGRAM_TOKEN"]
        return True

    def _validate_identity(self, params: list) -> bool:
        """Check for mandatory operational parameters."""
        missing = [p for p in params if self.config.get(p) is None]
        for m in missing:
            self.log_agent_fail(f"Identity Gap: Missing '{m}'")
        return len(missing) == 0

    # ------------------------------------------------------------------
    # PROVISIONING ENGINE
    # ------------------------------------------------------------------

    def _mount_capabilities(self):
        """
        Requests the Orchestrator to inject pre-validated capability proxies.
        """
        caps_list = self.config.get("capabilities", [])
        # PROTECTED = ["AGENT_ID", "MASTER_ID", "AGENT_TELEGRAM_TOKEN"]

        if not self.orchestrator:
            self.log_agent_fail("Provisioning aborted: No Orchestrator link.")
            return

        for cap_id in caps_list:
            # Look for the global instance in the Orchestrator
            global_cap = self.orchestrator.active_capabilities.get(cap_id)
            
            if global_cap:
                try:
                    # Pass the agent's .env (self.config) to the BoundCapability
                    bound_instance = global_cap.mount(self, self.config)
                    
                    cap_short_name = cap_id.split(".")[-1]
                    
                    # Inject Capabilities to the Agent
                    self.capabilities[cap_short_name] = bound_instance
                    setattr(self, f"cap_{cap_short_name}", bound_instance)
                    
                    self.log_agent_ok(f"Capability Wired: {cap_id}")
                except Exception as e:
                    self.log_agent_fail(f"Wiring Failure [{cap_id}]: {e}")
            else:
                self.log_agent_fail(f"Provisioning Failure: Capability [{cap_id}] not found in System.")

    # -------------------------------------------------------------------------
    # Role discovery
    # -------------------------------------------------------------------------

    def _discover_roles(self) -> List[str]:
        """Scans for Role files and wires them to the Event Router."""
        roles_path = self.dir / "roles"
        if not roles_path.exists():
            return []

        discovered_cmds = []
        for py_file in roles_path.glob("*.py"):
            if py_file.name.startswith("__") or py_file.name.endswith("_test.py"):
                continue

            role_inst = self._dynamic_load_role(py_file)
            if role_inst:
                role_name = py_file.stem
                self.roles[role_name] = role_inst
                setattr(self, role_name, role_inst)
                self._register_role_routes(role_inst)

                if hasattr(role_inst, "get_capabilities"):
                    discovered_cmds.extend(role_inst.get_capabilities().keys())

        return discovered_cmds

    def _register_role_routes(self, role_inst: Any):
        """Maps role handlers into the central Event Router."""
        for event_name in role_inst._handlers.keys():
            if event_name not in self.event_router:
                self.event_router[event_name] = []
            if role_inst not in self.event_router[event_name]:
                self.event_router[event_name].append(role_inst)
                role_module = role_inst.__class__.__module__.split(".")[-1]
                self.log_agent_info(f"Behavior Associated: {event_name} -> [{role_module}]")

    def _dynamic_load_role(self, py_file: Path) -> Optional[Any]:
        """
        Hot-reload aware Role loader.

        Clears sys.modules for this role's module name before importing so
        that a restart always picks up the latest code from disk — not a
        cached version from a previous load.
        """
        try:
            role_source = py_file.read_text().strip()
            if not role_source:
                self.log_agent_warn(f"Module '{py_file.stem}' is empty. Skipping.")
                return None

            if not self._perform_security_audit(role_source, py_file.stem):
                self.log_agent_fail(f"Security Rejection: Role '{py_file.stem}' is unsafe.")
                return None

            module_name = f"agents.{self.name.lower()}.roles.{py_file.stem}"

            # --- Hot-reload: purge stale module from Python's import cache ---
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec   = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "Role"):
                self.log_agent_ok(f"Role '{py_file.stem}' verified and active.")
                return getattr(module, "Role")(self)
            else:
                self.log_agent_warn(f"Module '{py_file.stem}' has no 'Role' class declared.")

        except Exception as e:
            self.log_agent_fail(f"Role Loading Error [{py_file.stem}]: {e}")

        return None

    def _perform_security_audit(self, source: str, role_name: str) -> bool:
        """AST-based scanner to detect forbidden I/O primitives."""
        try:
            tree      = ast.parse(source)
            forbidden = {"open", "write", "os.system", "subprocess", "eval", "exec"}

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in forbidden:
                        return False
                    if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden:
                        return False
            return True
        except Exception as e:
            self.log_agent_fail(f"Audit engine failure on {role_name}: {e}")
            return False

    # ------------------------------------------------------------------
    # LIFECYCLE MANAGEMENT
    # ------------------------------------------------------------------

    async def boot(self):
        """Activates the agent's functional brain."""
        self.log_agent_info("Executing activation sequence...")
        
        for cap_name, cap_instance in self.capabilities.items():
            # Try to load Capability.boot() (via proxy)
            try:
                # The proxy will redirect to the class's boot(), and pass the
                # proxy as 'self'.
                await cap_instance.boot() 
                self.log_agent_ok(f"Capability Sparked: {cap_name}")
            except AttributeError:
                # If the Capability has no boot() method, skip.
                continue
            except Exception as e:
                self.log_agent_fail(f"Failed to spark {cap_name}: {e}")

        # Change state to ACTIVE only if successfully booted
        self.state = AgentState.ACTIVE
        self.log_agent_ok("State → ACTIVE.")

    def pause(self):
        """
        Transitions the agent to PAUSED state.

        While paused, incoming emit() calls are enqueued rather than
        routed. Called by the Orchestrator before restarting this agent's
        master so the agent does not process events against a stale master
        reference.
        """
        if self._state == AgentState.ACTIVE:
            self._state = AgentState.PAUSED
            self.log_agent_warn(
                f"State → PAUSED. Events will be queued until master resumes."
            )

    def resume(self, master_id: str):
        """
        Transitions the agent from PAUSED → ACTIVE and drains the event queue.

        Only the correct master (identified by UUID) may resume this agent.
        This prevents rogue or mis-routed resume signals from an unrelated
        agent in the hierarchy.

        :param master_id: UUID of the master that is authorizing the resume.
        """
        if self._state != AgentState.PAUSED:
            return

        # Trust boundary: only the declared master may resume this agent
        if master_id != self.master_id:
            self.log_agent_fail(
                f"Resume REJECTED: sender '{master_id}' is not this agent's master."
            )
            return

        self._state = AgentState.ACTIVE
        pending     = self._event_queue.drain()
        dropped     = self._event_queue.dropped

        self.log_agent_ok(
            f"State → ACTIVE. Draining {len(pending)} queued event(s)."
        )
        if dropped:
            self.log_agent_warn(
                f"{dropped} event(s) were dropped (queue capacity exceeded while paused)."
            )

        # Re-emit queued events in arrival order — fire-and-forget via task
        async def _drain():
            for evt in pending:
                await self.emit(evt.event_name, **evt.kwargs)

        task = asyncio.create_task(_drain())
        self._tasks.append(task)

    async def shutdown(self):
        """
        Clean teardown sequence.

        1. Mark state as STOPPED so no new tasks are accepted.
        2. Cancel all tracked asyncio tasks.
        3. Invoke shutdown() on every capability that supports it.
        4. Clear roles and event router so stale references are released.
        """
        self._state = AgentState.STOPPED
        self.log_agent_warn("Initiating shutdown sequence...")

        # Cancel all tracked async tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Teardown capabilities (e.g. close persistent HTTP sessions)
        for cap_name, cap in self.capabilities.items():
            if hasattr(cap, "shutdown"):
                try:
                    await cap.shutdown()
                    self.log_agent_info(f"Capability '{cap_name}' shut down.")
                except Exception as e:
                    self.log_agent_fail(f"Capability '{cap_name}' shutdown error: {e}")

        # Release role and routing references
        self.roles.clear()
        self.event_router.clear()

        self.log_agent_warn("Shutdown complete.")

    # ------------------------------------------------------------------
    # EVENT BUS
    # ------------------------------------------------------------------

    async def emit(self, event_name: str, **kwargs):
        """
        Primary Event Dispatcher.

        Behaviour varies by agent state:
          ACTIVE  → sanitize, rate-check, route to registered roles.
          PAUSED  → enqueue the event for later processing.
          STOPPED → silently discard (agent is shutting down).
          BOOTING → silently discard (not yet ready).
          FAILED  → silently discard.
        """
        # --- Paused: hold for later ---
        if self._state == AgentState.PAUSED:
            accepted = self._event_queue.enqueue(event_name, kwargs)
            if not accepted:
                self.log_agent_warn(
                    f"Event '{event_name}' DROPPED: queue at capacity "
                    f"({self._event_queue._cap}). Master has been offline too long."
                )
            else:
                self.log_agent_info(
                    f"Event '{event_name}' queued "
                    f"({self._event_queue.size} pending)."
                )
            return

        # --- Not active: discard ---
        if self._state != AgentState.ACTIVE:
            return

        try:
            self._rate_limiter.check_limit()
            safe_kwargs = self._sanitizer.sanitize(kwargs)

            targets = self.event_router.get(event_name, [])
            if not targets:
                return

            for role_inst in targets:
                role_module = role_inst.__class__.__module__.split(".")[-1]
                self.log_agent_info(f"Routing '{event_name}' to [{role_module}]")
                await role_inst.handle_event(event_name, **safe_kwargs)

        except (RateLimitError, SecurityError) as e:
            self.log_agent_fail(f"SECURITY BREACH / LIMIT: {e}")
        except Exception as e:
            self.log_agent_fail(f"Internal Routing Error ({event_name}): {e}")

    async def report_to_master(self, event_name: str, **kwargs):
        """Inter-Agent Escalation Protocol."""
        if not self.master_id or not self.orchestrator:
            self.log_agent_warn("Escalation failed: No Master ID or Orchestrator link.")
            return

        master_agent = self.orchestrator.active_agents.get(self.master_id)
        if master_agent:
            self.log_agent_info(f"Escalating '{event_name}' → Master [{self.master_id}]")
            await master_agent.emit(event_name, sender_id=self.id, **kwargs)

    async def war_room_alert(self, message: str):
        token   = self.config.get("AGENT_TELEGRAM_TOKEN")
        chat_id = self.config.get("VOX_WAR_ROOM_ID")
        if not token or not chat_id:
            self.log_agent_warn("War Room unreachable: missing token or chat ID.")
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"[{self.name}] {message}"}
            )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        return len(self.roles) > 0

    def get_all_capabilities(self) -> dict:
        """Aggregates capabilities from all attached roles."""
        combined = {}
        for role in self.roles.values():
            combined.update(role.get_capabilities())
        return combined

    # ------------------------------------------------------------------
    # Logging shorthands
    # ------------------------------------------------------------------

    def log_agent_ok(self,   msg: str): log_ok(msg,   self.name)
    def log_agent_info(self, msg: str): log_info(msg, self.name)
    def log_agent_warn(self, msg: str): log_warn(msg, self.name)
    def log_agent_fail(self, msg: str): log_fail(msg, self.name)