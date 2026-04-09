"""
VOX Agent Executive
Internal Name: THE EXECUTIVE

Defines the VOXAgent class, which acts as a secure container for Roles and Capabilities.
It manages internal event routing (Synapsis) and enforces security boundaries
between raw input and role execution.
"""

import yaml
import asyncio
import importlib.util
from typing import Any, Dict, List, Optional, Type
from dotenv import dotenv_values
from pathlib import Path

# Core Infrastructure
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

    def __init__(self, agent_dir: Path, orchestrator: Any = None):
        """
        Initializes the Agent Sandbox.
        :param agent_dir: Path to the agent's filesystem (blueprint).
        :param orchestrator: Reference to the Orchestrator for inter-agent communication.
        """
        self.dir = agent_dir
        self.orchestrator = orchestrator
        
        # Internal State (The Single Source of Truth)
        self.config: Dict[str, Any] = {
            "name": None,
            "id": None,
            "master_id": None,
            "capabilities": []
        }
        
        self.roles: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.event_router: Dict[str, List[Any]] = {}
        self.commands: List[str] = []
        
        # Security Middleware
        self._sanitizer = InputSanitizer()
        self._rate_limiter = RateLimiter(max_calls=100, window_seconds=60)

        # Self-Ignition Sequence
        self._bootstrap()

    @property
    def name(self) -> str: return self.config.get("name") or "UnknownAgent"

    @property
    def id(self) -> str: return self.config.get("id")

    @property
    def master_id(self) -> str: return self.config.get("master_id")

    # --- LIFECYCLE MANAGEMENT -------------------------------------------------

    def _bootstrap(self):
        """Standard Ignition Sequence: Identity -> Capabilities -> Roles."""
        if not self._load_manifest():
            raise AgentProvisionError("Manifest (agent.yml) is missing or corrupted.")
            
        if not self._load_dotenv():
            raise AgentProvisionError("Environment (.env) is missing or unreadable.")

        # Identity Validation
        required_params = ["name", "id", "AGENT_TELEGRAM_TOKEN"]
        if not self._validate_identity(required_params):
            raise AgentProvisionError("Critical identity parameters are missing.")

        self.log_agent_ok(f"Identity Verified: {self.id}")

        # Provisioning Layers
        self._mount_capabilities()
        self.commands = self._discover_roles()
        
        if not self.roles:
            raise AgentProvisionError("Functional Failure: No valid roles discovered.")
            
        self.log_agent_ok(f"Agent Ready. System Synapsis established.")

    def _load_manifest(self) -> bool:
        """Parses the YAML blueprint."""
        manifest_path = self.dir / "agent.yml"
        try:
            with open(manifest_path, "r") as f:
                data = yaml.safe_load(f) or {}
                self.config.update({
                    "name": data.get("name"),
                    "id": data.get("id"),
                    "master_id": data.get("master_id"),
                    "capabilities": data.get("capabilities", [])
                })
                return True
        except Exception as e:
            log_fail(f"Blueprint Error [{self.dir.name}]: {e}")
            return False

    def _load_dotenv(self) -> bool:
        """Loads secrets into the internal config without polluting os.environ."""
        dotenv_path = self.dir / ".env"
        if not dotenv_path.exists(): return False
            
        secrets = dotenv_values(dotenv_path)
        self.config.update(secrets)
        
        # Core Mapping for specialized tokens
        if "TELEGRAM_TOKEN" in secrets:
            self.config["AGENT_TELEGRAM_TOKEN"] = secrets["TELEGRAM_TOKEN"]
        return True

    def _validate_identity(self, params: list) -> bool:
        """Check for mandatory operational parameters."""
        missing = [p for p in params if self.config.get(p) is None]
        for m in missing:
            self.log_agent_fail(f"Identity Gap: Missing '{m}'")
        return len(missing) == 0

    # --- PROVISIONING ENGINE --------------------------------------------------

    def _mount_capabilities(self):
        """Instantiates and attaches functional modules (Capabilities)."""
        caps_list = self.config.get("capabilities", [])
        
        # Security Firewall for Capability Provisioning
        PROTECTED = ["AGENT_ID", "MASTER_ID", "AGENT_TELEGRAM_TOKEN"]

        for cap_path in caps_list:
            try:
                module = importlib.import_module(f"core.capabilities.{cap_path}")
                class_name = cap_path.split(".")[-1].capitalize() + "Capability"
                cap_class = getattr(module, class_name)

                # Protocol Check: What data does the capability request?
                requested = cap_class.get_params()
                if any(p in requested for p in PROTECTED):
                    self.log_agent_fail(f"Capability '{cap_path}' BLOCKED: Unauthorized data access.")
                    continue

                safe_params, missing = cap_class.validate_and_extract(self.config)
                if missing:
                    self.log_agent_fail(f"Capability '{cap_path}' REJECTED: Missing {missing}")
                    continue

                # Instantiate and Bind
                instance = cap_class(self, **safe_params)
                cap_name = cap_path.split(".")[-1]
                self.capabilities[cap_name] = instance
                setattr(self, f"cap_{cap_name}", instance)
                
                self.log_agent_ok(f"Capability Mounted: {cap_name}")

            except Exception as e:
                self.log_agent_fail(f"Provisioning Failure [{cap_path}]: {e}")

    def _discover_roles(self) -> List[str]:
        """Scans for Role files and wires them to the Event Router."""
        roles_path = self.dir / "roles"
        if not roles_path.exists(): return []
        
        discovered_cmds = []
        for py_file in roles_path.glob("*.py"):
            if py_file.name.startswith("__"): continue
            
            role_inst = self._dynamic_load_role(py_file)
            if role_inst:
                role_name = py_file.stem
                self.roles[role_name] = role_inst
                setattr(self, role_name, role_inst)
                
                # Dynamic Routing Registration
                self._register_role_routes(role_inst)
                
                if hasattr(role_inst, "get_capabilities"):
                    discovered_cmds.extend(role_inst.get_capabilities().keys())
        return discovered_cmds

    def _register_role_routes(self, role_inst: Any):
        """Maps role handlers into the central Conductor (Synapsis)."""
        for event_name in role_inst._handlers.keys():
            if event_name not in self.event_router:
                self.event_router[event_name] = []
            
            if role_inst not in self.event_router[event_name]:
                self.event_router[event_name].append(role_inst)
                role_module = role_inst.__class__.__module__.split('.')[-1]
                self.log_agent_info(f"Behavior Associated: {event_name} -> [{role_module}]")

    def _dynamic_load_role(self, py_file: Path) -> Optional[Any]:
        """Low-level module loading for Roles."""
        try:
            module_name = f"agents.{self.name.lower()}.roles.{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "Role"):
                return getattr(module, "Role")(self)
        except Exception as e:
            self.log_agent_fail(f"Role Loading Error [{py_file.stem}]: {e}")
        return None

    # --- EVENT ORCHESTRATION --------------------------------------------------

    async def emit(self, event_name: str, **kwargs):
        """
        Primary Event Dispatcher.
        Implements Rate Limiting, Input Sanitization, and Targeted Routing.
        """
        try:
            # 1. Security Check
            self._rate_limiter.check_limit() 
            safe_kwargs = self._sanitizer.sanitize(kwargs)

            # 2. Targeted Delivery
            targets = self.event_router.get(event_name, [])
            if not targets: return

            for role_inst in targets:
                role_module = role_inst.__class__.__module__.split('.')[-1]
                self.log_agent_info(f"Routing '{event_name}' to [{role_module}]")
                await role_inst.handle_event(event_name, **safe_kwargs)

        except (RateLimitError, SecurityError) as e:
            self.log_agent_fail(f"SECURITY BREACH / LIMIT: {e}")
        except Exception as e:
            self.log_agent_fail(f"Internal Routing Error ({event_name}): {e}")

    async def boot(self):
        """Async engine activation for all mounted capabilities."""
        for cap in self.capabilities.values():
            if hasattr(cap, "boot"):
                asyncio.create_task(cap.boot())

    def get_all_capabilities(self) -> dict:
        """
        Aggregates capabilities from all attached roles.
        """
        combined_caps = {}
        for role in self.roles.values():
            combined_caps.update(role.get_capabilities())
        return combined_caps

    async def report_to_master(self, event_name: str, **kwargs):
        """Inter-Agent Escalation Protocol."""
        if not self.master_id or not self.orchestrator:
            self.log_agent_warn("Escalation failed: No Master ID or Orchestrator link.")
            return

        master_agent = self.orchestrator.active_agents.get(self.master_id)
        if master_agent:
            self.log_agent_info(f"Escalating '{event_name}' -> Master [{self.master_id}]")
            await master_agent.emit(event_name, sender_id=self.id, **kwargs)

    # --- UTILITIES ------------------------------------------------------------

    def health_check(self) -> bool: return len(self.roles) > 0

    def log_agent_ok(self, msg: str): log_ok(msg, self.name)
    def log_agent_info(self, msg: str): log_info(msg, self.name)
    def log_agent_warn(self, msg: str): log_warn(msg, self.name)
    def log_agent_fail(self, msg: str): log_fail(msg, self.name)