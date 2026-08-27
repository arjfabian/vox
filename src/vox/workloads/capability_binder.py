"""CapabilityBinder — capability mounting and Vault secret binding.

The binder is the runtime boundary between workload configuration,
capability contracts, and the workload Vault.

Configuration flow:

    manifest.yml
        │
        └── overrides[capability_id]
                │
                ▼
        VOXCapability.mount()
                │
                ▼
        VOXBoundCapability._params

Secrets flow:

    workload Vault
        │
        └── capability_id / secret_name
                │
                ▼
        VOXBoundCapability._secrets

Capability contracts are authoritative. Parameters and secrets are separate
namespaces and are never interchangeable.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from vox.observability import VOXForensicLogger
from vox.security import WorkloadVault
from vox.security.vault import VaultAccessError

if TYPE_CHECKING:
    from vox.capabilities.base import VOXBoundCapability
    from vox.workloads.base import VOXWorkload


class CapabilityBinder:
    """Mount capabilities and bind their Vault-managed secrets."""

    def __init__(
        self,
        workload: VOXWorkload,
        logger: VOXForensicLogger,
    ) -> None:
        self._workload = workload
        self.logger = logger

    # ------------------------------------------------------------------
    # Vault initialisation
    # ------------------------------------------------------------------

    def init_vault_sync(self) -> None:
        """Create or attach the workload-local Vault."""
        try:
            self._workload._vault = WorkloadVault(
                self._workload.dir,
                self._workload.id,
                config=self._workload.config,
            )
        except RuntimeError as exc:
            self.logger.warning(f"Vault unavailable: {exc}")
            self._workload._vault = None

    async def init_vault(self) -> None:
        """Initialize the workload Vault."""
        self.init_vault_sync()

    # ------------------------------------------------------------------
    # Capability discovery and mounting
    # ------------------------------------------------------------------

    def discover_and_mount(
        self,
        roles_dir: Path,
        system_capabilities: set[str],
    ) -> None:
        """Discover role requirements and mount all required capabilities.

        Capability configuration comes exclusively from:

          1. capability.yml defaults
          2. workload manifest ``overrides[capability_id]``

        Secrets are not resolved during mounting. They are injected from
        Vault during workload boot.
        """
        from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer

        workload = self._workload
        needed: set[str] = set(system_capabilities)

        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue

                needed |= ASTWorkloadAnalyzer.scan_role_capabilities(role_file)

        if not needed:
            return

        self.logger.info(f"Capabilities required by roles: {sorted(needed)}")

        for cap_id in sorted(needed):
            if workload._capability_provider is None:
                self.logger.warning(
                    f"Capability provider unavailable — cannot mount '{cap_id}'"
                )
                workload._degraded = True
                continue

            cap = workload._capability_provider.get_capability_instance(cap_id)

            if cap is None:
                self.logger.warning(
                    f"Security warning: capability '{cap_id}' not found — "
                    f"roles depending on it will be disabled"
                )
                workload._degraded = True
                continue

            try:
                bound = self._mount_capability(cap_id, cap)
            except (ValueError, TypeError, RuntimeError) as exc:
                self.logger.warning(
                    f"Capability '{cap_id}' could not be mounted: {exc}"
                )
                workload._degraded = True
                continue

            workload.capabilities[cap_id] = bound
            bound.logger = self.logger

            self.logger.ok(f"Mounted capability: {cap_id}")

            self._register_exposed_commands(cap_id, cap, bound)

        missing = needed - set(workload.capabilities)

        if missing:
            workload._degraded = True
            self._disable_roles_with_missing_capabilities(missing)

    def _mount_capability(
        self,
        cap_id: str,
        cap: Any,
    ) -> VOXBoundCapability:
        """Mount a capability using its contract and manifest override.

        There is deliberately no legacy configuration fallback.

        ``manifest.yml`` is the only workload-side source of parameter
        overrides. Unknown parameters are rejected by ``VOXCapability.mount``.
        """
        workload = self._workload
        overrides_config = workload.config.get("overrides", {})

        if overrides_config is None:
            overrides_config = {}

        if not isinstance(overrides_config, dict):
            raise ValueError("Workload 'overrides' must be a mapping")

        override = overrides_config.get(cap_id, {})

        if override is None:
            override = {}

        if not isinstance(override, dict):
            raise ValueError(f"Override for capability '{cap_id}' must be a mapping")

        return cap.mount(
            workload,
            overrides=override,
        )

    def _register_exposed_commands(
        self,
        cap_id: str,
        cap: Any,
        bound: VOXBoundCapability,
    ) -> None:
        """Register commands exposed by a mounted capability."""
        exposed = getattr(type(cap), "EXPOSED_COMMANDS", [])

        for cmd_def in exposed:
            cmd_name = cmd_def["name"]
            method_name = cmd_def.get("method", cmd_name)

            handler = getattr(bound, method_name)

            self._workload._capability_commands[cmd_name] = handler
            self._workload.commands.add(cmd_name)

            self.logger.info(
                f"  Exposed command: {cmd_name} (via {cap_id}.{method_name})"
            )

    # ------------------------------------------------------------------
    # Required-secret resolution
    # ------------------------------------------------------------------

    def _collect_required_secrets(self) -> dict[str, set[str]]:
        """Compute required secrets per capability from AST + YAML contracts.

        A secret is *required* for a capability when:
          1. The YAML contract marks it ``required: true``, AND
          2. At least one role declares it in ``REQUIRED_SECRETS``.

        ``REQUIRED_SECRETS`` is scanned statically from role source files
        via AST (consistent with ``REQUIRES`` scanning).

        Returns ``{cap_id: {secret_names}}`` for capabilities with
        required secrets.
        """
        from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer

        workload = self._workload
        roles_dir = workload.dir / "roles"

        # Union of REQUIRED_SECRETS across all role files
        role_required: dict[str, set[str]] = {}
        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue
                for cap_id, names in ASTWorkloadAnalyzer.scan_required_secrets(role_file).items():
                    role_required.setdefault(cap_id, set()).update(names)

        # Intersect with YAML contract's required secrets
        result: dict[str, set[str]] = {}
        for cap_id, bound in workload.capabilities.items():
            contract_required = bound._capability._ensure_contract().required_secret_names
            if not contract_required:
                continue
            role_names = role_required.get(cap_id, set())
            if not role_names:
                continue
            intersection = contract_required & role_names
            if intersection:
                result[cap_id] = intersection

        return result

    # ------------------------------------------------------------------
    # Vault secret injection
    # ------------------------------------------------------------------

    def _inject_vault_for_capability(
        self,
        cap_id: str,
        bound: VOXBoundCapability,
        required_names: set[str] | None = None,
    ) -> list[str]:
        """Inject all declared secrets for a capability.

        Returns the names of *required* secrets that could not be resolved.

        Secrets are stored exclusively in ``bound._secrets`` and never in
        ``bound._params``.
        """
        vault = self._workload._vault

        if vault is None:
            if required_names:
                return [
                    n for n in bound._capability.get_secret_names()
                    if n in required_names
                ]
            return []

        missing: list[str] = []

        for secret_name in bound._capability.get_secret_names():
            value = vault.get_sync(cap_id, secret_name)

            if value is None:
                if required_names and secret_name in required_names:
                    missing.append(secret_name)
                continue

            bound._secrets[secret_name] = value
            self.logger.info(f"Injected Vault secret: {cap_id}.{secret_name}")

        return missing

    async def inject_vault_secrets(self) -> None:
        """Resolve all declared capability secrets from the workload Vault.

        Secrets have no defaults and cannot be supplied through workload
        configuration.  Required secrets (those marked ``required: true`` in
        the YAML contract *and* declared in a role's ``REQUIRED_SECRETS``)
        cause a hard failure when the Vault is unavailable.  Optional
        missing secrets only disable the affected roles.
        """
        workload = self._workload

        required_by_cap: dict[str, set[str]] = self._collect_required_secrets()
        all_secret_caps = {
            cap_id
            for cap_id, bound in workload.capabilities.items()
            if bound._capability.get_secret_names()
        }

        if not all_secret_caps:
            return

        if workload._vault is None:
            if required_by_cap:
                detail = "; ".join(
                    f"{cap}: {sorted(secrets)}"
                    for cap, secrets in sorted(required_by_cap.items())
                )
                raise VaultAccessError(
                    f"Workload '{workload.name}' requires Vault-managed "
                    f"secrets but the workload Vault is unavailable: {detail}"
                )
            self.logger.warning(
                "Vault unavailable — optional secrets will not be injected"
            )
            return

        for cap_id, bound in workload.capabilities.items():
            secret_names = bound._capability.get_secret_names()

            if not secret_names:
                continue

            cap_required = required_by_cap.get(cap_id, set())
            missing: list[str] = []

            for secret_name in secret_names:
                value = await workload._vault.get(
                    cap_id,
                    secret_name,
                )

                if value is None:
                    missing.append(secret_name)
                    continue

                bound._secrets[secret_name] = value

                self.logger.info(f"Injected Vault secret: {cap_id}.{secret_name}")

            if not missing:
                continue

            missing_required = [s for s in missing if s in cap_required]
            missing_optional = [s for s in missing if s not in cap_required]

            if missing_required:
                self.logger.warning(
                    f"Required Vault secrets missing for {cap_id}: "
                    f"{', '.join(missing_required)}"
                )
                self._disable_roles_for_capability(cap_id)

            if missing_optional:
                self.logger.info(
                    f"Optional Vault secrets missing for {cap_id}: "
                    f"{', '.join(missing_optional)}"
                )

    # ------------------------------------------------------------------
    # Role disabling
    # ------------------------------------------------------------------

    def _disable_roles_with_missing_capabilities(
        self,
        missing: set[str],
    ) -> None:
        """Disable roles requiring unavailable capabilities."""
        for role_name, role in list(self._workload.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())

            affected = requires & missing

            if not affected:
                continue

            self._workload.roles.pop(role_name)

            self.logger.warning(
                f"Role '{role_name}' disabled — "
                f"missing capabilities: {sorted(affected)}"
            )

    def _disable_roles_for_capability(
        self,
        cap_id: str,
    ) -> None:
        """Disable roles requiring a capability with missing secrets."""
        for role_name, role in list(self._workload.roles.items()):
            requires = getattr(type(role), "REQUIRES", set())

            if cap_id not in requires:
                continue

            self._workload.roles.pop(role_name)

            self.logger.warning(
                f"Role '{role_name}' disabled due to missing "
                f"Vault secrets for capability '{cap_id}'"
            )
