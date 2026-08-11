"""comm.gateway — multi-channel communication gateway capability.

Provides inbound webhook ingestion and outbound message delivery across
configured channels (Telegram, generic HTTP webhook, and future adapters).
Normalises all inbound traffic into VOXInboundMessage before dispatching
to the orchestrator.

This capability is domain-agnostic: no business logic, no ticketing, no
CRM.  Pure channel abstraction.

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

from .models import VOXInboundMessage, VOXOutboundMessage
from .server import IngressServer

logger = logging.getLogger(__name__)

# Shared across all agents — the server is a singleton resource.
_shared_server: IngressServer | None = None
_shared_adapters: dict[str, Any] = {}
_mounted_agents_count: int = 0


class CommGatewayCapability(VOXCapability):
    CAPABILITY_NAME = "comm.gateway"
    # noqa: RUF012 — mutable defaults are intentional; each agent binding may
    # override PARAMS with different channel configs (Telegram, WhatsApp, etc.).
    PARAMS = {
        "GATEWAY_PORT": ["HTTP listen port for webhooks", 8001],
        "GATEWAY_HOST": ["HTTP bind address", "0.0.0.0"],
        "GATEWAY_WEBHOOK_SECRET": ["Shared secret for generic webhook validation", ""],
        "TELEGRAM_BOT_TOKEN": ["Telegram Bot API token", None],
        "TELEGRAM_WEBHOOK_SECRET": ["Telegram webhook secret token", ""],
        "TELEGRAM_LONG_TIMEOUT": ["Telegram getUpdates long-poll timeout (seconds)", 25],
    }
    SENSITIVE_PARAMS = {
        "GATEWAY_WEBHOOK_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
    }

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

        from .adapters.telegram import TelegramAdapter
        from .adapters.webhook import WebhookAdapter

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

        adapters: dict[str, Any] = {}

        if self.TELEGRAM_BOT_TOKEN:
            adapters["telegram"] = TelegramAdapter(
                {
                    "TELEGRAM_BOT_TOKEN": self.TELEGRAM_BOT_TOKEN,
                    "TELEGRAM_WEBHOOK_SECRET": self.TELEGRAM_WEBHOOK_SECRET,
                    "TELEGRAM_LONG_TIMEOUT": self.TELEGRAM_LONG_TIMEOUT,
                },
                dispatch=dispatch,
            )

        adapters["webhook"] = WebhookAdapter(
            {
                "GATEWAY_WEBHOOK_SECRET": self.GATEWAY_WEBHOOK_SECRET,
            }
        )

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

    async def send_broadcast(self, text: str) -> bool:
        """Agent-facing convenience — sends text via the default channel.

        Resolves channel (``"telegram"``) and recipient
        (``TELEGRAM_USER_ID``) from the agent's config automatically.
        """
        channel = "telegram"
        recipient_id = self._agent.config.get("TELEGRAM_USER_ID", "")
        if not recipient_id:
            self.error("No TELEGRAM_USER_ID configured — cannot broadcast")
            return False
        return await self.send_text(channel, recipient_id, text)
