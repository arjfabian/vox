import asyncio
import json
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from vox.runtime.models import VOXRuntimeConfig, VOXRuntime
from vox.runtime.control_plane import handle_control_command
from vox.runtime.daemon import run_vox


class TestVOXRuntimeConfig(unittest.TestCase):

    def test_default_uds_path(self):
        cfg = VOXRuntimeConfig()
        self.assertEqual(str(cfg.uds_path), "/tmp/vox.sock")

    def test_default_keepalive_interval(self):
        cfg = VOXRuntimeConfig()
        self.assertEqual(cfg.keepalive_interval, 3600)

    def test_custom_values(self):
        cfg = VOXRuntimeConfig(uds_path="/tmp/custom.sock", keepalive_interval=60)
        self.assertEqual(str(cfg.uds_path), "/tmp/custom.sock")
        self.assertEqual(cfg.keepalive_interval, 60)


class TestVOXRuntime(unittest.TestCase):

    def test_api_server_defaults_to_none(self):
        runtime = VOXRuntime(
            config=MagicMock(),
            logger=MagicMock(),
            orchestrator=MagicMock(),
        )
        self.assertIsNone(runtime.api_server)

    def test_api_server_can_be_set(self):
        server = MagicMock()
        runtime = VOXRuntime(
            config=MagicMock(),
            logger=MagicMock(),
            orchestrator=MagicMock(),
            api_server=server,
        )
        self.assertIs(runtime.api_server, server)


class TestHandleControlCommand(unittest.TestCase):

    def setUp(self):
        self.runtime = MagicMock()
        self.logger = MagicMock()
        self.writer = MagicMock()
        self.writer.drain = AsyncMock()
        self.writer.wait_closed = AsyncMock()
        self.reader = AsyncMock()

    async def _send_command(self, cmd: str, args: list[str] = None):
        payload = json.dumps({"cmd": cmd, "args": args or []}) + "\n"
        self.reader.readline = AsyncMock(return_value=payload.encode())
        await handle_control_command(
            self.reader, self.writer, self.runtime, self.logger,
        )

    def _decode_response(self):
        call_args = self.writer.write.call_args
        if call_args is None:
            return None
        raw = call_args[0][0]
        return json.loads(raw.decode().strip())

    def test_status_command(self):
        self.runtime.orchestrator.get_fleet_snapshot.return_value = {
            "system": {}, "agents": [], "capabilities": [],
        }
        asyncio.run(self._send_command("status"))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("data", resp)

    def test_list_command(self):
        self.runtime.orchestrator.get_fleet_snapshot.return_value = {
            "agents": [{"id": "a1", "name": "tina"}],
        }
        asyncio.run(self._send_command("list"))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(resp["data"]["agents"]), 1)

    def test_stop_command_with_args(self):
        self.runtime.orchestrator.resolve_agent_id.return_value = "a1"
        self.runtime.orchestrator.stop_agent = AsyncMock(return_value=True)
        asyncio.run(self._send_command("stop", ["tina"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("tina", resp["data"])

    def test_stop_command_without_args(self):
        asyncio.run(self._send_command("stop"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))

    def test_restart_command(self):
        self.runtime.orchestrator.restart_agent = AsyncMock(return_value=True)
        asyncio.run(self._send_command("restart", ["tina"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])

    def test_start_command(self):
        self.runtime.orchestrator.start_agent_by_name = AsyncMock(return_value=True)
        asyncio.run(self._send_command("start", ["tina"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])

    def test_unknown_command(self):
        asyncio.run(self._send_command("fly"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))
        self.assertIn("Unknown command", resp["error"])

    def test_empty_data_returns_cleanly(self):
        self.reader.readline = AsyncMock(return_value=b"")
        asyncio.run(handle_control_command(
            self.reader, self.writer, self.runtime, self.logger,
        ))
        self.writer.write.assert_not_called()

    def test_invalid_json_returns_error(self):
        self.reader.readline = AsyncMock(return_value=b"not json\n")
        asyncio.run(handle_control_command(
            self.reader, self.writer, self.runtime, self.logger,
        ))
        resp = self._decode_response()
        self.assertIsNotNone(resp)
        self.assertFalse(resp["ok"])


class TestRunVox(unittest.TestCase):

    def setUp(self):
        self.runtime = MagicMock()
        self.runtime.orchestrator.boot = AsyncMock()
        self.runtime.orchestrator.shutdown = AsyncMock()
        self.runtime.api_server.start = AsyncMock()
        self.logger = MagicMock()

    def test_run_vox_boots_orchestrator(self):
        self.runtime.orchestrator.boot.return_value = True
        with patch(
            "vox.runtime.daemon.start_control_plane",
            new=AsyncMock(),
        ), patch(
            "vox.runtime.daemon._keepalive",
            new=AsyncMock(),
        ):
            asyncio.run(run_vox(self.runtime, self.logger))
            self.runtime.orchestrator.boot.assert_awaited_once()
            self.runtime.api_server.start.assert_called_once()
            self.runtime.orchestrator.shutdown.assert_awaited_once()

    def test_run_vox_exits_if_no_operative_agents(self):
        self.runtime.orchestrator.boot.return_value = False
        with patch(
            "vox.runtime.daemon.start_control_plane",
            new=AsyncMock(),
        ), patch(
            "vox.runtime.daemon._keepalive",
            new=AsyncMock(),
        ):
            asyncio.run(run_vox(self.runtime, self.logger))
            self.runtime.orchestrator.shutdown.assert_awaited_once()

    def test_run_vox_shuts_down_on_error(self):
        self.runtime.orchestrator.boot.side_effect = RuntimeError("fail")
        asyncio.run(run_vox(self.runtime, self.logger))
        self.runtime.orchestrator.shutdown.assert_awaited_once()
