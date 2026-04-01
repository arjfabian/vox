from pathlib import Path
import asyncio
from core.agent import VOXAgent, AgentProvisionError
from core.logger import log_info, log_ok, log_warn, log_fail
import sys

class VOXOrchestrator:
    def __init__(self, agents_path: str = "agents"):
        self.agents_dir = Path(agents_path)
        self.active_agents = {}

    async def boot(self):
        """Performs Agent Discovery and Onboarding."""

        # ---  AGENT DISCOVERY  ------------------------------------------------

        log_info("Initializing VOX Discovery Sequence...")

        # Make sure the agents/ directory exists before starting VOX.
        if not self.agents_dir.exists():
            log_fail(f"CRITICAL: Agent directory '{self.agents_dir}' not found!")
            log_fail("VOX cannot operate without agents. Shutting down.")
            sys.exit(1)

        # Scan the agents/ folder.
        for agent_folder in self.agents_dir.iterdir():
            if agent_folder.is_dir():
                # The path string starts with "agents/", so it is trimmed
                log_info(f"Processing folder '{agent_folder.name}'...")
                self._hire_agent(agent_folder)

        # VOX is terminated if there are no agents to run.
        active_agents = self.active_agents
        if active_agents == 0:
            log_fail(f"No Agents could be initialized.")
            sys.exit(1)

        log_ok(f"VOX is operative. {len(self.active_agents)} Agents ready.")
        
        log_info(f"Agents in fleet: {list(self.active_agents.keys())}")

        # ---  GLOBAL ACTIVATION  ----------------------------------------------

        log_info("Starting Global Activation Sequence...")

        # 1. Start the "engines" for each Agent (e.g. Telegram polling)
        await asyncio.gather(*(agent.boot() for agent in self.active_agents.values()))

        # 2. Allow sockets to be completely established
        await asyncio.sleep(1.5)

        # 3. Trigger the Welcome event
        await asyncio.gather(*(agent.emit("on_boot") for agent in self.active_agents.values()))

    def _hire_agent(self, folder: Path):
        """Tries to instantiate and validate an Agent."""
        try:
            # 1. Try to instantiate the Agent (YAML parse + Role discovery)
            log_info(f"Instantiating Agent '{folder.name}'...")
            new_agent = VOXAgent(folder, orchestrator=self)

            # 2. Health Check (Security Protocol)
            log_info(f"Performing Health Check on '{folder.name}'...")
            if new_agent.health_check():
                self.active_agents[new_agent.id] = new_agent
                new_agent.log_agent_ok("Now active and ready to begin!")
            else:
                log_warn(f"Agent '{folder.name}' failed the Health Check and won't be loaded.")

        except AgentProvisionError as e:
            # If the Agent has no YAML or Roles, VOX ignores it and moves on
            log_warn(f"Skipping {folder.name}: {e}")
        except Exception as e:
            # Some other Exception happened
            log_fail(f"Critical error loading '{folder.name}': {e}")

    def get_active_commands(self):
        """
        Goes through the list of active Agents and collects all the commands
        (intents) that they can handle.
        """
        total_commands = []
        
        for agent_id, agent_inst in self.active_agents.items():
            # If self.commands is not empty, generate a dictionary:
            # {'parse_and_apply': <role_object>}
            if hasattr(agent_inst, 'commands'):
                total_commands.extend(agent_inst.commands)
                
        # Clean duplicates
        return list(set(total_commands))

    def get_fleet_status(self):
        """Returns the hierarchy for the War Room."""
        return {uid: agent.config for uid, agent in self.active_agents.items()}

# ===  MAIN LOOP   =============================================================

async def main():
    log_info("Initializing...")
    vox = VOXOrchestrator()
    await vox.boot()
    log_ok("VOX is operative. Press Ctrl+C to terminate.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    log_info("Welcome to VOX!")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_warn("Shutting down VOX...")
