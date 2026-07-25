"""IngressServer — aiohttp-based webhook listener for comm.gateway.

Starts an HTTP server on a configurable port with routes for each
registered adapter.  Incoming payloads are parsed into VOXInboundMessage
and dispatched to the orchestrator via a caller-supplied callback.

The server is started by CommGatewayCapability.boot() and stopped by
shutdown().  A single server instance serves all mounted agents — the
orchestrator routes inbound messages to the correct agent(s).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiohttp import web

from .adapters.base import BaseAdapter
from .models import VOXInboundMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DispatchFn = Callable[[VOXInboundMessage], Awaitable[None]]


class IngressServer:
    """Async HTTP server that ingests webhook payloads and dispatches them."""

    def __init__(
        self,
        adapters: dict[str, BaseAdapter],
        dispatch: DispatchFn,
        host: str = "0.0.0.0",
        port: int = 8001,
    ) -> None:
        self._adapters = adapters
        self._dispatch = dispatch
        self._host = host
        self._port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
        logger.log(LOG_LEVEL_OK, "IngressServer listening on %s:%s", self._host, self._port)

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

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        if "telegram" in self._adapters:
            self._app.router.add_post(
                "/webhook/telegram",
                self._make_handler("telegram"),
            )
            self._app.router.add_get(
                "/webhook/telegram",
                self._handle_get,
            )

        if "webhook" in self._adapters:
            self._app.router.add_post(
                "/webhook/generic",
                self._make_handler("webhook"),
            )

        self._app.router.add_get("/health", self._handle_health)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _make_handler(self, channel: str) -> Callable:
        adapter = self._adapters[channel]

        async def handler(request: web.Request) -> web.Response:
            if not adapter.verify_request(request):
                return web.json_response({"error": "forbidden"}, status=403)

            try:
                raw = await request.json()
            except Exception:
                return web.json_response({"error": "invalid JSON"}, status=400)

            try:
                inbound = adapter.parse_inbound(raw)
            except Exception as exc:
                logger.error("Parse error [%s]: %s", channel, exc)
                return web.json_response({"error": "parse error"}, status=422)

            try:
                await self._dispatch(inbound)
            except Exception as exc:
                logger.error("Dispatch error [%s]: %s", channel, exc)
                return web.json_response({"error": "dispatch error"}, status=500)

            return web.json_response({"ok": True})

        return handler

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "adapters": list(self._adapters.keys()),
        })

    async def _handle_get(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "webhook registered"})
