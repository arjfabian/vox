"""
VOX Orchestrator Core
Internal Name: THE ORCHESTRATOR

Responsible for the lifecycle management of VOXAgents, event routing mediation,
fleet synchronization, file change detection, and CLI-driven agent control.

New in v2:
  - Hierarchy index: master_id → [subordinate_ids] (arbitrary depth)
  - start_agent(folder)   — provisions + boots a single agent
  - stop_agent(agent_id)  — clean shutdown + removal from fleet
  - restart_agent(name)   — stop → reload → start, with cascade pause/resume
  - FileWatcher           — monitors agent.yml / .env / roles/*.py for changes
  - CLI interface:
      python vox.py                     # run the fleet normally
      python vox.py restart <name>      # restart an agent (future hook)
      python vox.py stop    <name>      # stop an agent
"""

import asyncio
# import hashlib
import sys
from pathlib import Path
# from typing import Dict, List, Optional, Set

# from core.agent import VOXAgent, AgentProvisionError
# from core.logger import log_info, log_ok, log_warn, log_fail
# from core.lifecycle import AgentState
from core.orchestrator import VOXOrchestrator
from core.logger import log_info, log_ok, log_warn



# ------------------------------------------------------------------------------
# CLI ENTRY POINT
# ------------------------------------------------------------------------------

def _print_usage():
    print(
        "\nUsage:\n"
        "  python vox.py                  — Start the full fleet\n"
        "  python vox.py restart <name>   — Restart a named agent\n"
        "  python vox.py stop    <name>   — Stop a named agent\n"
    )


async def _cli_run(args: List[str]):
    """
    Handles CLI sub-commands.

    'run' (default): cold-boot the full fleet and keep running.
    'restart <name>': boot fleet, then immediately restart the named agent.
    'stop <name>':    boot fleet, then stop the named agent.

    In a production setup, restart/stop would be sent to a running process
    via a Unix socket or HTTP control plane. For now, they boot the fleet
    first so the command can be tested end-to-end in a single invocation.
    """
    command    = args[0] if args else "run"
    agent_name = args[1] if len(args) > 1 else None

    vox = VOXOrchestrator()

    if command == "run":
        await vox.boot()
        log_ok("SYSTEM READY. VOX is monitoring in the background.")
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log_warn("Main loop cancelled. Preparing for shutdown...")

    elif command in ("restart", "stop"):
        if not agent_name:
            log_fail(f"Command '{command}' requires an agent name.")
            _print_usage()
            sys.exit(1)

        # Boot the fleet first so the orchestrator has a live context
        await vox.boot()
        log_ok("SYSTEM READY.")

        if command == "restart":
            success = await vox.restart_agent(agent_name)
        else:  # stop
            agent_id = vox._resolve_agent_id(agent_name)
            success  = await vox.stop_agent(agent_id) if agent_id else False
            if not success:
                log_fail(f"Could not stop agent '{agent_name}'.")

        if success:
            log_ok(f"Command '{command} {agent_name}' completed successfully.")
        # The process stays up so the fleet keeps running
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    else:
        log_fail(f"Unknown command: '{command}'")
        _print_usage()
        sys.exit(1)


if __name__ == "__main__":
    log_info("Welcome to VOX - Virtual Orchestrator eXperience")
    cli_args = sys.argv[1:]

    try:
        asyncio.run(_cli_run(cli_args))
    except KeyboardInterrupt:
        log_warn("Shutdown signal received (Ctrl+C).")
        log_info("Terminating all Agent processes...")