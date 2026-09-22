"""IngressServer — aiohttp-based webhook listener for comm.gateway.

Starts an HTTP server on a configurable port serving inbound webhook routes.
Incoming payloads are parsed by the route-owning adapter into VOXInboundMessage
and dispatched to the orchestrator via that adapter's caller-supplied
callback.

The server is a process-level shared resource: every bound comm.gateway
capability attaches to a single IngressServer instance. The server owns the
inbound routes and the bound-user reference count; it does **not** own a
process-global adapter set.

Route ownership: an inbound webhook path has exactly one owner within the
process. The first bound capability that boots with a channel and claims it
becomes the route owner for that channel and starts that channel's inbound
activity. A later workload with the same channel keeps its own adapter
instance for outbound operations but must not start a second inbound
poller/handler for an already-owned route.

The server remains alive while at least one bound capability is attached and
shuts down when the final bound user detaches.

Routes are registered generically from each adapter's ``WEBHOOK_PATH``, so a
new channel needs no server changes. POST serves the inbound payload; GET
serves a provider subscription handshake when the adapter implements one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

from .adapters.base import BaseAdapter
from .models import VOXInboundMessage

logger = logging.getLogger(__name__)

DispatchFn = Callable[[VOXInboundMessage], Awaitable[None]]


class IngressServer:
    """Process-shared HTTP server that ingests webhook payloads.

    Owns the bound-user reference count and the per-channel inbound route
    ownership. Adapter instances themselves stay with the bound capability
    that created them; only the route-owning adapter per channel is held here
    for inbound handling.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8001,
    ) -> None:
        self._host = host
        self._port = port

        # channel -> (route-owning adapter, its dispatch). Each channel's
        # inbound webhook path has a single owner within the process.
        self._inbound_adapters: dict[str, BaseAdapter] = {}
        self._channel_dispatch: dict[str, DispatchFn] = {}
        # Number of bound capabilities currently attached to this server.
        self._user_count = 0

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task | None = None
        self._health_registered = False
        # Webhook paths already registered in the aiohttp app. Routes are
        # stored once per path and outlive ownership changes: on reclaim the
        # handler simply resolves the new owner dynamically (404 while
        # unclaimed). Keeping this prevents a re-claimed channel from crashing
        # on a duplicate aiohttp route registration.
        self._registered_paths: set[str] = set()

    # --------------------------------------------------------------------------
    # Ownership / reference counting
    # --------------------------------------------------------------------------

    @property
    def user_count(self) -> int:
        """Number of bound capabilities currently attached to this server."""
        return self._user_count

    @property
    def route_owners(self) -> dict[str, BaseAdapter]:
        """Read-only view of per-channel inbound route owners."""
        return dict(self._inbound_adapters)

    def attach(self) -> None:
        """Register one bound capability as a user of this server."""
        self._user_count += 1

    def detach(self) -> bool:
        """Release one bound capability; ``True`` when the last user detached."""
        self._user_count -= 1
        if self._user_count <= 0:
            self._user_count = 0
            return True
        return False

    def claim_channel(
        self,
        channel: str,
        adapter: BaseAdapter,
        dispatch: DispatchFn,
    ) -> bool:
        """Claim inbound route ownership for ``channel``.

        The first claimant wins: its adapter instance becomes the route owner
        (and the only one that performs inbound activity for the channel).
        Later claimants keep their own adapter for outbound but do not own the
        route. Returns ``True`` when this call became the owner.
        """
        if channel in self._inbound_adapters:
            return False

        self._inbound_adapters[channel] = adapter
        self._channel_dispatch[channel] = dispatch

        if self._app is not None:
            self._register_route(channel)
            self._ensure_health_route()

        return True

    def relinquish_channel(self, channel: str) -> None:
        """Drop inbound route ownership for ``channel``.

        Called when the route-owning bound capability detaches. The webhook
        path remains registered but returns 404 until another bound capability
        claims the channel.
        """
        self._inbound_adapters.pop(channel, None)
        self._channel_dispatch.pop(channel, None)

    # --------------------------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------------------------

    async def start(self) -> None:
        if self._app is not None:
            return
        self._app = web.Application()
        self._register_routes()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        self._task = asyncio.create_task(site.start())
        from vox.observability.constants import LOG_LEVEL_OK

        logger.log(
            LOG_LEVEL_OK,
            "IngressServer listening on %s:%s",
            self._host,
            self._port,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("IngressServer stopped")

    # --------------------------------------------------------------------------
    # Route registration
    # --------------------------------------------------------------------------

    def _register_routes(self) -> None:
        # Register each route-owning adapter's webhook route: POST for inbound,
        # GET for provider subscription handshake (if implemented) or a generic
        # 200. Duplicate claims are prevented by claim_channel().
        for channel in list(self._inbound_adapters):
            self._register_route(channel)

        self._ensure_health_route()

    def _ensure_health_route(self) -> None:
        if self._health_registered:
            return
        self._app.router.add_get("/health", self._handle_health)
        self._health_registered = True

    def _register_route(self, channel: str) -> None:
        adapter = self._inbound_adapters[channel]
        path = getattr(adapter, "WEBHOOK_PATH", "")
        if not path:
            return
        if path in self._registered_paths:
            return
        self._registered_paths.add(path)
        self._app.router.add_post(path, self._make_handler(channel))
        base_verify = BaseAdapter.handle_verification
        has_custom = type(adapter).handle_verification is not base_verify
        if has_custom:
            self._app.router.add_get(
                path,
                self._make_verification_handler(channel),
            )
        else:
            self._app.router.add_get(path, self._handle_get)

    # --------------------------------------------------------------------------
    # Handlers
    # --------------------------------------------------------------------------

    def _make_handler(self, channel: str) -> Callable:
        async def handler(request: web.Request) -> web.Response:
            adapter = self._inbound_adapters.get(channel)
            if adapter is None:
                return web.json_response({"error": "no route owner"}, status=404)

            # Read raw body first: body-signing channels (e.g. Meta/Whatsapp)
            # verify against the exact bytes.
            # aiohttp caches the payload, so request.json() below reuses what
            # was read here.
            body = await request.read()
            if not adapter.verify_request(request, body):
                return web.json_response({"error": "forbidden"}, status=403)

            try:
                raw = await request.json()
            except Exception:  # noqa: BLE001 — invalid JSON returns 400
                return web.json_response({"error": "invalid JSON"}, status=400)

            try:
                inbound = adapter.parse_inbound(raw)
                inbound = await adapter.resolve_attachment(inbound)
            except Exception:
                logger.exception("Parse error [%s]", channel)
                return web.json_response({"error": "parse error"}, status=422)

            dispatch = self._channel_dispatch.get(channel)
            if dispatch is None:
                return web.json_response({"error": "no route owner"}, status=404)

            try:
                await dispatch(inbound)
            except Exception:
                logger.exception("Dispatch error [%s]", channel)
                return web.json_response({"error": "dispatch error"}, status=500)

            return web.json_response({"ok": True})

        return handler

    def _make_verification_handler(self, channel: str) -> Callable:
        async def handler(request: web.Request) -> web.Response:
            adapter = self._inbound_adapters.get(channel)
            if adapter is None:
                return web.Response(status=404)
            challenge = adapter.handle_verification(dict(request.query))
            if challenge is None:
                return web.Response(status=403)
            # Providers expect the raw challenge echoed back, not JSON.
            return web.Response(text=challenge)

        return handler

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "adapters": list(self._inbound_adapters.keys()),
            }
        )

    async def _handle_get(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "webhook registered"})