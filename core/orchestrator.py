import asyncio
import importlib.util
import sys
from pathlib import Path
from core.agent import VOXAgent
from core.control_server import ControlServer
from core.filewatcher import AgentFileWatcher
from core.logger import log_info, log_ok, log_warn, log_fail
from core.security.speaker_profile import SpeakerProfile

# ------------------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------------------

class VOXOrchestrator:
    """
    Main controller for the VOX ecosystem.

    Implements Agent discovery, provisioning, hierarchy management, and the
    start / stop / restart lifecycle API. All three operations are safe to
    call while the fleet is running.
    """

    def __init__(
        self,
        caps_path: str = "core/capabilities",
        agents_path: str = "agents"
    ):

        self.config                                          = self._load_system_env()

        self.caps_dir:            Path                       = Path(caps_path)
        self.active_capabilities: Dict [str, VOXCapability]  = {}

        self.agents_dir:          Path                       = Path(agents_path)
        self.active_agents:       Dict[str, VOXAgent]        = {}

        self._subordinates:       Dict[str, List[str]]       = {}
        self._agent_folders:      Dict[str, Path]           = {}

        self._watcher:            Optional[AgentFileWatcher] = None
        self._control:            Optional[ControlServer]    = None

    # --------------------------------------------------------------------------
    # BOOT
    # --------------------------------------------------------------------------

    async def boot(self):
        """
        Cold-boot sequence: Discovery → Instantiation → Activation → Watcher.
        """
        log_info("Initializing VOX Discovery Sequence...")

        # STEP 1: Check environment variables

        if not self._check_environment():
            log_fail("CRITICAL: Environment check failed. Shutting down.")
            sys.exit(1)
        log_ok("Environment set up. Proceeding to load Speaker Profile.")

        # STEP 2: Speaker profile (optional — degraded mode if absent)

        self.speaker_profile = SpeakerProfile()
        self.speaker_profile.load()
        log_ok("Speaker Profile loaded. Proceeding to discover Capabilities.")

        # STEP 3: Capability Discovery

        if not await self._discover_capabilities():
            log_fail("VOX cannot run in a degraded state. Aborting execution.")
            sys.exit(1)
        log_ok("Capabilities initialized. Proceeding to discover and hire Agents.")

        # STEP 4: Agent Discovery

        if not await self._discover_agents():
            log_fail("Initialization failed: No valid Agents were provisioned.")
            sys.exit(1)

        log_ok("Agents initialized. Proceeding to build Agent Hierarchy.")

        self._rebuild_hierarchy_index()

        log_ok(f"Fleet Status: {len(self.active_agents)} Agent(s) active.")
        log_info(f"Active Fleet IDs: {list(self.active_agents.keys())}")

        await self._activate_fleet()

        # Start filesystem watcher as a background task
        self._watcher = AgentFileWatcher(self.agents_dir, self)
        asyncio.create_task(self._watcher.run())

        # Start the Unix Domain Socket control plane
        self._control = ControlServer(self)
        await self._control.start()

    def _load_system_env(self) -> dict:
        from dotenv import dotenv_values
        return dotenv_values(".env")

    def _check_environment(self) -> bool:
        if not self.agents_dir.exists():
            log_fail(f"Agent directory '{self.agents_dir}' is missing.")
            return False
        return True

    # -------------------------------------------------------------------------
    # Capability discovery
    # -------------------------------------------------------------------------

    async def _discover_capabilities(self):
        """Scans core/capabilities/**/*.py for 'class Capability'."""
        for py_file in self.caps_dir.rglob("*.py"):

            if py_file.name.startswith("__"):
                continue
            
            # Use the folder structure as a namespace
            # Examples:
            #   - comm/telegram.py" -> "comm.telegram"
            #   - ai/ollama.py"     -> "ai.ollama"
            relative_path = py_file.relative_to(self.caps_dir)
            cap_id = ".".join(relative_path.with_suffix("").parts)

            instance = await self._dynamic_load_capability(py_file)
            if not instance:
                log_fail(f"Integrity Breach: Capability [{cap_id}] is non-functional.")
                return False
            
            # Add the name of the Capability for debug purposes
            instance.id = cap_id
            
            # Add Capability to pool
            self.active_capabilities[cap_id] = instance
            log_ok(f"Capability Online: [{cap_id}]")
            
        return True

    async def _dynamic_load_capability(self, py_file: Path):
        """
        Loads the 'Capability' class from a given file and performs health checks.
        """
        module_name = f"core.capabilities.{py_file.stem}"
        try:
            # Hot-reload safety: clear from cache if exists
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "Capability"):
                log_fail(f"Module [{py_file.stem}] missing 'Capability' class.")
                return None

            instance = module.Capability()
                
            # Perform Health Check if implemented
            if hasattr(instance, "health_check"):
                if not await instance.health_check():
                    log_fail(f"Capability [{py_file.stem}] failed health check.")
                    return None
            
            return instance

        except Exception as e:
            log_fail(f"Failed to load capability {py_file.stem}: {e}")
            return None

    # -------------------------------------------------------------------------
    # Agent discovery
    # -------------------------------------------------------------------------

    async def _discover_agents(self):
        """Scans the filesystem and provisions all valid agent directories."""
        for agent_folder in sorted(self.agents_dir.iterdir()):
            if agent_folder.is_dir() and not agent_folder.name.startswith("."):
                log_info(f"Analyzing potential Agent: '{agent_folder.name}'...")
                self._hire_agent(agent_folder)

        if not self.active_agents:
            log_fail("Fleet Integrity Error: No valid Agents were provisioned.")
            return False
        
        log_ok(f"Fleet discovery complete. {len(self.active_agents)} agents online.")
        return True

    def _hire_agent(self, folder: Path) -> Optional[VOXAgent]:
        """
        Provisions a single agent from a directory.
        Returns the agent on success, None on failure.
        Registers the folder path for later restart-by-name lookups.
        """
        try:
            new_agent = VOXAgent(folder, orchestrator=self)
            
            if not new_agent.health_check():
                log_warn(f"Agent '{folder.name}' failed health check. Rejected.")
                return None
            
            new_agent.config["VOX_WAR_ROOM_ID"] = self.config.get("VOX_WAR_ROOM_ID")
            self.active_agents[new_agent.id] = new_agent
            self._agent_folders[new_agent.name.lower()] = folder
            new_agent.log_agent_ok("Onboarded and active.")
            return new_agent

        except Exception as e:
            log_fail(f"Provisioning failed for '{folder.name}': {e}")

        return None

    # -------------------------------------------------------------------------
    # Fleet activation
    # -------------------------------------------------------------------------

    async def _activate_fleet(self):
        """Triggers async activation for all provisioned agents."""
        log_info("Executing Global Activation Sequence...")
        await asyncio.gather(*(agent.boot() for agent in self.active_agents.values()))
        await asyncio.sleep(1.5)
        log_info("Broadcasting 'on_boot' event to all Agents...")
        await asyncio.gather(*(agent.emit("on_boot") for agent in self.active_agents.values()))

    # -------------------------------------------------------------------------
    # Hierarchy
    # -------------------------------------------------------------------------

    def _rebuild_hierarchy_index(self):
        """
        Reconstructs the inverted hierarchy map from the current fleet.

        For every agent that declares a master_id, we register it as a
        subordinate of that master. This is O(n) over the fleet and is
        called after every start/stop/restart operation.
        """
        self._subordinates = {}
        for agent in self.active_agents.values():
            if agent.master_id:
                self._subordinates.setdefault(agent.master_id, [])
                if agent.id not in self._subordinates[agent.master_id]:
                    self._subordinates[agent.master_id].append(agent.id)

        log_info(f"Hierarchy index rebuilt: {self._subordinates}")

    def _get_all_subordinates(self, agent_id: str) -> List[str]:
        """
        Returns the full recursive list of subordinate UUIDs for a given agent.

        Uses BFS to traverse the hierarchy tree to arbitrary depth.
        The result is ordered breadth-first (direct children first, then
        grandchildren, etc.) which mirrors the natural notification order.

        Example — if the tree is:
            A → [B, C]
            B → [D, E, F]
        Then _get_all_subordinates(A) = [B, C, D, E, F]
        And  _get_all_subordinates(B) = [D, E, F]
        And  _get_all_subordinates(C) = []
        """
        visited: List[str] = []
        queue   = list(self._subordinates.get(agent_id, []))

        while queue:
            current = queue.pop(0)
            if current not in visited:
                visited.append(current)
                queue.extend(self._subordinates.get(current, []))

        return visited

    # -------------------------------------------------------------------------
    # Agent lifecycle management
    # -------------------------------------------------------------------------

    async def start_agent(self, folder: Path) -> Optional[VOXAgent]:
        """
        Provisions and boots a single agent from a directory.

        Safe to call while other agents are running.
        After provisioning, the hierarchy index is rebuilt so the new
        agent's relationships are immediately reflected.
        """
        log_info(f"Starting agent from '{folder.name}'...")
        agent = self._hire_agent(folder)
        if agent:
            await agent.boot()
            await agent.emit("on_boot")
            self._rebuild_hierarchy_index()
            log_ok(f"Agent '{agent.name}' started successfully.")
        return agent

    async def stop_agent(self, agent_id: str) -> bool:
        """
        Shuts down a single agent and removes it from the active fleet.

        Does NOT propagate to subordinates — call restart_agent() for
        cascade behaviour. This is the atomic primitive; higher-level
        operations compose it.
        """
        agent = self.active_agents.get(agent_id)
        if not agent:
            log_warn(f"stop_agent: No active agent with ID '{agent_id}'.")
            return False

        log_info(f"Stopping agent '{agent.name}' [{agent_id}]...")
        await agent.shutdown()
        del self.active_agents[agent_id]

        # Remove from name→folder index
        name_key = agent.name.lower()
        if name_key in self._agent_folders:
            del self._agent_folders[name_key]

        self._rebuild_hierarchy_index()
        log_ok(f"Agent '{agent.name}' stopped and removed from fleet.")
        return True

    async def restart_agent(self, agent_name: str) -> bool:
        """
        Full restart of an agent and cascade pause/resume for subordinates.

        Sequence for restart_agent("A") when A has subordinates [B, C]
        and B has subordinates [D, E, F]:

          1. Resolve A's ID and collect all subordinates: [B, C, D, E, F]
          2. Pause all subordinates (they queue incoming events).
          3. Stop A cleanly (shutdown + remove from fleet).
          4. Reload A from disk (fresh agent.yml, .env, roles/*.py).
          5. Boot A → emit on_boot.
          6. Resume all subordinates in reverse BFS order (deepest first):
             [D, E, F, B, C] — so leaf nodes resume before their parents,
             avoiding a scenario where B is resumed and immediately tries to
             escalate to A before A is ready.
          7. Rebuild hierarchy index.

        If A itself fails to restart, subordinates are NOT resumed
        automatically — the Orchestrator logs the failure and instructs
        the user to check logs and restart manually.
        """
        # --- 1. Resolve agent by name ---
        agent_id = self._resolve_agent_id(agent_name)
        if not agent_id:
            log_fail(f"restart_agent: No active agent named '{agent_name}'.")
            return False

        agent  = self.active_agents[agent_id]
        folder = self._agent_folders.get(agent.name.lower())
        if not folder:
            log_fail(f"restart_agent: Folder for agent '{agent_name}' not found.")
            return False

        subordinate_ids = self._get_all_subordinates(agent_id)
        log_info(
            f"Restarting '{agent.name}'. "
            f"Subordinates affected: {len(subordinate_ids)} "
            f"({subordinate_ids or 'none'})."
        )

        # --- 2. Pause all subordinates ---
        for sub_id in subordinate_ids:
            sub = self.active_agents.get(sub_id)
            if sub:
                sub.pause()

        # --- 3. Stop the target agent ---
        if not await self.stop_agent(agent_id):
            # Unlikely at this point, but guard anyway
            log_fail(f"Failed to stop '{agent_name}'. Aborting restart.")
            self._notify_manual_restart(agent_name, subordinate_ids)
            return False

        # Small propagation delay so capabilities close their connections
        await asyncio.sleep(0.5)

        # --- 4 & 5. Reload and boot ---
        new_agent = await self.start_agent(folder)
        if not new_agent:
            log_fail(
                f"Agent '{agent_name}' FAILED to restart. "
                f"Subordinates remain PAUSED."
            )
            self._notify_manual_restart(agent_name, subordinate_ids)
            return False

        # --- 6. Resume subordinates (reverse BFS = deepest first) ---
        for sub_id in reversed(subordinate_ids):
            sub = self.active_agents.get(sub_id)
            if sub:
                # new_agent.id is the authoritative master for direct children;
                # for grandchildren the master is their own declared master_id.
                # We pass new_agent.id; resume() validates against master_id,
                # so only direct children will accept it.
                #
                # For deeper nodes (grandchildren), we look up their actual
                # master in the active fleet and ask it to resume them.
                self._resume_subordinate(sub, new_agent)

        # --- 7. Rebuild index ---
        self._rebuild_hierarchy_index()
        log_ok(f"Restart of '{agent_name}' complete.")
        return True

    def _resume_subordinate(self, sub: VOXAgent, restarted_agent: VOXAgent):
        """
        Issues a resume() to a subordinate using the correct master identity.

        A subordinate only accepts resume() from its declared master_id.
        If the subordinate's master is the restarted agent, pass its new ID.
        Otherwise, look up the subordinate's actual master in the active
        fleet and delegate — the master will issue the resume itself once
        it is back online (via its own on_boot role logic).
        """
        if sub.master_id == restarted_agent.id:
            sub.resume(restarted_agent.id)
        else:
            # This subordinate belongs to an intermediate master.
            # Its master is already active (it was only paused, not stopped),
            # so we ask the master to resume it now.
            actual_master = self.active_agents.get(sub.master_id)
            if actual_master and actual_master.is_active:
                sub.resume(sub.master_id)
            else:
                log_warn(
                    f"Cannot auto-resume '{sub.name}': master "
                    f"'{sub.master_id}' is not active. Manual restart required."
                )

    def _notify_manual_restart(self, agent_name: str, subordinate_ids: List[str]):
        """
        Logs a structured failure report when automatic restart fails.
        Subordinates remain PAUSED until the user intervenes.
        """
        log_fail("=" * 60)
        log_fail(f"  MANUAL INTERVENTION REQUIRED")
        log_fail(f"  Agent '{agent_name}' could not be restarted.")
        log_fail(f"  Check agent logs, then run:")
        log_fail(f"    python vox.py restart {agent_name}")
        if subordinate_ids:
            paused_names = [
                self.active_agents[sid].name
                for sid in subordinate_ids
                if sid in self.active_agents
            ]
            log_fail(f"  Subordinates currently PAUSED: {paused_names}")
            log_fail(f"  They will resume once '{agent_name}' is back online.")
        log_fail("=" * 60)

    # -------------------------------------------------------------------------
    # Introspection
    # -------------------------------------------------------------------------

    def _resolve_agent_id(self, name_or_id: str) -> Optional[str]:
        """
        Resolves an agent name or UUID to the UUID used in active_agents.

        Accepts:
          - Exact UUID (e.g. "14839225-d9d7-...")
          - Case-insensitive agent name
        """
        # Try direct UUID lookup first
        if name_or_id in self.active_agents:
            return name_or_id

        # Try name lookup
        name_lower = name_or_id.lower()
        for agent_id, agent in self.active_agents.items():
            if agent.name.lower() == name_lower:
                return agent_id

        return None

    def get_active_commands(self) -> List[str]:
        """Dynamic discovery of all unique public capabilities in the fleet."""
        all_commands: set = set()
        for agent in self.active_agents.values():
            all_commands.update(agent.get_all_capabilities().keys())
        return sorted(all_commands)