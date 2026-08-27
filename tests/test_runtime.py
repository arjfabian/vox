import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from vox.runtime.control_plane import handle_control_command
from vox.runtime.daemon import run_vox
from vox.runtime.factory import build_vox
from vox.runtime.models import VOXRuntime, VOXRuntimeConfig


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
    def test_api_server_required(self):
        server = MagicMock()
        runtime = VOXRuntime(
            config=MagicMock(),
            logger=MagicMock(),
            orchestrator=MagicMock(),
            api_server=server,
        )
        self.assertIs(runtime.api_server, server)

    def test_runtime_rejects_missing_api_server(self):
        with self.assertRaises(TypeError):
            VOXRuntime(
                config=MagicMock(),
                logger=MagicMock(),
                orchestrator=MagicMock(),
            )


class TestBuildVox(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.log_path = "/tmp/vox-test.log"
        self.config.uds_path = "/tmp/vox-test.sock"
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.api_server = MagicMock()

    @patch("vox.runtime.factory.VOXOrchestrator")
    @patch("vox.runtime.factory.VOXAPIServer")
    def test_build_vox_wires_components(self, mock_api_cls, mock_orc_cls):
        mock_orc_cls.return_value = self.orchestrator
        mock_api_cls.return_value = self.api_server

        runtime = asyncio.run(build_vox(config=self.config, logger=self.logger))

        mock_orc_cls.assert_called_once_with(config=self.config, logger=self.logger)
        mock_api_cls.assert_called_once_with(self.orchestrator)
        self.assertIs(runtime.config, self.config)
        self.assertIs(runtime.orchestrator, self.orchestrator)
        self.assertIs(runtime.api_server, self.api_server)


class TestHandleControlCommand(unittest.TestCase):
    def setUp(self):
        self.runtime = MagicMock()
        self.logger = MagicMock()
        self.writer = MagicMock()
        self.writer.drain = AsyncMock()
        self.writer.wait_closed = AsyncMock()
        self.reader = AsyncMock()

    async def _send_command(self, cmd: str, args: list[str] | None = None):
        payload = json.dumps({"cmd": cmd, "args": args or []}) + "\n"
        self.reader.readline = AsyncMock(return_value=payload.encode())
        await handle_control_command(
            self.reader,
            self.writer,
            self.runtime,
            self.logger,
        )

    def _decode_response(self):
        call_args = self.writer.write.call_args
        if call_args is None:
            return None
        raw = call_args[0][0]
        return json.loads(raw.decode().strip())

    def test_status_command(self):
        self.runtime.orchestrator.get_fleet_snapshot.return_value = {
            "system": {},
            "workloads": [],
            "capabilities": [],
        }
        asyncio.run(self._send_command("status"))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("data", resp)

    def test_list_command(self):
        self.runtime.orchestrator.get_fleet_snapshot.return_value = {
            "workloads": [{"id": "a1", "name": "workload1"}],
        }
        asyncio.run(self._send_command("list"))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(resp["data"]["workloads"]), 1)

    def test_stop_command_with_args(self):
        self.runtime.orchestrator.resolve_workload_id.return_value = "a1"
        self.runtime.orchestrator.stop_workload = AsyncMock(return_value=True)
        asyncio.run(self._send_command("stop", ["workload1"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("workload1", resp["data"])

    def test_stop_command_without_args(self):
        asyncio.run(self._send_command("stop"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))

    def test_restart_command(self):
        self.runtime.orchestrator.restart_workload = AsyncMock(return_value=True)
        asyncio.run(self._send_command("restart", ["workload1"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])

    def test_start_command(self):
        self.runtime.orchestrator.start_workload_by_name = AsyncMock(return_value=True)
        asyncio.run(self._send_command("start", ["workload1"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])

    def test_pause_command(self):
        self.runtime.orchestrator.pause_workload = AsyncMock(return_value=True)
        asyncio.run(self._send_command("pause", ["workload1"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("workload1", resp["data"])

    def test_pause_command_without_args(self):
        asyncio.run(self._send_command("pause"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))

    def test_resume_command(self):
        self.runtime.orchestrator.resume_workload = AsyncMock(return_value=True)
        asyncio.run(self._send_command("resume", ["workload1"]))
        resp = self._decode_response()
        self.assertTrue(resp["ok"])
        self.assertIn("workload1", resp["data"])

    def test_resume_command_without_args(self):
        asyncio.run(self._send_command("resume"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))

    def test_unknown_command(self):
        asyncio.run(self._send_command("fly"))
        resp = self._decode_response()
        self.assertIsNone(resp.get("ok"))
        self.assertIn("Unknown command", resp["error"])

    def test_empty_data_returns_cleanly(self):
        self.reader.readline = AsyncMock(return_value=b"")
        asyncio.run(
            handle_control_command(
                self.reader,
                self.writer,
                self.runtime,
                self.logger,
            )
        )
        self.writer.write.assert_not_called()

    def test_invalid_json_returns_error(self):
        self.reader.readline = AsyncMock(return_value=b"not json\n")
        asyncio.run(
            handle_control_command(
                self.reader,
                self.writer,
                self.runtime,
                self.logger,
            )
        )
        resp = self._decode_response()
        self.assertIsNotNone(resp)
        self.assertFalse(resp["ok"])


class TestRunVox(unittest.TestCase):
    def setUp(self):
        self.runtime = MagicMock()
        self.runtime.orchestrator.boot = AsyncMock()
        self.runtime.orchestrator.shutdown = AsyncMock()
        self.runtime.api_server.start = AsyncMock()
        self.runtime.api_server.shutdown = AsyncMock()
        self.logger = MagicMock()

    def test_run_vox_boots_orchestrator(self):
        self.runtime.orchestrator.boot.return_value = True
        with (
            patch(
                "vox.runtime.daemon.start_control_plane",
                new=AsyncMock(),
            ),
            patch(
                "vox.runtime.daemon._keepalive",
                new=AsyncMock(),
            ),
        ):
            asyncio.run(run_vox(self.runtime, self.logger))
            self.runtime.orchestrator.boot.assert_awaited_once()
            self.runtime.api_server.start.assert_called_once()
            self.runtime.orchestrator.shutdown.assert_awaited_once()

    def test_run_vox_exits_if_no_operative_workloads(self):
        self.runtime.orchestrator.boot.return_value = False
        with (
            patch(
                "vox.runtime.daemon.start_control_plane",
                new=AsyncMock(),
            ),
            patch(
                "vox.runtime.daemon._keepalive",
                new=AsyncMock(),
            ),
        ):
            asyncio.run(run_vox(self.runtime, self.logger))
            self.runtime.orchestrator.shutdown.assert_awaited_once()

    def test_run_vox_shuts_down_on_error(self):
        self.runtime.orchestrator.boot.side_effect = RuntimeError("fail")
        asyncio.run(run_vox(self.runtime, self.logger))
        self.runtime.orchestrator.shutdown.assert_awaited_once()
