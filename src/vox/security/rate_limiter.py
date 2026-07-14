"""Sliding-window rate limiter.

Prevents event bursts by capping the number of emissions per agent
within a rolling time window. Uses ``collections.deque`` for O(1)
timestamp purge on each check.
"""

import time
from collections import deque


class RateLimitError(Exception):
    """Raised when an agent exceeds its allowed event frequency."""
    pass


class RateLimiter:
    """High-performance sliding-window rate limiter.

    Tracks event timestamps in a deque; on each check, expired entries
    are purged from the left (oldest) side. If the window is full the
    caller is denied.
    """

    def __init__(self, max_calls: int = 30, window_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque[float] = deque()

    def check_limit(self) -> None:
        """Active enforcement — raises on breach (circuit-breaker style)."""
        if not self.allow():
            raise RateLimitError(
                f"Rate limit breached: {self.max_calls} calls per {self.window}s window."
            )

    def allow(self) -> bool:
        """Passive check — purges expired timestamps, then registers + returns bool."""
        now = time.time()
        cutoff = now - self.window

        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

        if len(self._calls) >= self.max_calls:
            return False

        self._calls.append(now)
        return True

    def get_utilization(self) -> float:
        """Current window utilization as a percentage (0‑100)."""
        if self.max_calls == 0:
            return 0.0
        return (len(self._calls) / self.max_calls) * 100
