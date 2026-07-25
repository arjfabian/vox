"""CapabilityBinder — runtime capability and credentials binder.

Part of the v0.5.0 State-Isolated Hot-Reload architecture.
Orchestrates the discovery, validation, instantiation, and Vault
secret injection for capabilities requested by agent roles.
Provides a sandboxed integration layer between role requirements
and mounted capability instances, keeping the agent class focused
on state and lifecycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from vox.observability import VOXForensicLogger
from vox.security import AgentVault
from vox.security.vault import VaultAccessError

if TYPE_CHECKING:
    from vox.agents.base import VOXAgent
    from vox.capabilities.base import VOXBoundCapability


class CapabilityBinder:
    """Manages the full capability lifecycle for an agent.

    Operates on a reference to its owning ``VOXAgent``, modifying the
    agent's ``capabilities`` and ``roles`` dictionaries directly.
    This is intentional — the binder acts as a service object that
    encapsulates the mounting, vault-injection, and validation logic
    that would otherwise bloat the agent class.
    """

    def __init__(self, agent: VOXAgent, logger: VOXForensicLogger) -> None:
        self._agent = agent
        self.logger = logger

    # ------------------------------------------------------------------
    # Vault initialisation
    # ------------------------------------------------------------------

    def init_vault_sync(self) -> None:
        """Create or attach the per-agent encrypted vault.

        The vault is stored as ``secrets.vault`` in the agent directory.
        Failure is non-fatal — the agent continues without vault access.
        """
        try:
            self._agent._vault = AgentVault(
                self._agent.dir,
                self._agent.id,
                config=self._agent.config,
            )
        except RuntimeError as e:
            self.logger.warning(f"Vault unavailable: {e}")
            self._agent._vault = None

    async def init_vault(self) -> None:
        """Async wrapper around ``init_vault_sync``."""
        self.init_vault_sync()

    # ------------------------------------------------------------------
    # Capability discovery, mounting, and vault injection
    # ------------------------------------------------------------------

    def discover_and_mount(
        self,
        roles_dir: Path,
        system_capabilities: set[str],
    ) -> None:
        """Scan role files, resolve and mount required capabilities.

        For each required capability:
        1. Obtain an instance from the orchestrator's registry.
        2. Mount it on the agent with ``cap.mount(agent, config)``.
        3. Inject vault secrets for sensitive parameters.
        4. Validate that required params are present.
        5. Register exposed commands on the agent.

        Roles whose required capabilities cannot be mounted are disabled.
        """
        # Late import to avoid circular dependency at module level.
        from vox.agents.ast_analyzer import ASTAgentAnalyzer

        agent = self._agent
        needed: set[str] = set(system_capabilities)

        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue
                needed |= ASTAgentAnalyzer.scan_role_capabilities(role_file)

        if not needed:
            return

        self.logger.info(f"Capabilities required by roles: {sorted(needed)}")

        for cap_id in sorted(needed):
            if agent._capability_provider is None:
                agent._degraded = True
                continue

            cap = agent._capability_provider.get_capability_instance(cap_id)
            if not cap:
                self.logger.warning(
                    f"Security warning: capability '{cap_id}' not found — "
                    f"roles depending on it will be disabled"
                )
                agent._degraded = True
                continue

            bound = cap.mount(agent, agent.config)
            bound.logger = self.logger
            agent.capabilities[cap_id] = bound
            self._inject_vault_for_capability(cap_id, bound)

            missing_params = bound.validate_params()
            if missing_params:
                self.logger.warning(
                    f"Capability '{cap_id}' missing required params: {missing_params} — "
                    f"agent will be degraded"
                )
                agent._degraded = True

            self.logger.ok(f"Mounted capability: {cap_id}")

            exposed = getattr(type(cap), "EXPOSED_COMMANDS", [])
            for cmd_def in exposed:
                cmd_name = cmd_def["name"]
                method_name = cmd_def.get("method", cmd_name)
                handler = getattr(bound, method_name)
                agent._capability_commands[cmd_name] = handler
                agent.commands.add(cmd_name)
                self.logger.info(f"  Exposed command: {cmd_name} (via {cap_id}.{method_name})")

        missing = needed - set(agent.capabilities.keys())
        if missing:
            agent._degraded = True
            self._disable_roles_with_missing_capabilities(missing)

    def _inject_vault_for_capability(
        self,
        cap_id: str,
        bound: VOXBoundCapability,
    ) -> None:
        """Replace sensitive params with vault-stored values for one capability."""
        if self._agent._vault is None:
            return
        sensitive = getattr(type(bound._capability), "SENSITIVE_PARAMS", set())
        for key in sensitive:
            vault_val = self._agent._vault.get_sync(cap_id, key)
            if vault_val is not None:
                bound._params[key] = vault_val

    async def inject_vault_secrets(self) -> None:
        """Iterate all mounted capabilities and inject vault secrets.

        Logs warnings for any sensitive params that could not be resolved
        from the vault or config, and disables affected roles.

        Raises ``VaultAccessError`` when the vault is not available and at
        least one ``SENSITIVE_PARAMS`` value is missing from ``bound._params``
        (fail-fast for missing ``VOX_MASTER_KEY``).
        """
        agent = self._agent
        if agent._vault is None:
            missing = [
                (cap_id, key)
                for cap_id, bound in agent.capabilities.items()
                for key in getattr(type(bound._capability), "SENSITIVE_PARAMS", set())
                if bound._params.get(key) is None
            ]
            if missing:
                raise VaultAccessError(
                    f"Agent '{agent.name}' requires vault-managed secrets "
                    f"but VOX_MASTER_KEY is not set."
                )
            return
        for cap_id, bound in agent.capabilities.items():
            sensitive = getattr(type(bound._capability), "SENSITIVE_PARAMS", set())
            missing = []
            for key in sensitive:
                vault_val = await agent._vault.get(cap_id, key)
                if vault_val is not None:
                    bound._params[key] = vault_val
                    self.logger.info(f"Injected vault secret: {cap_id}.{key}")
                elif bound._params.get(key) is None:
                    missing.append(key)
            if not missing:
                continue
            self.logger.warning(
                f"Sensitive params missing for {cap_id}: {', '.join(missing)}. "
                f"Run 'python tools/provision_vault.py --agent {agent.name.lower()}' "
                f"to provision them."
            )
            self._disable_roles_for_capability(cap_id)

    # ------------------------------------------------------------------
    # Role disabling helpers
    # ------------------------------------------------------------------

    def _disable_roles_with_missing_capabilities(self, missing: set[str]) -> None:
        """Remove roles that depend on any of the given capability IDs."""
        for role_name, role in list(self._agent.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())
            if requires & missing:
                self._agent.roles.pop(role_name)
                self.logger.warning(
                    f"Role '{role_name}' disabled — missing capabilities: "
                    f"{sorted(requires & missing)}"
                )

    def _disable_roles_for_capability(self, cap_id: str) -> None:
        """Remove roles that depend on a specific capability."""
        for role_name, role in list(self._agent.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())
            if cap_id in requires:
                self._agent.roles.pop(role_name)
                self.logger.warning(f"Role '{role_name}' disabled due to missing secrets")
