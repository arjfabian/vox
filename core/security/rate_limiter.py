import time
from collections import deque

class RateLimitError(Exception):
    pass

class RateLimiter:
    """
    Implements a rate limiter with a sliding window.
    """
    def __init__(self, max_calls: int = 100, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window = window_seconds
        self.calls = deque()

    def allow(self) -> bool:
        """
        Verifies whether the current call is within the allowed limit.
        """
        now = time.time()

        # Eliminate calls outside the time window
        while self.calls and self.calls[0] < now - self.window:
            self.calls.popleft()

        # If maximum value is exceeded, block call
        if len(self.calls) >= self.max_calls:
            return False
            
        # Registers the current call
        self.calls.append(now)
        return True