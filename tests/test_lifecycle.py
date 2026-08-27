import unittest

from vox.workloads.lifecycle import EventQueue, WorkloadState


class TestWorkloadStateTransitions(unittest.TestCase):
    def _assert_can(self, src: WorkloadState, *targets: WorkloadState) -> None:
        for t in targets:
            self.assertTrue(
                src.can_transition_to(t),
                f"{src} should transition to {t}",
            )

    def _assert_cannot(self, src: WorkloadState, *targets: WorkloadState) -> None:
        for t in targets:
            self.assertFalse(
                src.can_transition_to(t),
                f"{src} should NOT transition to {t}",
            )

    def test_booting_transitions(self):
        self._assert_can(
            WorkloadState.BOOTING,
            WorkloadState.IDLE,
            WorkloadState.ACTIVE,
            WorkloadState.FAILED,
            WorkloadState.STOPPING,
        )
        self._assert_cannot(
            WorkloadState.BOOTING,
            WorkloadState.BOOTING,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPED,
        )

    def test_idle_transitions(self):
        self._assert_can(WorkloadState.IDLE, WorkloadState.BOOTING, WorkloadState.STOPPING)
        self._assert_cannot(
            WorkloadState.IDLE,
            WorkloadState.ACTIVE,
            WorkloadState.FAILED,
            WorkloadState.IDLE,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPED,
        )

    def test_active_transitions(self):
        self._assert_can(WorkloadState.ACTIVE, WorkloadState.PAUSING, WorkloadState.STOPPING)
        self._assert_cannot(
            WorkloadState.ACTIVE,
            WorkloadState.ACTIVE,
            WorkloadState.BOOTING,
            WorkloadState.FAILED,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPED,
        )

    def test_pausing_transitions(self):
        self._assert_can(WorkloadState.PAUSING, WorkloadState.PAUSED, WorkloadState.STOPPING)
        self._assert_cannot(
            WorkloadState.PAUSING,
            WorkloadState.ACTIVE,
            WorkloadState.BOOTING,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.RESUMING,
            WorkloadState.STOPPED,
        )

    def test_paused_transitions(self):
        self._assert_can(WorkloadState.PAUSED, WorkloadState.RESUMING, WorkloadState.STOPPING)
        self._assert_cannot(
            WorkloadState.PAUSED,
            WorkloadState.ACTIVE,
            WorkloadState.BOOTING,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.STOPPED,
        )

    def test_resuming_transitions(self):
        self._assert_can(WorkloadState.RESUMING, WorkloadState.ACTIVE, WorkloadState.STOPPING)
        self._assert_cannot(
            WorkloadState.RESUMING,
            WorkloadState.BOOTING,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPED,
        )

    def test_stopping_transitions(self):
        self._assert_can(WorkloadState.STOPPING, WorkloadState.STOPPED)
        self._assert_cannot(
            WorkloadState.STOPPING,
            WorkloadState.ACTIVE,
            WorkloadState.BOOTING,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPING,
        )

    def test_stopped_transitions(self):
        self._assert_can(WorkloadState.STOPPED, WorkloadState.BOOTING)
        self._assert_cannot(
            WorkloadState.STOPPED,
            WorkloadState.ACTIVE,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPING,
            WorkloadState.STOPPED,
        )

    def test_failed_transitions(self):
        self._assert_can(WorkloadState.FAILED, WorkloadState.BOOTING)
        self._assert_cannot(
            WorkloadState.FAILED,
            WorkloadState.ACTIVE,
            WorkloadState.FAILED,
            WorkloadState.PAUSING,
            WorkloadState.PAUSED,
            WorkloadState.RESUMING,
            WorkloadState.STOPPING,
            WorkloadState.STOPPED,
        )

    def test_str(self):
        self.assertEqual(str(WorkloadState.BOOTING), "BOOTING")
        self.assertEqual(str(WorkloadState.IDLE), "IDLE")
        self.assertEqual(str(WorkloadState.ACTIVE), "ACTIVE")
        self.assertEqual(str(WorkloadState.PAUSING), "PAUSING")
        self.assertEqual(str(WorkloadState.PAUSED), "PAUSED")
        self.assertEqual(str(WorkloadState.RESUMING), "RESUMING")
        self.assertEqual(str(WorkloadState.STOPPING), "STOPPING")
        self.assertEqual(str(WorkloadState.STOPPED), "STOPPED")
        self.assertEqual(str(WorkloadState.FAILED), "FAILED")


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
