"""Tests for the sliding-window rate limiter."""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vox.security.rate_limiter import RateLimiter, RateLimitError
from vox.workloads.base import VOXWorkload


class TestRateLimiter(unittest.TestCase):
    """Unit tests for the standalone RateLimiter class."""

    def test_default_construction(self):
        rl = RateLimiter()
        self.assertEqual(rl.max_calls, 30)
        self.assertEqual(rl.window, 60)
        self.assertEqual(rl.get_utilization(), 0.0)

    def test_custom_limits(self):
        rl = RateLimiter(max_calls=5, window_seconds=10)
        self.assertEqual(rl.max_calls, 5)
        self.assertEqual(rl.window, 10)

    @patch("vox.security.rate_limiter.time")
    def test_allow_within_limit(self, mock_time):
        mock_time.time.return_value = 1000.0
        rl = RateLimiter(max_calls=3, window_seconds=10)
        self.assertTrue(rl.allow())
        self.assertTrue(rl.allow())
        self.assertTrue(rl.allow())

    @patch("vox.security.rate_limiter.time")
    def test_deny_when_over_limit(self, mock_time):
        mock_time.time.return_value = 1000.0
        rl = RateLimiter(max_calls=3, window_seconds=10)
        self.assertTrue(rl.allow())
        self.assertTrue(rl.allow())
        self.assertTrue(rl.allow())
        self.assertFalse(rl.allow())

    @patch("vox.security.rate_limiter.time")
    def test_check_limit_raises_on_breach(self, mock_time):
        mock_time.time.return_value = 1000.0
        rl = RateLimiter(max_calls=2, window_seconds=5)
        rl.check_limit()
        rl.check_limit()
        with self.assertRaises(RateLimitError):
            rl.check_limit()

    @patch("vox.security.rate_limiter.time")
    def test_window_slides_and_allows_again(self, mock_time):
        mock_time.time.return_value = 1000.0
        rl = RateLimiter(max_calls=2, window_seconds=10)
        self.assertTrue(rl.allow())
        self.assertTrue(rl.allow())
        self.assertFalse(rl.allow())
        mock_time.time.return_value = 1011.0
        self.assertTrue(rl.allow())

    @patch("vox.security.rate_limiter.time")
    def test_get_utilization(self, mock_time):
        mock_time.time.return_value = 1000.0
        rl = RateLimiter(max_calls=10, window_seconds=60)
        self.assertEqual(rl.get_utilization(), 0.0)
        for _ in range(5):
            rl.allow()
        self.assertAlmostEqual(rl.get_utilization(), 50.0)

    def test_zero_max_calls_does_not_divide_by_zero(self):
        rl = RateLimiter(max_calls=0, window_seconds=60)
        self.assertEqual(rl.get_utilization(), 0.0)
        self.assertFalse(rl.allow())


class TestWorkloadRateLimiterIntegration(unittest.TestCase):
    """Integration tests — RateLimiter wired into VOXWorkload.emit()."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_rate_workload_{id(self)}"
        roles_dir = self.tmp / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text(
            "name: RateTestWorkload\nid: rate-test-uuid\n"
            "rate_limit_max_calls: 3\nrate_limit_window: 60\n"
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        mock_cap = MagicMock()
        mock_bound = MagicMock()
        mock_bound.initialize = AsyncMock()
        mock_bound.boot = AsyncMock()
        mock_bound.validate_params = MagicMock(return_value=[])
        mock_cap.mount.return_value = mock_bound
        mock_cap.CAPABILITY_NAME = "comm.gateway"
        mock_cap.get_secret_names.return_value = []
        mock_bound._capability = mock_cap
        type(mock_cap).EXPOSED_COMMANDS = []
        self.orchestrator.get_capability_instance.return_value = mock_cap

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("vox.security.rate_limiter.time")
    def test_emit_respected_when_under_limit(self, mock_time):
        mock_time.time.return_value = 1000.0
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        boot_ok = asyncio.run(workload.boot())
        self.assertTrue(boot_ok)

        workload.event_router["test_event"] = []

        async def emit_two():
            await workload.emit("test_event")
            await workload.emit("test_event")

        asyncio.run(emit_two())

        self.assertEqual(workload.rate_limiter_utilization, 100.0)

    @patch("vox.security.rate_limiter.time")
    def test_emit_blocked_when_over_limit(self, mock_time):
        mock_time.time.return_value = 1000.0
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        asyncio.run(workload.boot())
        workload.event_router["test_event"] = []

        async def spam():
            for _ in range(5):
                await workload.emit("test_event")

        asyncio.run(spam())

        self.assertGreater(workload.rate_limiter_utilization, 0.0)
        workload.logger.critical.assert_called()

    @patch("vox.security.rate_limiter.time")
    def test_rate_limiter_utilization_property(self, mock_time):
        mock_time.time.return_value = 1000.0
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        asyncio.run(workload.boot())
        # on_boot counts as 1 call against the 3-call limit
        self.assertAlmostEqual(workload.rate_limiter_utilization, 100.0 / 3, places=5)
