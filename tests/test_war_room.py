"""Tests for VOXWarRoom, VOXWarRoomMaster, and comm.gateway broadcast."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from vox.orchestration.war_room import VOXWarRoom, VOXWarRoomMaster, WarRoomMessage

# ------------------------------------------------------------------
# WarRoomMessage
# ------------------------------------------------------------------


class TestWarRoomMessage(unittest.TestCase):
    def test_default_fields(self):
        msg = WarRoomMessage(source="workload3", payload={"event": "test"})
        self.assertEqual(msg.source, "workload3")
        self.assertEqual(msg.target, "everyone")
        self.assertEqual(msg.payload, {"event": "test"})
        self.assertEqual(msg.read_by, {})
        self.assertIsNotNone(msg.message_id)

    def test_to_vox_message(self):
        from vox.messaging.models import VOXMessage

        msg = WarRoomMessage(
            source="workload3",
            payload={"event": "vps_breach", "severity": "CRITICAL"},
        )
        vox = msg.to_vox_message()
        self.assertIsInstance(vox, VOXMessage)
        self.assertEqual(vox.message_source, "workload3")
        self.assertEqual(vox.details, msg.payload)

    def test_acknowledge_tracking(self):
        msg = WarRoomMessage(source="system")
        self.assertNotIn("workload2", msg.read_by)
        msg.read_by["workload2"] = "2026-01-01T00:00:00"
        self.assertIn("workload2", msg.read_by)


# ------------------------------------------------------------------
# VOXWarRoom
# ------------------------------------------------------------------


class TestVOXWarRoom(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.war_room = VOXWarRoom()

    async def test_publish_enqueues_and_records_history(self):
        msg = WarRoomMessage(source="workload3", payload={"event": "test"})
        await self.war_room.publish(msg)
        self.assertIn(msg, self.war_room._history)
        self.assertEqual(self.war_room._queue.qsize(), 1)

    async def test_acknowledge_marks_read(self):
        msg = WarRoomMessage(source="system")
        await self.war_room.publish(msg)
        ok = await self.war_room.acknowledge(msg.message_id, "workload2")
        self.assertTrue(ok)
        self.assertIn("workload2", msg.read_by)

    async def test_acknowledge_unknown_id_returns_false(self):
        ok = await self.war_room.acknowledge("nonexistent", "workload1")
        self.assertFalse(ok)

    async def test_get_unread_excludes_acknowledged(self):
        msg = WarRoomMessage(source="system")
        await self.war_room.publish(msg)
        await self.war_room.acknowledge(msg.message_id, "workload2")
        unread = self.war_room.get_unread("workload2")
        self.assertEqual(unread, [])
        unread_workload1 = self.war_room.get_unread("workload1")
        self.assertEqual(len(unread_workload1), 1)

    async def test_get_history_with_since_filter(self):
        msg1 = WarRoomMessage(source="a", payload={"seq": 1})
        msg2 = WarRoomMessage(source="b", payload={"seq": 2})
        await self.war_room.publish(msg1)
        await self.war_room.publish(msg2)
        all_msgs = self.war_room.get_history()
        self.assertEqual(len(all_msgs), 2)

    async def test_flush_to_disk_drains_queue(self):
        msg = WarRoomMessage(source="system")
        await self.war_room.publish(msg)
        self.assertEqual(self.war_room._queue.qsize(), 1)
        self.war_room.flush_to_disk()
        self.assertEqual(self.war_room._queue.qsize(), 0)
        self.assertIn(msg, self.war_room._history)


# ------------------------------------------------------------------
# VOXWarRoomMaster
# ------------------------------------------------------------------


class TestVOXWarRoomMaster(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.war_room = VOXWarRoom()
        self.broadcast_fn = AsyncMock(return_value=True)
        self.orchestrator = MagicMock()
        self.orchestrator.active_workloads = {}
        self.master = VOXWarRoomMaster(
            war_room=self.war_room,
            broadcast_fn=self.broadcast_fn,
            orchestrator=self.orchestrator,
        )

    async def test_start_and_stop(self):
        await self.master.start()
        self.assertTrue(self.master._running)
        self.assertIsNotNone(self.master._dispatch_task)
        await self.master.stop()
        self.assertFalse(self.master._running)

    async def test_dispatch_fans_out_to_workloads(self):
        workload1 = MagicMock()
        workload1.emit = AsyncMock()
        workload2 = MagicMock()
        workload2.emit = AsyncMock()
        self.orchestrator.active_workloads = {"a1": workload1, "a2": workload2}

        msg = WarRoomMessage(source="system", payload={"event": "alert"})
        await self.war_room.publish(msg)

        await self.master.start()
        await asyncio.sleep(0.2)
        await self.master.stop()

        workload1.emit.assert_called_once()
        workload2.emit.assert_called_once()
        args, _ = workload1.emit.call_args
        self.assertEqual(args[0], "on_war_room_alert")

    async def test_dispatch_mirrors_to_broadcast_fn(self):
        msg = WarRoomMessage(source="system", payload={"event": "breach"})
        await self.war_room.publish(msg)

        await self.master.start()
        await asyncio.sleep(0.2)
        await self.master.stop()

        self.broadcast_fn.assert_called_once()
        called_text = self.broadcast_fn.call_args[0][0]
        self.assertIn("breach", called_text)
        self.assertIn("Alert", called_text)

    async def test_format_alert_contains_source(self):
        msg = WarRoomMessage(
            source="workload3",
            payload={"event": "intrusion", "severity": "HIGH"},
        )
        text = VOXWarRoomMaster._format_alert(msg)
        self.assertIn("workload3", text)
        self.assertIn("intrusion", text)
        self.assertIn("HIGH", text)

    async def test_dispatch_handles_empty_queue_gracefully(self):
        await self.master.start()
        await asyncio.sleep(0.3)
        await self.master.stop()


# ------------------------------------------------------------------
# Panic shutdown integration
# ------------------------------------------------------------------


class TestPanicShutdown(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.uds_path = "/tmp/test_panic.sock"
        self.logger = MagicMock()

    def _make_orc_with_workloads(self):
        from vox.orchestration.base import VOXOrchestrator

        orc = VOXOrchestrator(config=self.config, logger=self.logger)
        return orc

    def test_panic_shutdown_purges_vault_keys(self):
        orc = self._make_orc_with_workloads()
        vault = MagicMock()
        vault._key = b"secret_key_data_32_bytes_long!!"
        workload = MagicMock()
        workload._vault = vault
        workload._tasks = []
        orc.active_workloads["a1"] = workload

        orc.panic_shutdown()

        self.assertIsNone(vault._key)
        self.assertIsNone(workload._vault)
        self.logger.critical.assert_any_call("PANIC SHUTDOWN initiated")

    def test_panic_shutdown_cancels_workload_tasks(self):
        orc = self._make_orc_with_workloads()
        task = MagicMock()
        workload = MagicMock()
        workload._tasks = [task]
        workload._vault = None
        orc.active_workloads["a1"] = workload

        orc.panic_shutdown()

        task.cancel.assert_called_once()


# ------------------------------------------------------------------
# War room message routing in dispatch_inbound_message
# ------------------------------------------------------------------


class TestAlertRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.uds_path = "/tmp/test_routing.sock"
        self.logger = MagicMock()

    async def test_alert_type_routed_to_war_room(self):
        from vox.orchestration.base import VOXOrchestrator

        orc = VOXOrchestrator(config=self.config, logger=self.logger)
        payload = {"type": "alert", "event": "breach", "severity": "CRITICAL"}
        await orc.dispatch_inbound_message("workload3", payload)
        history = orc._war_room.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].source, "workload3")
        self.assertEqual(history[0].payload, payload)

    async def test_non_alert_message_skips_war_room(self):
        from vox.orchestration.base import VOXOrchestrator

        orc = VOXOrchestrator(config=self.config, logger=self.logger)
        payload = {"type": "chat", "content": "hello"}
        await orc.dispatch_inbound_message("comm.gateway", payload)
        history = orc._war_room.get_history()
        self.assertEqual(len(history), 0)
