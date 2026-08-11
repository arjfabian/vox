"""AgentGraph — in-memory fleet topology index.

Part of the v0.5.0 service-oriented orchestrator decomposition.
Maintains indexed lookups of active, inactive, and degraded agents,
resolves agent identities, and exposes the hierarchical topology
of the running fleet.
"""

from __future__ import annotations

from typing import Any

from vox.observability import VOXForensicLogger


class AgentGraph:
    """Runtime index of the agent fleet.

    Provides read-only query methods over the three agent registries
    (active, inactive, degraded).  Intended as the structural
    foundation for the lazy graph traversal planned in v0.5.0.
    """

    def __init__(
        self,
        active_agents: dict[str, Any],
        inactive_agents: dict[str, Any],
        degraded_agents: dict[str, Any],
        logger: VOXForensicLogger,
    ) -> None:
        self._active_agents = active_agents
        self._inactive_agents = inactive_agents
        self._degraded_agents = degraded_agents
        self._logger = logger

    @property
    def all_agents(self) -> dict[str, Any]:
        return {
            **self._active_agents,
            **self._inactive_agents,
            **self._degraded_agents,
        }

    def resolve_agent_id(self, agent_name: str) -> str | None:
        """Find an agent UUID by display name (case-insensitive)."""
        for agent in self.all_agents.values():
            if agent.name.lower() == agent_name.lower():
                return agent.id
        return None

    def resolve_agent(self, identifier: str) -> Any | None:
        """Look up an agent by UUID or name. Returns agent or None."""
        all_agents = self.all_agents
        agent = all_agents.get(identifier)
        if agent:
            return agent
        agent_id = self.resolve_agent_id(identifier)
        if agent_id:
            return all_agents.get(agent_id)
        return None

    def get_hierarchy_snapshot(self) -> dict[str, list[str]]:
        hierarchy: dict[str, list[str]] = {}
        for agent in self._active_agents.values():
            if not agent.master_id:
                continue
            hierarchy.setdefault(agent.master_id, []).append(agent.id)
        return hierarchy

    def get_children(self, agent_id: str) -> list[Any]:
        return [
            agent for agent in self.all_agents.values() if agent.master_id == agent_id
        ]
