"""
VOX Orchestrator Core
Internal Name: THE ORCHESTRATOR

Responsible for the lifecycle management of VOXAgents, event routing mediation,
and fleet synchronization. It acts as the high-level entry point for the 
decentralized multi-agent system.
"""

from pathlib import Path
import asyncio
import sys
from typing import Dict, List

# Core internal imports
from core.agent import VOXAgent, AgentProvisionError
from core.logger import log_info, log_ok, log_warn, log_fail

class VOXOrchestrator:
    """
    Main controller for the VOX ecosystem.
    Implements the 'Manager' pattern to handle Agent discovery, 
    provisioning, and global event triggers.
    """

    def __init__(self, agents_path: str = "agents"):
        """
        Initializes the Orchestrator.
        :param agents_path: The directory where VOX looks for Agent blueprints.
        """
        self.agents_dir = Path(agents_path)
        self.active_agents: Dict[str, VOXAgent] = {}

    async def boot(self):
        """
        Executes the cold-boot sequence: Discovery > Instantiation > Activation.
        This follows a non-blocking gathering pattern for high-speed ignition.
        """
        log_info("Initializing VOX Discovery Sequence...")

        if not self._check_environment():
            log_fail("CRITICAL: Environment check failed. Shutting down.")
            sys.exit(1)

        # 1. Discovery and Internal Provisioning
        self._discover_and_hire()

        if not self.active_agents:
            log_fail("Initialization failed: No valid Agents were provisioned.")
            sys.exit(1)

        log_ok(f"Fleet Status: {len(self.active_agents)} Agents active.")
        log_info(f"Active Fleet IDs: {list(self.active_agents.keys())}")

        # 2. Global Ignition Sequence
        await self._activate_fleet()

    def _check_environment(self) -> bool:
        """Verifies if the necessary infrastructure is present."""
        if not self.agents_dir.exists():
            log_fail(f"Agent directory '{self.agents_dir}' is missing.")
            return False
        return True

    def _discover_and_hire(self):
        """Scans the filesystem and initiates the Agent loading protocol."""
        for agent_folder in self.agents_dir.iterdir():
            if agent_folder.is_dir() and not agent_folder.name.startswith('.'):
                log_info(f"Analyzing potential Agent: '{agent_folder.name}'...")
                self._hire_agent(agent_folder)

    def _hire_agent(self, folder: Path):
        """
        Abstraction Layer: Instantiates a VOXAgent.
        The Orchestrator doesn't know about Capabilities or Roles; 
        it only expects a valid Agent object.
        """
        try:
            # The Agent handles its own internal complexity (Black Box)
            new_agent = VOXAgent(folder, orchestrator=self)

            if new_agent.health_check():
                self.active_agents[new_agent.id] = new_agent
                new_agent.log_agent_ok("Onboarded and active.")
            else:
                log_warn(f"Agent '{folder.name}' failed health check. Rejected.")

        except AgentProvisionError as e:
            log_warn(f"Provisioning failed for {folder.name}: {e}")
        except Exception as e:
            log_fail(f"Uncaught exception during Agent ignition: {e}")

    async def _activate_fleet(self):
        """
        Triggers the asynchronous activation of all provisioned Agents.
        Follows a three-phase sequence: Engines > Stability > Greeting.
        """
        log_info("Executing Global Activation Sequence...")

        # Phase 1: Engine Start (Polling, Listeners, Sockets)
        await asyncio.gather(*(agent.boot() for agent in self.active_agents.values()))

        # Phase 2: Propagation Delay (Ensures network stability)
        await asyncio.sleep(1.5)

        # Phase 3: High-Level Broadcast
        log_info("Broadcasting 'on_boot' event to all Agents...")
        await asyncio.gather(*(agent.emit("on_boot") for agent in self.active_agents.values()))

    def get_global_commands(self) -> List[str]:
        """
        Aggregates all available capabilities (intents) from the entire fleet.
        Useful for Global NLU classification.
        """
        commands = set()
        for agent in self.active_agents.values():
            if hasattr(agent, 'commands'):
                commands.update(agent.commands)
        return list(commands)

    def get_active_commands(self) -> list:
        """
        Dynamic Discovery Protocol.
        Scans all onboarded agents and returns a consolidated list 
        of all unique public capabilities (commands).
        """
        all_commands = set()
        
        # We iterate through the active fleet
        for agent_id, agent in self.active_agents.items():
            # Each agent knows its own roles' capabilities
            # We assume agent.get_all_capabilities() returns a dict of {cmd: desc}
            agent_caps = agent.get_all_capabilities()
            all_commands.update(agent_caps.keys())
        
        # Convert set to list for JSON/LLM compatibility
        return sorted(list(all_commands))

# ==============================================================================
# ENTRY POINT
# ==============================================================================

async def main():
    """Application entry point with persistent loop."""
    log_info("Ignition Sequence Started...")
    
    vox = VOXOrchestrator()
    await vox.boot()
    
    log_ok("SYSTEM READY. VOX is monitoring in the background.")
    
    try:
        # Keeping the event loop alive
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        log_warn("Main loop cancelled. Preparing for shutdown...")

if __name__ == "__main__":
    log_info("Welcome to VOX - Virtual Orchestrator Experience")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_warn("Shutdown signal received (Ctrl+C).")
        log_info("Terminating all Agent processes...")