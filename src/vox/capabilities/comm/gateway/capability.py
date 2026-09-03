"""comm.gateway — multi-channel communication gateway capability.

Provides inbound webhook ingestion and outbound message delivery across
configured channels (Telegram, WhatsApp, generic HTTP webhook).
Normalises all inbound traffic into VOXInboundMessage before dispatching to the
orchestrator.

This capability is domain-agnostic: no business logic, no ticketing, no CRM.
Pure channel abstraction. The complete public parameter contract (ports, host,
adapter tokens, secrets) is declared across per-adapter ``config.yml`` files and
merged into a single CapabilityContract at load time. Adapters keep their own
internal PARAMS for self-documentation and ``is_configured()`` checks, but the
YAML config files are authoritative.

Architecture note: The IngressServer is shared across all workloads that mount
this capability. The first workload to boot creates the server; subsequent
workloads attach to the existing instance and log an attachment message.
Adapters are also shared since the server routes all inbound traffic through the
host's narrow ``dispatch_inbound`` service, which delegates to the shared
dispatcher that fans out to every workload with comm.gateway mounted.

Shutdown: A reference count (_mounted_workloads_count) tracks how many workloads
have the capability booted. The server is only stopped when the last workload
shuts down, preventing one workload's stop from killing inbound for the rest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vox.capabilities.base import (
    CapabilityContract,
    VOXCapability,
    load_capability_yaml,
    load_config_yml,
)

from .adapters import ADAPTER_REGISTRY
from .models import VOXInboundMessage, VOXOutboundMessage
from .server import IngressServer

logger = logging.getLogger(__name__)

# Shared across all workloads — the server is a singleton resource.
_shared_server: IngressServer | None = None
_shared_adapters: dict[str, Any] = {}
_mounted_workloads_count: int = 0


class CommGatewayCapability(VOXCapability):
    CAPABILITY_NAME = "comm.gateway"

    # --------------------------------------------------------------------------
    # Contract loading — aggregate adapter config.yml files
    # --------------------------------------------------------------------------

    @classmethod
    def load_contract(
        cls,
        capability_file: Path,
    ) -> CapabilityContract | None:
        """Load the gateway contract by merging per-adapter config.yml files.

        The gateway's own ``capability.yml`` provides metadata only (name,
        version, description, provides). Each adapter directory contains a
        ``config.yml`` with ``params`` and ``secrets`` sections that are merged
        into a single CapabilityContract.
        """
        contract = load_capability_yaml(capability_file)

        if contract is None:
            return None

        adapter_dir = capability_file.parent / "adapters"

        if adapter_dir.is_dir():
            for adapter_entry in sorted(adapter_dir.iterdir()):
                config_yml = adapter_entry / "config.yml"

                if not config_yml.is_file():
                    continue

                params, secrets = load_config_yml(config_yml)

                overlap = set(params) & set(contract.secrets)
                if overlap:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"secret(s) already in contract: {sorted(overlap)}"
                    )

                overlap = set(secrets) & set(contract.params)
                if overlap:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"param(s) already in contract: {sorted(overlap)}"
                    )

                conflict = set(params) & set(contract.params)
                if conflict:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"param(s): {sorted(conflict)}"
                    )

                conflict = set(secrets) & set(contract.secrets)
                if conflict:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"secret(s): {sorted(conflict)}"
                    )

                contract.params.update(params)
                contract.secrets.update(secrets)

        cls._contract = contract
        return contract

    # --------------------------------------------------------------------------
    # Adapter parameter introspection
    # --------------------------------------------------------------------------

    @classmethod
    def adapter_param_specs(cls) -> dict[str, dict[str, list[Any]]]:
        """Per-channel param+secret specs from adapter config.yml files."""
        adapter_dir = Path(__file__).resolve().parent / "adapters"
        specs: dict[str, dict[str, list[Any]]] = {}
        for channel in ADAPTER_REGISTRY:
            config_yml = adapter_dir / channel / "config.yml"
            if not config_yml.is_file():
                specs[channel] = {}
                continue
            params, secrets = load_config_yml(config_yml)
            specs[channel] = {
                **{
                    name: [meta.description, meta.default]
                    for name, meta in params.items()
                },
                **{name: [meta.description, ""] for name, meta in secrets.items()},
            }
        return specs

    # --------------------------------------------------------------------------
    # Capability lifecycle
    # --------------------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        global _shared_server, _shared_adapters, _mounted_workloads_count

        if _shared_server is not None:
            _mounted_workloads_count += 1
            self.ok(
                f"Attached to existing gateway server on "
                f"{self.GATEWAY_HOST}:{self.GATEWAY_PORT}"
            )
            return

        port = int(self.GATEWAY_PORT) if self.GATEWAY_PORT else 8001
        host = self.GATEWAY_HOST or "0.0.0.0"

        async def dispatch(message: VOXInboundMessage) -> None:
            delivered = await self._host.dispatch_inbound(
                source="comm.gateway",
                payload=message.model_dump(),
            )
            if not delivered:
                logger.warning("Gateway inbound dropped: no dispatch target")

        # Instantiate every registered adapter, slicing its declared params out
        # of the bound config. A channel is skipped when it is not configured
        # (e.g. no bot token for Telegram).
        adapters: dict[str, Any] = {}

        for channel, adapter_cls in ADAPTER_REGISTRY.items():
            config_yml = (
                Path(__file__).resolve().parent / "adapters" / channel / "config.yml"
            )
            if not config_yml.is_file():
                logger.warning(
                    "Adapter '%s' has no config.yml — skipping",
                    channel,
                )
                continue

            params, secrets = load_config_yml(config_yml)

            adapter_keys = set(params) | set(secrets)

            adapter_config = {
                key: value
                for key, value in {
                    **self._params,
                    **self._secrets,
                }.items()
                if key in adapter_keys
            }

            if not adapter_cls.is_configured(adapter_config):
                continue

            adapters[channel] = adapter_cls(adapter_config, dispatch=dispatch)

        if not adapters:
            self.ok("No adapters configured — gateway idle")
            return

        _shared_adapters = adapters
        _shared_server = IngressServer(
            adapters=adapters,
            dispatch=dispatch,
            host=host,
            port=port,
        )
        await _shared_server.start()
        for adapter in adapters.values():
            if hasattr(adapter, "start"):
                await adapter.start()
        _mounted_workloads_count += 1
        self.ok(f"Gateway server listening on {host}:{port}")

    async def shutdown(self) -> None:
        global _shared_server, _mounted_workloads_count

        _mounted_workloads_count -= 1

        if _mounted_workloads_count <= 0 and _shared_server is not None:
            await _shared_server.stop()
            _shared_server = None

            for adapter in _shared_adapters.values():
                if hasattr(adapter, "shutdown"):
                    await adapter.shutdown()
            _shared_adapters.clear()
            _mounted_workloads_count = 0

    # --------------------------------------------------------------------------
    # Outbound
    # --------------------------------------------------------------------------

    async def send_message(self, message: VOXOutboundMessage) -> bool:
        """Send an outbound message through the appropriate channel adapter."""
        if not _shared_adapters:
            self.error("Gateway has no adapters — cannot send")
            return False

        adapter = _shared_adapters.get(message.channel)
        if adapter is None:
            self.error(f"No adapter for channel: {message.channel}")
            return False

        return await adapter.send_outbound(message)

    async def send_text(
        self,
        channel: str,
        recipient_id: str,
        text: str,
        parse_mode: str = "HTML",
        **metadata: Any,
    ) -> bool:
        """Convenience wrapper: build a VOXOutboundMessage from primitives."""
        msg = VOXOutboundMessage(
            channel=channel,
            recipient_id=recipient_id,
            text=text,
            parse_mode=parse_mode,
            metadata=metadata,
        )
        return await self.send_message(msg)
