"""HTTP interface over the VOX runtime.

Read-only queries (GET) and lifecycle commands (POST) for agents.
The orchestrator is the single source of truth.
"""

import hmac
import json
import os
from typing import TYPE_CHECKING, Any, Dict

from aiohttp import web

if TYPE_CHECKING:
    from vox.orchestration import VOXOrchestrator


class VOXAPIServer:

    def __init__(self, orchestrator: VOXOrchestrator, port: int = 8000) -> None:
        self._orc = orchestrator
        self._port = port
        self._app = web.Application(middlewares=[self._auth_middleware])
        self._register_routes()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler) -> web.Response:
        """Requires a Bearer token if the VOX_API_TOKEN variable is set up."""
        token = os.environ.get("VOX_API_TOKEN")
        if token:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not hmac.compare_digest(auth_header, f"Bearer {token}"):
                return self._json({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def start(self) -> None:
        host = os.environ.get("VOX_API_HOST", "127.0.0.1")
        token = os.environ.get("VOX_API_TOKEN")

        if not token:
            if host not in ("127.0.0.1", "localhost"):
                raise RuntimeError(
                    f"VOX_API_TOKEN is not set. Refusing to bind to {host} "
                    f"(only 127.0.0.1/localhost allowed without a token)."
                )
            self._orc.logger.warning(
                "VOX_API_TOKEN is not set. Authentication is DISABLED. "
                "Server bound to %s — do not expose to the network.",
                host,
            )

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, self._port)
        await site.start()
        self._orc.logger.info(f"API server running on http://{host}:{self._port}")

    async def shutdown(self) -> None:
        if hasattr(self, "_runner"):
            await self._runner.cleanup()

    def _register_routes(self) -> None:
        routes = [
            ("GET", "/", self._handle_root),
            ("GET", "/fleet", self._handle_fleet),
            ("GET", "/agents", self._handle_agents),
            ("GET", "/agents/{id}", self._handle_agent),
            ("GET", "/capabilities", self._handle_capabilities),
            ("GET", "/agents/{id}/commands", self._handle_agent_commands),
            ("POST", "/agents/{id}/pause", self._handle_pause),
            ("POST", "/agents/{id}/resume", self._handle_resume),
            ("POST", "/agents/{id}/stop", self._handle_stop),
            ("POST", "/agents/{id}/start", self._handle_start),
            ("POST", "/agents/{id}/restart", self._handle_restart),
        ]
        for method, path, handler in routes:
            self._app.router.add_route(method, path, handler)
            if path != "/":
                self._app.router.add_route(method, path + "/", handler)

    async def _handle_root(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        return self._json({
            "system": snapshot.get("system"),
            "capabilities": snapshot.get("capabilities"),
            "agents": snapshot.get("agents"),
        })

    async def _handle_fleet(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        return self._json({
            "system": snapshot.get("system"),
            "hierarchy": snapshot.get("hierarchy"),
        })

    async def _handle_agents(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        return self._json({
            "agents": snapshot.get("agents", []),
            "hierarchy": snapshot.get("hierarchy"),
        })

    async def _handle_agent(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        agent = self._orc._resolve_agent(identifier)
        if not agent:
            return self._json({"error": f"Agent '{identifier}' not found"}, status=404)
        return self._json(agent.describe())

    async def _handle_agent_commands(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        agent = self._orc._resolve_agent(identifier)
        if not agent:
            return self._json({"error": f"Agent '{identifier}' not found"}, status=404)
        cmd_map = agent.get_command_map()
        commands = {name: {"description": info.description} for name, info in cmd_map.items()}
        return self._json({"agent_id": agent.id, "agent_name": agent.name, "commands": commands})

    async def _handle_capabilities(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        caps = snapshot.get("capabilities", [])
        return self._json({"capabilities": caps})

    async def _handle_pause(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        ok = await self._orc.pause_agent(identifier)
        if ok:
            return self._json({"ok": True, "data": f"Agent paused"})
        return self._json({"error": "Failed to pause agent"}, status=400)

    async def _handle_resume(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        ok = await self._orc.resume_agent(identifier)
        if ok:
            return self._json({"ok": True, "data": f"Agent resumed"})
        return self._json({"error": "Failed to resume agent"}, status=400)

    async def _handle_stop(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        agent_id = self._orc.resolve_agent_id(identifier)
        if not agent_id:
            return self._json({"error": f"Agent '{identifier}' not found"}, status=404)
        ok = await self._orc.stop_agent(agent_id)
        if ok:
            return self._json({"ok": True, "data": f"Agent stopped"})
        return self._json({"error": "Failed to stop agent"}, status=400)

    async def _handle_start(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        ok = await self._orc.start_agent_by_name(identifier)
        if ok:
            return self._json({"ok": True, "data": f"Agent started"})
        return self._json({"error": "Failed to start agent"}, status=400)

    async def _handle_restart(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        ok = await self._orc.restart_agent(identifier)
        if ok:
            return self._json({"ok": True, "data": f"Agent restarted"})
        return self._json({"error": "Failed to restart agent"}, status=400)

    @staticmethod
    def _json(data: Dict[str, Any], status: int = 200) -> web.Response:
        return web.Response(
            text=json.dumps(data, indent=2, ensure_ascii=False),
            status=status,
            content_type="application/json",
        )
