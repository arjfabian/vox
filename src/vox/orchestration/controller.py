"""FleetController — lock-guarded agent lifecycle transitions.

Part of the v0.5.0 service-oriented orchestrator decomposition.
Coordinates safe, transactional state transitions for individual
agents (stop, start, restart, pause, resume) using per-agent
``asyncio.Lock`` to prevent race conditions during concurrent
control-plane requests.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vox.observability import VOXForensicLogger

if TYPE_CHECKING:
    from vox.orchestration.graph import AgentGraph
    from vox.orchestration.registry import VOXRegistry


class FleetController:
    """Lock-guarded lifecycle controller for the agent fleet.

    Each agent ID has a dedicated ``asyncio.Lock`` so that concurrent
    control-plane operations (e.g. simultaneous HTTP requests) are
    serialised per agent without blocking unrelated agents.
    """

    def __init__(
        self,
        registry: VOXRegistry,
        graph: AgentGraph,
        logger: VOXForensicLogger,
        agent_locks: dict[str, asyncio.Lock],
        active_agents: dict[str, Any],
        inactive_agents: dict[str, Any],
        degraded_agents: dict[str, Any],
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._logger = logger
        self._agent_locks = agent_locks
        self._active_agents = active_agents
        self._inactive_agents = inactive_agents
        self._degraded_agents = degraded_agents

    def _get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._agent_locks:
            self._agent_locks[agent_id] = asyncio.Lock()
        return self._agent_locks[agent_id]

    async def stop_agent(self, agent_id: str) -> bool:
        async with self._get_agent_lock(agent_id):
            agent = self._active_agents.get(agent_id)
            if not agent:
                self._logger.error(f"Agent '{agent_id}' not found in active agents")
                return False
            await agent.stop()
            self._active_agents.pop(agent_id)
            self._inactive_agents[agent_id] = agent
            self._logger.ok(f"Agent '{agent.name}' stopped and moved to inactive")
            return True

    async def restart_agent(self, agent_name: str) -> bool:
        agent_id = self._graph.resolve_agent_id(agent_name)
        if not agent_id:
            self._logger.error(f"Agent '{agent_name}' not found")
            return False
        async with self._get_agent_lock(agent_id):
            agent = (
                self._active_agents.get(agent_id)
                or self._inactive_agents.get(agent_id)
                or self._degraded_agents.get(agent_id)
            )
            if not agent:
                self._logger.error(f"Agent '{agent_name}' not found in fleet")
                return False

            folder = agent.dir

            await agent.shutdown()
            self._active_agents.pop(agent_id, None)
            self._inactive_agents.pop(agent_id, None)
            self._degraded_agents.pop(agent_id, None)
            self._agent_locks.pop(agent_id, None)

            new_agent = self._registry.hire_agent(folder)
            if not new_agent:
                self._logger.error(f"Failed to re-hire agent '{agent_name}'")
                return False

            if new_agent._degraded or not new_agent.health_check():
                self._degraded_agents[new_agent.id] = new_agent
                self._logger.warning(
                    f"Agent '{agent_name}' restarted in DEGRADED state"
                )
                return True

            ok = await new_agent.boot()
            if ok:
                self._active_agents[new_agent.id] = new_agent
                self._logger.ok(f"Agent '{agent_name}' restarted and active")
            else:
                self._degraded_agents[new_agent.id] = new_agent
                self._logger.warning(
                    f"Agent '{agent_name}' restarted in DEGRADED state (boot failed)"
                )
            return ok

    async def start_agent_by_name(self, agent_name: str) -> bool:
        agent_id = self._graph.resolve_agent_id(agent_name)
        if not agent_id:
            self._logger.error(f"Agent '{agent_name}' not found")
            return False
        async with self._get_agent_lock(agent_id):
            agent = self._inactive_agents.get(agent_id)
            if not agent:
                self._logger.error(
                    f"Agent '{agent_name}' is already active or not found"
                )
                return False
            ok = await agent.boot()
            if ok:
                self._inactive_agents.pop(agent_id)
                self._active_agents[agent_id] = agent
                self._logger.ok(f"Agent '{agent.name}' started")
            return ok

    async def pause_agent(self, agent_name: str) -> bool:
        agent_id = self._graph.resolve_agent_id(agent_name)
        if not agent_id:
            self._logger.error(f"Agent '{agent_name}' not found")
            return False
        agent = self._active_agents.get(agent_id)
        if not agent:
            self._logger.error(f"Agent '{agent_name}' is not active")
            return False
        await agent.pause()
        return True

    async def resume_agent(self, agent_name: str) -> bool:
        agent_id = self._graph.resolve_agent_id(agent_name)
        if not agent_id:
            self._logger.error(f"Agent '{agent_name}' not found")
            return False
        agent = self._active_agents.get(agent_id)
        if not agent:
            self._logger.error(f"Agent '{agent_name}' is not active")
            return False
        await agent.resume()
        return True
