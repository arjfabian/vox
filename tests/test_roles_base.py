import asyncio
import gc
import unittest
from unittest.mock import MagicMock

from vox.roles.base import VOXRole, WorkloadHostDeadError


class TestVOXRole(unittest.TestCase):
    def setUp(self):
        self.workload = MagicMock()
        self.role = VOXRole(self.workload)

    def test_workload_property_returns_host(self):
        self.assertIs(self.role.workload, self.workload)

    def test_workload_property_raises_when_host_dead(self):
        del self.workload
        gc.collect()
        with self.assertRaises(WorkloadHostDeadError):
            _ = self.role.workload

    def test_on_registers_handler(self):
        async def handler(**kwargs):
            pass

        self.role.on("test_event")(handler)
        event_map = self.role._handlers
        self.assertIn("test_event", event_map)
        self.assertIs(event_map["test_event"], handler)

    def test_handle_event_returns_true_for_registered(self):
        mock = MagicMock()

        async def h(**kwargs):
            mock(**kwargs)

        self.role.on("greet")(h)
        result = asyncio.run(self.role.handle_event("greet", name="world"))

        self.assertTrue(result)
        mock.assert_called_once_with(name="world")

    def test_handle_event_returns_false_for_unregistered(self):
        result = asyncio.run(self.role.handle_event("nonexistent"))
        self.assertFalse(result)

    def test_multiple_events_independent(self):
        events = []

        async def handler_a(**kwargs):
            events.append("a")

        async def handler_b(**kwargs):
            events.append("b")

        self.role.on("a")(handler_a)
        self.role.on("b")(handler_b)

        asyncio.run(self.role.handle_event("a"))
        self.assertEqual(events, ["a"])

        asyncio.run(self.role.handle_event("b"))
        self.assertEqual(events, ["a", "b"])

    def test_get_event_map_returns_copy(self):
        async def h(**kwargs):
            pass

        self.role.on("evt")(h)

        self.assertIn("evt", self.role._handlers)

    def test_handle_event_passes_kwargs(self):
        results = {}

        async def handler(**kwargs):
            results.update(kwargs)

        self.role.on("cmd")(handler)
        asyncio.run(self.role.handle_event("cmd", cmd="test", args=[1, 2, 3]))

        self.assertEqual(results["cmd"], "test")
        self.assertEqual(results["args"], [1, 2, 3])
