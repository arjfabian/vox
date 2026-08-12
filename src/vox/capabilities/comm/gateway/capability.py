"""comm.gateway — multi-channel communication gateway capability.

Provides inbound webhook ingestion and outbound message delivery across
configured channels (Telegram, WhatsApp, generic HTTP webhook).
Normalises all inbound traffic into VOXInboundMessage before dispatching
to the orchestrator.

This capability is domain-agnostic: no business logic, no ticketing, no
CRM.  Pure channel abstraction.  Channel-specific parameters (bot tokens,
secrets, timeouts) are declared by each adapter in ``adapters/`` and
aggregated here — the gateway never hardcodes a channel's keys.

Architecture note: The IngressServer is shared across all agents that mount
this capability.  The first agent to boot creates the server; subsequent
agents attach to the existing instance and log an attachment message.
Adapters are also shared since the server routes all inbound traffic to the
orchestrator, which fans out to every agent with comm.gateway mounted.

Shutdown: A reference count (_mounted_agents_count) tracks how many agents
have the capability booted.  The server is only stopped when the last agent
shuts down, preventing one agent's stop from killing inbound for the rest.
"""

from __future__ import annotations

import logging
from typing import Any

from vox.capabilities.base import VOXCapability

from .adapters import ADAPTER_REGISTRY
from .models import VOXInboundMessage, VOXOutboundMessage
from .server import IngressServer

logger = logging.getLogger(__name__)

# Shared across all agents — the server is a singleton resource.
_shared_server: IngressServer | None = None
_shared_adapters: dict[str, Any] = {}
_mounted_agents_count: int = 0


class CommGatewayCapability(VOXCapability):
    CAPABILITY_NAME = "comm.gateway"
    # Mutable defaults are intentional; subclasses and the adapter aggregation
    # below override PARAMS per channel.
    PARAMS: dict[str, list[Any]] = {  # noqa: RUF012
        "GATEWAY_PORT": ["HTTP listen port for webhooks", 8001],
        "GATEWAY_HOST": ["HTTP bind address", "0.0.0.0"],
        **{
            param: spec
            for adapter in ADAPTER_REGISTRY.values()
            for param, spec in adapter.PARAMS.items()
        },
    }
    SENSITIVE_PARAMS: set[str] = {  # noqa: RUF012
        param
        for adapter in ADAPTER_REGISTRY.values()
        for param in adapter.SENSITIVE_PARAMS
    }

    # ------------------------------------------------------------------
    # Adapter parameter introspection
    # ------------------------------------------------------------------

    @classmethod
    def adapter_param_specs(cls) -> dict[str, dict[str, list[Any]]]:
        """Nested per-channel param specs: {channel: {param: [desc, default]}}."""
        return {channel: dict(adapter.PARAMS) for channel, adapter in ADAPTER_REGISTRY.items()}

    # ------------------------------------------------------------------
    # Capability lifecycle
    # ------------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        global _shared_server, _shared_adapters, _mounted_agents_count

        if _shared_server is not None:
            _mounted_agents_count += 1
            self.ok(
                f"Attached to existing gateway server on "
                f"{self.GATEWAY_HOST}:{self.GATEWAY_PORT}"
            )
            return

        port = int(self.GATEWAY_PORT) if self.GATEWAY_PORT else 8001
        host = self.GATEWAY_HOST or "0.0.0.0"
        orchestrator = self._agent.orchestrator

        async def dispatch(message: VOXInboundMessage) -> None:
            if orchestrator is None:
                return
            from vox.security import SecurityError

            try:
                await orchestrator.dispatch_inbound_message(
                    source="comm.gateway",
                    payload=message.model_dump(),
                )
            except SecurityError as exc:
                logger.warning("Inbound dispatch blocked by guardrail: %s", exc)

        # Instantiate every registered adapter, slicing its declared params
        # out of the bound config. A channel is skipped when it is not
        # configured (e.g. no bot token for Telegram).
        adapters: dict[str, Any] = {}
        for channel, adapter_cls in ADAPTER_REGISTRY.items():
            adapter_config = {key: getattr(self, key) for key in adapter_cls.PARAMS}
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
        _mounted_agents_count += 1
        self.ok(f"Gateway server listening on {host}:{port}")

    async def shutdown(self) -> None:
        global _shared_server, _mounted_agents_count

        _mounted_agents_count -= 1

        if _mounted_agents_count <= 0 and _shared_server is not None:
            await _shared_server.stop()
            _shared_server = None

            for adapter in _shared_adapters.values():
                if hasattr(adapter, "shutdown"):
                    await adapter.shutdown()
            _shared_adapters.clear()
            _mounted_agents_count = 0

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

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
