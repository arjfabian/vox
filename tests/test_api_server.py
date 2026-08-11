import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import vox.api_server as api_server_module
from vox.api_server import VOXAPIServer


def _req(method: str = "GET", path: str = "/", match_info=None, headers=None):
    kw = {"headers": {"HOST": "localhost"}}
    if headers:
        kw["headers"].update(headers)
    if match_info:
        kw["match_info"] = match_info
    return make_mocked_request(method, path, **kw)


class TestVOXAPIServerHandlers(unittest.TestCase):
    def setUp(self):
        self.orchestrator = MagicMock()
        self.orchestrator.logger = MagicMock()
        self.orchestrator.get_fleet_snapshot.return_value = {
            "system": {"version": "VOX+1.0"},
            "capabilities": [],
            "agents": [],
            "hierarchy": {},
        }
        self.server = VOXAPIServer(self.orchestrator)

    def _check_json(self, resp, expected_status: int = 200):
        self.assertEqual(resp.status, expected_status)
        self.assertEqual(resp.content_type, "application/json")
        body = resp.body
        return json.loads(body.decode()) if body else {}

    def _run(self, coro):
        return asyncio.run(coro)

    def test_root_handler(self):
        resp = self._run(self.server._handle_root(_req()))
        data = self._check_json(resp)
        self.assertIn("system", data)
        self.assertIn("capabilities", data)
        self.assertIn("agents", data)

    def test_fleet_handler(self):
        resp = self._run(self.server._handle_fleet(_req(path="/fleet")))
        data = self._check_json(resp)
        self.assertIn("system", data)
        self.assertIn("hierarchy", data)

    def test_agents_handler(self):
        resp = self._run(self.server._handle_agents(_req(path="/agents")))
        data = self._check_json(resp)
        self.assertIn("agents", data)
        self.assertIn("hierarchy", data)

    def test_capabilities_handler(self):
        resp = self._run(self.server._handle_capabilities(_req(path="/capabilities")))
        data = self._check_json(resp)
        self.assertIn("capabilities", data)

    def test_agent_not_found_returns_404(self):
        self.orchestrator._resolve_agent.return_value = None
        resp = self._run(
            self.server._handle_agent(_req(match_info={"id": "nonexistent"}))
        )
        self.assertEqual(resp.status, 404)

    def test_agent_found_returns_describe(self):
        mock_agent = MagicMock()
        mock_agent.describe.return_value = {"id": "a1", "name": "tina"}
        self.orchestrator._resolve_agent.return_value = mock_agent
        resp = self._run(self.server._handle_agent(_req(match_info={"id": "a1"})))
        data = self._check_json(resp)
        self.assertEqual(data["name"], "tina")

    def test_agent_commands(self):
        mock_agent = MagicMock()
        mock_agent.id = "a1"
        mock_agent.name = "tina"
        mock_cmd = MagicMock()
        mock_cmd.description = "Does something"
        mock_agent.get_command_map.return_value = {"doit": mock_cmd}
        self.orchestrator._resolve_agent.return_value = mock_agent
        resp = self._run(
            self.server._handle_agent_commands(_req(match_info={"id": "a1"}))
        )
        data = self._check_json(resp)
        self.assertIn("commands", data)
        self.assertIn("doit", data["commands"])

    def test_agent_commands_not_found_returns_404(self):
        self.orchestrator._resolve_agent.return_value = None
        resp = self._run(
            self.server._handle_agent_commands(_req(match_info={"id": "ghost"}))
        )
        self.assertEqual(resp.status, 404)

    def test_pause_agent_returns_ok(self):
        self.orchestrator.resolve_agent_id.return_value = "a1"
        self.orchestrator.pause_agent = AsyncMock(return_value=True)
        resp = self._run(
            self.server._handle_pause(_req(method="POST", match_info={"id": "tina"}))
        )
        data = self._check_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"], "Agent paused")

    def test_pause_agent_failure_returns_400(self):
        self.orchestrator.pause_agent = AsyncMock(return_value=False)
        resp = self._run(
            self.server._handle_pause(_req(method="POST", match_info={"id": "tina"}))
        )
        self.assertEqual(resp.status, 400)

    def test_resume_agent_returns_ok(self):
        self.orchestrator.resume_agent = AsyncMock(return_value=True)
        resp = self._run(
            self.server._handle_resume(_req(method="POST", match_info={"id": "tina"}))
        )
        data = self._check_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"], "Agent resumed")

    def test_resume_agent_failure_returns_400(self):
        self.orchestrator.resume_agent = AsyncMock(return_value=False)
        resp = self._run(
            self.server._handle_resume(_req(method="POST", match_info={"id": "tina"}))
        )
        self.assertEqual(resp.status, 400)

    def test_stop_agent_not_found_returns_404(self):
        self.orchestrator.resolve_agent_id.return_value = None
        resp = self._run(
            self.server._handle_stop(_req(method="POST", match_info={"id": "tina"}))
        )
        self.assertEqual(resp.status, 404)

    def test_stop_agent_returns_ok(self):
        self.orchestrator.resolve_agent_id.return_value = "a1"
        self.orchestrator.stop_agent = AsyncMock(return_value=True)
        resp = self._run(
            self.server._handle_stop(_req(method="POST", match_info={"id": "a1"}))
        )
        data = self._check_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"], "Agent stopped")

    def test_start_agent_returns_ok(self):
        self.orchestrator.start_agent_by_name = AsyncMock(return_value=True)
        resp = self._run(
            self.server._handle_start(_req(method="POST", match_info={"id": "tina"}))
        )
        data = self._check_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"], "Agent started")

    def test_start_agent_failure_returns_400(self):
        self.orchestrator.start_agent_by_name = AsyncMock(return_value=False)
        resp = self._run(
            self.server._handle_start(_req(method="POST", match_info={"id": "tina"}))
        )
        self.assertEqual(resp.status, 400)

    def test_restart_agent_returns_ok(self):
        self.orchestrator.restart_agent = AsyncMock(return_value=True)
        resp = self._run(
            self.server._handle_restart(_req(method="POST", match_info={"id": "tina"}))
        )
        data = self._check_json(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"], "Agent restarted")

    def test_restart_agent_failure_returns_400(self):
        self.orchestrator.restart_agent = AsyncMock(return_value=False)
        resp = self._run(
            self.server._handle_restart(_req(method="POST", match_info={"id": "tina"}))
        )
        self.assertEqual(resp.status, 400)

    def test_json_response_format(self):
        resp = self._run(self.server._handle_root(_req()))
        self.assertEqual(resp.content_type, "application/json")


class TestVOXAPIServerSecurity(unittest.TestCase):
    def setUp(self):
        self.orchestrator = MagicMock()
        self.orchestrator.logger = MagicMock()
        self.server = VOXAPIServer(self.orchestrator)
        self.dummy_handler = AsyncMock(return_value=web.Response(text="success"))

    @patch.dict(os.environ, {"VOX_API_TOKEN": "super-secret-token"})
    def test_middleware_blocks_unauthorized_request(self):
        req = _req(method="GET", path="/fleet")
        resp = asyncio.run(self.server._auth_middleware(req, self.dummy_handler))
        self.assertEqual(resp.status, 401)
        body = json.loads(resp.body.decode())
        self.assertEqual(body["error"], "unauthorized")
        self.dummy_handler.assert_not_called()

    @patch.dict(os.environ, {"VOX_API_TOKEN": "super-secret-token"})
    def test_middleware_allows_authorized_request(self):
        headers = {"Authorization": "Bearer super-secret-token"}
        req = _req(method="GET", path="/fleet", headers=headers)
        resp = asyncio.run(self.server._auth_middleware(req, self.dummy_handler))
        self.assertEqual(resp.text, "success")
        self.dummy_handler.assert_called_once_with(req)

    @patch.dict(os.environ, {}, clear=True)
    def test_middleware_allows_everything_if_token_unset(self):
        req = _req(method="GET", path="/fleet")
        resp = asyncio.run(self.server._auth_middleware(req, self.dummy_handler))
        self.assertEqual(resp.text, "success")
        self.dummy_handler.assert_called_once_with(req)


class TestVOXAPIServerInit(unittest.TestCase):
    def _make(self):
        orc = MagicMock()
        orc.logger = MagicMock()
        return VOXAPIServer(orc)

    def _noop_web(self):
        """Mock aiohttp.web so start() does not open a real socket."""
        patcher = patch.object(api_server_module, "web", autospec=False)
        mock_web = patcher.start()
        self.addCleanup(patcher.stop)
        mock_runner = AsyncMock()
        mock_site = AsyncMock()
        mock_web.AppRunner.return_value = mock_runner
        mock_web.TCPSite.return_value = mock_site
        return mock_web

    def test_port_defaults_to_8000(self):
        server = self._make()
        self.assertEqual(server._port, 8000)

    def test_custom_port(self):
        server = VOXAPIServer(MagicMock(logger=MagicMock()), port=9000)
        self.assertEqual(server._port, 9000)

    def test_routes_are_registered(self):
        server = self._make()
        self.assertTrue(len(server._app.router.routes()) > 0)

    def test_shutdown_noop_when_not_started(self):
        server = self._make()
        asyncio.run(server.shutdown())

    @patch.dict(os.environ, {"VOX_API_HOST": "0.0.0.0"}, clear=True)
    def test_start_raises_when_token_unset_host_not_localhost(self):
        server = self._make()
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(server.start())
        self.assertIn("VOX_API_TOKEN", str(ctx.exception))
        self.assertIn("0.0.0.0", str(ctx.exception))

    @patch.dict(os.environ, {}, clear=True)
    def test_start_warns_when_token_unset_host_is_localhost(self):
        server = self._make()
        self._noop_web()
        asyncio.run(server.start())
        server._orc.logger.warning.assert_called_once()

    @patch.dict(
        os.environ, {"VOX_API_TOKEN": "s3cret", "VOX_API_HOST": "0.0.0.0"}, clear=True
    )
    def test_start_with_token_on_any_host_succeeds(self):
        server = self._make()
        self._noop_web()
        try:
            asyncio.run(server.start())
        except RuntimeError:
            self.fail("start() raised RuntimeError unexpectedly")
        server._orc.logger.info.assert_called()
