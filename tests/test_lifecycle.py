import unittest

from vox.agents.lifecycle import AgentState, EventQueue


class TestAgentStateTransitions(unittest.TestCase):
    def _assert_can(self, src: AgentState, *targets: AgentState) -> None:
        for t in targets:
            self.assertTrue(
                src.can_transition_to(t),
                f"{src} should transition to {t}",
            )

    def _assert_cannot(self, src: AgentState, *targets: AgentState) -> None:
        for t in targets:
            self.assertFalse(
                src.can_transition_to(t),
                f"{src} should NOT transition to {t}",
            )

    def test_booing_transitions(self):
        self._assert_can(
            AgentState.BOOTING,
            AgentState.IDLE,
            AgentState.ACTIVE,
            AgentState.FAILED,
            AgentState.STOPPING,
        )
        self._assert_cannot(
            AgentState.BOOTING,
            AgentState.BOOTING,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPED,
        )

    def test_idle_transitions(self):
        self._assert_can(AgentState.IDLE, AgentState.BOOTING, AgentState.STOPPING)
        self._assert_cannot(
            AgentState.IDLE,
            AgentState.ACTIVE,
            AgentState.FAILED,
            AgentState.IDLE,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPED,
        )

    def test_active_transitions(self):
        self._assert_can(AgentState.ACTIVE, AgentState.PAUSING, AgentState.STOPPING)
        self._assert_cannot(
            AgentState.ACTIVE,
            AgentState.ACTIVE,
            AgentState.BOOTING,
            AgentState.FAILED,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPED,
        )

    def test_pausing_transitions(self):
        self._assert_can(AgentState.PAUSING, AgentState.PAUSED, AgentState.STOPPING)
        self._assert_cannot(
            AgentState.PAUSING,
            AgentState.ACTIVE,
            AgentState.BOOTING,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.RESUMING,
            AgentState.STOPPED,
        )

    def test_paused_transitions(self):
        self._assert_can(AgentState.PAUSED, AgentState.RESUMING, AgentState.STOPPING)
        self._assert_cannot(
            AgentState.PAUSED,
            AgentState.ACTIVE,
            AgentState.BOOTING,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.STOPPED,
        )

    def test_resuming_transitions(self):
        self._assert_can(AgentState.RESUMING, AgentState.ACTIVE, AgentState.STOPPING)
        self._assert_cannot(
            AgentState.RESUMING,
            AgentState.BOOTING,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPED,
        )

    def test_stopping_transitions(self):
        self._assert_can(AgentState.STOPPING, AgentState.STOPPED)
        self._assert_cannot(
            AgentState.STOPPING,
            AgentState.ACTIVE,
            AgentState.BOOTING,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPING,
        )

    def test_stopped_transitions(self):
        self._assert_can(AgentState.STOPPED, AgentState.BOOTING)
        self._assert_cannot(
            AgentState.STOPPED,
            AgentState.ACTIVE,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPING,
            AgentState.STOPPED,
        )

    def test_failed_transitions(self):
        self._assert_can(AgentState.FAILED, AgentState.BOOTING)
        self._assert_cannot(
            AgentState.FAILED,
            AgentState.ACTIVE,
            AgentState.FAILED,
            AgentState.PAUSING,
            AgentState.PAUSED,
            AgentState.RESUMING,
            AgentState.STOPPING,
            AgentState.STOPPED,
        )

    def test_str(self):
        self.assertEqual(str(AgentState.BOOTING), "BOOTING")
        self.assertEqual(str(AgentState.IDLE), "IDLE")
        self.assertEqual(str(AgentState.ACTIVE), "ACTIVE")
        self.assertEqual(str(AgentState.PAUSING), "PAUSING")
        self.assertEqual(str(AgentState.PAUSED), "PAUSED")
        self.assertEqual(str(AgentState.RESUMING), "RESUMING")
        self.assertEqual(str(AgentState.STOPPING), "STOPPING")
        self.assertEqual(str(AgentState.STOPPED), "STOPPED")
        self.assertEqual(str(AgentState.FAILED), "FAILED")


class TestEventQueue(unittest.TestCase):
    def test_enqueue_and_drain(self):
        q = EventQueue(capacity=3)
        self.assertTrue(q.enqueue("evt1", {"a": 1}))
        self.assertTrue(q.enqueue("evt2", {"b": 2}))
        self.assertEqual(q.size, 2)
        events = q.drain()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_name, "evt1")
        self.assertEqual(events[1].kwargs, {"b": 2})
        self.assertEqual(q.size, 0)

    def test_dropped_when_full(self):
        q = EventQueue(capacity=2)
        q.enqueue("a", {})
        q.enqueue("b", {})
        self.assertFalse(q.enqueue("c", {}))
        self.assertEqual(q.dropped, 1)
        self.assertEqual(q.size, 2)

    def test_dropped_counter_accumulates(self):
        q = EventQueue(capacity=1)
        q.enqueue("a", {})
        q.enqueue("b", {})
        q.enqueue("c", {})
        self.assertEqual(q.dropped, 2)

    def test_drain_clears_queue(self):
        q = EventQueue(capacity=5)
        q.enqueue("x", {})
        q.drain()
        self.assertEqual(q.size, 0)
        self.assertEqual(q.dropped, 0)

    def test_empty_drain_returns_empty_list(self):
        q = EventQueue(capacity=5)
        self.assertEqual(q.drain(), [])
