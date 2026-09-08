"""CapabilityBinder — capability mounting and Vault secret binding.

The binder is the runtime boundary between workload configuration, capability
contracts, and the workload Vault.

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
from vox.provider import CapabilityHostProtocol
from vox.security import VaultAccessError, WorkloadVault

if TYPE_CHECKING:
    from vox.capabilities.base import VOXBoundCapability


class CapabilityBinder:
    """Mount capabilities and bind their Vault-managed secrets.

    The binder is a composition mechanism, not part of a capability's business
    implementation. It depends only on ``CapabilityHostProtocol`` for the
    workload services it needs and never reaches into concrete workload
    private state.
    """

    def __init__(
        self,
        host: CapabilityHostProtocol,
        logger: VOXForensicLogger,
    ) -> None:
        self._host = host
        self.logger = logger
        self._role_capabilities: dict[str, set[str]] = {}

    # --------------------------------------------------------------------------
    # Vault initialisation
    # --------------------------------------------------------------------------

    def init_vault_sync(self) -> None:
        try:
            self._host.vault = WorkloadVault(
                self._host.dir,
                self._host.id,
                config=self._host.config,
            )
        except RuntimeError as exc:
            self.logger.warning(f"Vault unavailable: {exc}")
            self._host.vault = None

    async def init_vault(self) -> None:
        self.init_vault_sync()

    # --------------------------------------------------------------------------
    # Capability discovery and mounting
    # --------------------------------------------------------------------------

    def discover_and_mount(
        self,
        roles_dir: Path,
        system_capabilities: set[str],
        loaded_role_names: set[str] | None = None,
    ) -> None:
        """Mount every required capability.

        Capability configuration comes exclusively from capability.yml defaults
        and workload manifest ``overrides[capability_id]``; secrets are injected
        from Vault later, during boot.

        Requirements are attributed per successfully loaded Role: only Role
        files whose stem is in ``loaded_role_names`` are AST-scanned, so a Role
        that failed to load contributes nothing. The mount set is
        ``system_capabilities`` unioned with the loaded Roles' requirements.
        """
        from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer

        host = self._host
        loaded_role_names = loaded_role_names or set()

        role_capabilities: dict[str, set[str]] = {}
        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue
                if role_file.stem not in loaded_role_names:
                    continue
                role_capabilities[role_file.stem] = (
                    ASTWorkloadAnalyzer.scan_role_capabilities(role_file)
                )
        self._role_capabilities = role_capabilities

        needed: set[str] = set(system_capabilities)
        for caps in role_capabilities.values():
            needed |= caps

        if not needed:
            return

        self.logger.info(f"Capabilities required by roles: {sorted(needed)}")

        for cap_id in sorted(needed):
            if host.capability_provider is None:
                self.logger.warning(
                    f"Capability provider unavailable — cannot mount '{cap_id}'"
                )
                host.mark_degraded()
                continue

            cap = host.capability_provider.get_capability_instance(cap_id)

            if cap is None:
                self.logger.warning(
                    f"Security warning: capability '{cap_id}' not found — "
                    f"roles depending on it will be disabled"
                )
                host.mark_degraded()
                continue

            try:
                bound = self._mount_capability(cap_id, cap)
            except (ValueError, TypeError, RuntimeError) as exc:
                self.logger.warning(
                    f"Capability '{cap_id}' could not be mounted: {exc}"
                )
                host.mark_degraded()
                continue

            host.register_capability(cap_id, bound)
            bound.logger = self.logger

            self.logger.ok(f"Mounted capability: {cap_id}")

            self._register_exposed_commands(cap_id, bound)

        missing = needed - set(host.capabilities)

        if missing:
            host.mark_degraded()
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
        host = self._host
        overrides_config = host.config.get("overrides", {})

        if overrides_config is None:
            overrides_config = {}

        if not isinstance(overrides_config, dict):
            raise TypeError("Workload 'overrides' must be a mapping")

        override = overrides_config.get(cap_id, {})

        if override is None:
            override = {}

        if not isinstance(override, dict):
            raise TypeError(f"Override for capability '{cap_id}' must be a mapping")

        return cap.mount(
            host,
            overrides=override,
        )

    def _register_exposed_commands(
        self,
        cap_id: str,
        bound: VOXBoundCapability,
    ) -> None:
        exposed = bound.get_exposed_commands()

        for cmd_def in exposed:
            cmd_name = cmd_def["name"]
            method_name = cmd_def.get("method", cmd_name)

            handler = getattr(bound, method_name)

            self._host.register_capability_command(cmd_name, handler)

            self.logger.info(
                f"  Exposed command: {cmd_name} (via {cap_id}.{method_name})"
            )

    # --------------------------------------------------------------------------
    # Required-secret resolution
    # --------------------------------------------------------------------------

    def _collect_required_secrets(self) -> dict[str, set[str]]:
        """Compute required secrets per capability from AST + YAML contracts.

        A secret is *required* for a capability when:
          1. The YAML contract marks it ``required: true``, AND
          2. At least one role declares it in ``REQUIRED_SECRETS``.

        ``REQUIRED_SECRETS`` is scanned statically from role source files via
        AST; only files belonging to successfully loaded roles are considered.

        Returns ``{cap_id: {secret_names}}`` for capabilities with required
        secrets.
        """
        from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer

        host = self._host
        roles_dir = host.dir / "roles"

        # Union of REQUIRED_SECRETS across loaded role files
        role_required: dict[str, set[str]] = {}
        if roles_dir.exists():
            for role_file in roles_dir.glob("*.py"):
                if role_file.name.startswith("_"):
                    continue
                if role_file.stem not in self._role_capabilities:
                    continue
                for cap_id, names in ASTWorkloadAnalyzer.scan_required_secrets(
                    role_file
                ).items():
                    role_required.setdefault(cap_id, set()).update(names)

        # Intersect with YAML contract's required secrets
        result: dict[str, set[str]] = {}
        for cap_id, bound in host.capabilities.items():
            contract_required = bound.get_required_secret_names()
            if not contract_required:
                continue
            role_names = role_required.get(cap_id, set())
            if not role_names:
                continue
            intersection = contract_required & role_names
            if intersection:
                result[cap_id] = intersection

        return result

    # --------------------------------------------------------------------------
    # Vault secret injection
    # --------------------------------------------------------------------------

    async def inject_vault_secrets(self) -> None:
        """Resolve all declared capability secrets from the workload Vault.

        Secrets have no defaults and cannot be supplied through workload
        configuration. Required secrets (those marked ``required: true`` in the
        YAML contract *and* declared in a role's ``REQUIRED_SECRETS``) cause a
        hard failure when the Vault is unavailable. Optional missing secrets
        only disable the affected roles.
        """
        host = self._host

        required_by_cap: dict[str, set[str]] = self._collect_required_secrets()
        all_secret_caps = {
            cap_id
            for cap_id, bound in host.capabilities.items()
            if bound.get_secret_names()
        }

        if not all_secret_caps:
            return

        if host.vault is None:
            if required_by_cap:
                detail = "; ".join(
                    f"{cap}: {sorted(secrets)}"
                    for cap, secrets in sorted(required_by_cap.items())
                )
                raise VaultAccessError(
                    f"Workload '{host.name}' requires Vault-managed "
                    f"secrets but the workload Vault is unavailable: {detail}"
                )
            self.logger.warning(
                "Vault unavailable — optional secrets will not be injected"
            )
            return

        for cap_id, bound in host.capabilities.items():
            secret_names = bound.get_secret_names()

            if not secret_names:
                continue

            cap_required = required_by_cap.get(cap_id, set())
            missing: list[str] = []

            for secret_name in secret_names:
                value = await host.vault.get(
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

    # --------------------------------------------------------------------------
    # Role disabling
    # --------------------------------------------------------------------------

    def _disable_roles_with_missing_capabilities(
        self,
        missing: set[str],
    ) -> None:
        def _reason(role_name: str, role: Any) -> str | None:
            affected = self._role_capabilities.get(role_name, set()) & missing
            if not affected:
                return None
            return f"missing capabilities: {sorted(affected)}"

        disabled = self._host.disable_roles(_reason)

        for role_name, reason in disabled.items():
            self.logger.warning(f"Role '{role_name}' disabled — {reason}")

    def _disable_roles_for_capability(
        self,
        cap_id: str,
    ) -> None:
        def _reason(role_name: str, role: Any) -> str | None:
            if cap_id not in self._role_capabilities.get(role_name, set()):
                return None
            return f"due to missing Vault secrets for capability '{cap_id}'"

        self._host.mark_degraded()
        disabled = self._host.disable_roles(_reason)

        for role_name, reason in disabled.items():
            self.logger.warning(
                f"Role '{role_name}' disabled {reason}"
            )
