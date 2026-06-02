"""Read-only HTTP interface over the VOX runtime.

Zero business logic, no direct agent manipulation.
The orchestrator is the single source of truth.
"""

import json
from typing import TYPE_CHECKING, Any, Dict

from aiohttp import web

if TYPE_CHECKING:
    from vox.orchestration import VOXOrchestrator


class VOXAPIServer:

    def __init__(self, orchestrator: VOXOrchestrator, port: int = 8000) -> None:
        self._orc = orchestrator
        self._port = port
        self._app = web.Application()
        self._register_routes()

    async def start(self) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._port)
        await site.start()
        self._orc.logger.info(f"API server running on http://0.0.0.0:{self._port}")

    def _register_routes(self) -> None:
        self._app.router.add_get("/", self._handle_root)
        self._app.router.add_get("/fleet", self._handle_fleet)
        self._app.router.add_get("/agents", self._handle_agents)
        self._app.router.add_get("/agents/{id}", self._handle_agent)
        self._app.router.add_get("/capabilities", self._handle_capabilities)

    async def _handle_root(self, request: web.Request) -> web.Response:
        return self._json(self._orc.get_fleet_snapshot())

    async def _handle_fleet(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        return self._json({
            "system": snapshot.get("system"),
            "hierarchy": snapshot.get("hierarchy"),
        })

    async def _handle_agents(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        return self._json({"agents": snapshot.get("agents", [])})

    async def _handle_agent(self, request: web.Request) -> web.Response:
        identifier = request.match_info["id"]
        snapshot = self._orc.get_fleet_snapshot()
        agents = snapshot.get("agents", [])
        agent = next((a for a in agents if a["id"] == identifier), None)
        if not agent:
            agent = next(
                (a for a in agents if a["name"].lower() == identifier.lower()),
                None,
            )
        if not agent:
            return self._json({"error": f"Agent '{identifier}' not found"}, status=404)
        return self._json(agent)

    async def _handle_capabilities(self, request: web.Request) -> web.Response:
        snapshot = self._orc.get_fleet_snapshot()
        caps = snapshot.get("capabilities", [])
        return self._json({"capabilities": caps})

    @staticmethod
    def _json(data: Dict[str, Any], status: int = 200) -> web.Response:
        return web.Response(
            text=json.dumps(data, indent=2, ensure_ascii=False),
            status=status,
            content_type="application/json",
        )
