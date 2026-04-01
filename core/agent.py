import yaml
import asyncio
import importlib.util
from pathlib import Path
from core.logger import log_info, log_ok, log_warn, log_fail
from core.security.input_sanitizer import InputSanitizer, SecurityError
from core.security.rate_limiter import RateLimiter, RateLimitError

class AgentProvisionError(Exception):
    pass

class VOXAgent:
    def __init__(self, agent_dir: Path, orchestrator=None):
        self.dir = agent_dir
        self.orchestrator = orchestrator
        self.roles = {}
        self.commands = []
        self.capabilities = {}
        self._sanitizer = InputSanitizer()              # For input sanitization
        self._rate_limiter = RateLimiter(max_calls=100, window_seconds=60)

        # 1. Load Manifest
        self.config = self._load_manifest()
        if not self.config:
            raise AgentProvisionError(f"No valid agent.yml in {agent_dir}")

        # Validation
        required_fields = ["name", "id"]
        if not all(field in self.config for field in required_fields):
            raise AgentProvisionError(f"Missing fields (name/id) in {agent_dir}/agent.yml")

        self.name = self.config["name"]
        self.id = self.config["id"]
        self.master_id = self.config.get("master_id", "")

        self.log_agent_ok("Manifest loaded successfully")

        # 2. Provision Capabilities
        self._provision_capabilities()

        # 3. Discover Roles (No parameters for now)
        self.commands = self._discover_roles()
        if self.commands:
            self.log_agent_ok(
                f"Roles initialized successfully. My available commands are: {self.commands}"
            )
        else:
            self.log_agent_warn(f"Agent {self.name}'s Roles do not declare any public commands.")
        
        if not self.roles:
            self.log_agent_fail("Agent has no valid roles. Aborting.")
            raise AgentProvisionError(f"Agent {self.name} has no valid roles.")

    def _discover_roles(self):
        """Scans the Agent's Roles folder and tries to load Role files."""
        roles_path = self.dir / "roles"
        if not roles_path.exists():
            return

        all_discovered_commands = []

        for py_file in roles_path.glob("*.py"):
            # Skip hidden or system files
            if py_file.name.startswith("__"): continue
            
            role_instance = self._dynamic_load(py_file)
            if role_instance:
                # 1. Register Role
                self.roles[py_file.stem] = role_instance
                setattr(self, py_file.stem, role_instance)
                # 2. Extract public function names as commands
                if hasattr(role_instance, "get_capabilities"):
                    caps = role_instance.get_capabilities()
                    all_discovered_commands.extend(caps.keys())

        # This list might return empty
        return all_discovered_commands

    def _dynamic_load(self, py_file: Path):
        """Loads a .py file and looks for the Role class."""
        try:
            # Create a unique module name to avoid collisions in sys.modules
            module_name = f"agents.{self.name.lower()}.roles.{py_file.stem}"
            
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "Role"):
                role_class = getattr(module, "Role")
                # Inject the Agent instance (self)
                return role_class(self) 
            else:
                self.log_agent_warn(f"No class 'Role' found in {py_file.name}")
                return None
        except Exception as e:
            self.log_agent_fail(f"Failed to load role {py_file.stem}: {e}")
            return None

    def _load_manifest(self):
        manifest_path = self.dir / "agent.yml"
        if not manifest_path.exists(): return None
        try:
            with open(manifest_path, "r") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            log_fail(f"YAML Syntax Error in {manifest_path}: {e}")
            return None

    def _provision_capabilities(self):
        """Reads the 'capabilities' dictionary and provisions them from the core."""
        caps_config = self.config.get("capabilities", {})
        
        for cap_path, cap_params in caps_config.items():
            # 1. Translate e.g. 'comm.telegram' -> 'core/capabilities/comm/telegram.py'
            module_path = "core.capabilities." + cap_path
            
            try:
                # 2. Dynamic Loading of the Capability
                module = importlib.import_module(module_path)
                # Look for the standard class (e.g. TelegramCapability)
                # This converts 'telegram' to 'TelegramCapability'
                class_name = cap_path.split(".")[-1].capitalize() + "Capability"
                
                if hasattr(module, class_name):
                    cap_class = getattr(module, class_name)
                    # Instantiate using parameters in the YAML
                    instance = cap_class(self, **cap_params)
                    
                    # Register in the Agent
                    cap_name = cap_path.split(".")[-1]
                    self.capabilities[cap_name] = instance
                    setattr(self, f"cap_{cap_name}", instance)
                    
                    self.log_agent_ok(f"Capability '{cap_name}' mounted.")
            except Exception as e:
                self.log_agent_fail(f"Failed to mount capability {cap_path}: {e}")

    async def boot(self):
        """Activates all capabilities that have a 'boot' method."""
        for cap_instance in self.capabilities.values():
            if hasattr(cap_instance, "boot"):
                asyncio.create_task(cap_instance.boot())

    def health_check(self) -> bool:
        return len(self.roles) > 0

    def log_agent_ok(self, msg: str): log_ok(msg, self.name)
    def log_agent_info(self, msg: str): log_info(msg, self.name)
    def log_agent_warn(self, msg: str): log_warn(msg, self.name)
    def log_agent_fail(self, msg: str): log_fail(msg, self.name)

    async def emit(self, event_name, **kwargs):
        """Notifies all the Roles that something happened."""
        self.log_agent_info("Broadcasting event to all roles.")
        try:
            # Check using Rate Limiter
            if not self._rate_limiter.allow():
                self.log_agent_fail("BLOCK: Rate Limit exceeded.")
                raise RateLimitError(f"Agent {self.name} exceeded its call limit.")

            # Sanitize Input
            self.log_agent_info("Sanitizing input.")
            safe_kwargs = self._sanitizer.sanitize(kwargs)

            # Broadcast to the roles
            for role_inst in self.roles.values():
                if hasattr(role_inst, "handle_event"):
                    self.log_agent_info(f"{role_inst} will try to parse the command.")
                    await role_inst.handle_event(event_name, **safe_kwargs)
        except RateLimitError as e:
            self.log_agent_fail(f"RATE LIMIT BLOCK: {e}")
            # TODO: Report to the War Room
        except SecurityError as e:
            self.log_agent_fail(f"SECURITY BLOCK: {e}")
            # TODO: Report to the War Room

    async def report_to_master(self, event_name, **kwargs):
        """Sends an event to the Agent defined as master_id."""
        if not self.master_id:
            self.log_agent_warn("I'm trying to report, but my master_id is not defined.")
            # TODO: Report to the War Room
            return

        # We need the Orchestrator to help us find the 'Boss'
        # We assume we're saving a reference to the orchestrator in self.orchestrator
        master_agent = self.orchestrator.active_agents.get(self.master_id)
        
        if master_agent:
            self.log_agent_info(f"Escalating event '{event_name}' to {self.master_id}.")
            await master_agent.emit(event_name, sender_name=self.name, **kwargs)