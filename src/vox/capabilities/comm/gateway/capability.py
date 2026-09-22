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

Architecture note: adapter instances are per bound capability (per workload).
Each bound capability creates and owns its own configured adapter instances
from its own Vault-injected secrets and its own manifest params. Adapter
selection for a particular outbound operation remains an execution-time
concern (``send_text(channel, ...)``).

A single process-level IngressServer is shared across all workloads. The server
owns the inbound webhook routes and the bound-user reference count, and it shuts
down when the final bound user detaches. The first bound capability to boot
with a channel becomes the inbound route owner for that channel and starts that
channel's inbound activity; a later workload with the same channel keeps its own
adapter instance for outbound but does not start a second inbound
poller/handler. A workload never inherits another workload's adapter
configuration or credentials.

Lifecycle: each adapter implements ``start()``/``shutdown()`` as part of the
BaseAdapter contract. Only the route-owning adapter instance is started
(inbound activity); every adapter instance owned by a bound workload is shut
down when that workload detaches.
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

# Process-level handle to the shared ingress server. The server object itself
# owns route ownership and the bound-user reference count; it does not own a
# process-global adapter set.
_shared_server: IngressServer | None = None


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

    async def _build_adapters(
        self,
        dispatch,
    ) -> dict[str, Any]:
        """Instantiate this bound capability's own configured adapters.

        Each adapter is built from this bound's own params and injected Vault
        secrets; a channel is skipped when it is not configured (e.g. no bot
        token for Telegram). The returned instances belong exclusively to this
        bound capability/workload.
        """
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

        return adapters

    async def boot(self) -> None:
        global _shared_server

        port = int(self.GATEWAY_PORT) if self.GATEWAY_PORT else 8001
        host = self.GATEWAY_HOST or "0.0.0.0"

        async def dispatch(message: VOXInboundMessage) -> None:
            delivered = await self._host.dispatch_inbound(
                source="comm.gateway",
                payload=message.model_dump(),
            )
            if not delivered:
                logger.warning("Gateway inbound dropped: no dispatch target")

        # This bound own its adapter instances; never shared across workloads.
        adapters = await self._build_adapters(dispatch)

        if not adapters:
            self.ok("No adapters configured — gateway idle")
            return

        server = _shared_server
        if server is None:
            server = IngressServer(host=host, port=port)
            _shared_server = server

        server.attach()

        owned_channels: set[str] = set()
        for channel, adapter in adapters.items():
            if server.claim_channel(channel, adapter, dispatch):
                owned_channels.add(channel)

        self._adapters = adapters
        self._owned_channels = owned_channels
        self._server = server

        await server.start()

        # Only the route-owning adapter instance performs inbound activity;
        # every other instance for the same channel stays outbound-only.
        for channel in owned_channels:
            await adapters[channel].start()

        if owned_channels:
            self.ok(f"Gateway server listening on {host}:{port}")
        else:
            self.ok(f"Attached to existing gateway server on {host}:{port}")

    async def shutdown(self) -> None:
        global _shared_server

        adapters = getattr(self, "_adapters", {})
        owned_channels = getattr(self, "_owned_channels", set())
        server = getattr(self, "_server", None)

        # Shut down this workload's own adapter instances only. A non-owning
        # telegram adapter has no poller, but its client/resources are still
        # released here.
        for adapter in adapters.values():
            await adapter.shutdown()

        if server is not None:
            for channel in owned_channels:
                server.relinquish_channel(channel)

            last_user = server.detach()
            if last_user:
                await server.stop()
                if _shared_server is server:
                    _shared_server = None

    # --------------------------------------------------------------------------
    # Outbound
    # --------------------------------------------------------------------------

    async def send_message(self, message: VOXOutboundMessage) -> bool:
        """Send an outbound message through this workload's own channel adapter.

        The adapter for ``message.channel`` is selected from this bound
        capability's adapter set — never another workload's — preserving
        per-workload credentials and configuration.
        """
        adapter = getattr(self, "_adapters", {}).get(message.channel)
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