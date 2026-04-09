"""
VOX Rate Limiter
Internal Name: THE SENTINEL

Provides a sliding-window flow control mechanism to prevent Event Storms
and ensure the Agent remains within operational boundaries.
"""

import time
from collections import deque
from typing import Optional

class RateLimitError(Exception):
    """Raised when an Agent exceeds its allowed event frequency."""
    pass

class RateLimiter:
    """
    Implements a High-Performance Sliding Window Rate Limiter.
    Tracks event frequency to protect external APIs and local compute resources.
    """

    def __init__(self, max_calls: int = 100, window_seconds: int = 60):
        """
        Initializes the monitor window.
        :param max_calls: Maximum number of events allowed per window.
        :param window_seconds: Duration of the sliding window in seconds.
        """
        self.max_calls = max_calls
        self.window = window_seconds
        self.calls = deque()

    def check_limit(self):
        """
        Active Enforcement: Verifies compliance and raises an exception on breach.
        Used by the Agent's 'emit' pipeline as a circuit breaker.
        
        :raises RateLimitError: If the execution frequency is violated.
        """
        if not self.allow():
            raise RateLimitError(
                f"Rate limit breached: {self.max_calls} calls per {self.window}s window."
            )

    def allow(self) -> bool:
        """
        Passive Verification: Checks if a new event can be processed.
        Maintains the internal call history by purging expired timestamps.
        
        :return: True if the event is within limits, False otherwise.
        """
        now = time.time()

        # Phase 1: Cleanup (Purge timestamps outside the current sliding window)
        # Using a deque allows O(1) complexity for removals from the left.
        while self.calls and self.calls[0] < now - self.window:
            self.calls.popleft()

        # Phase 2: Boundary Check
        if len(self.calls) >= self.max_calls:
            return False
            
        # Phase 3: Registration
        self.calls.append(now)
        return True

    def get_utilization(self) -> float:
        """
        Returns the current window utilization as a percentage.
        Useful for health monitoring and 'War Room' telemetry.
        """
        if self.max_calls == 0: return 0.0
        return (len(self.calls) / self.max_calls) * 100