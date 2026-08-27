"""Inbound payload sanitization — dangerous pattern detection (WAF layer).

Runs before any payload touches workload roles or capabilities.
``InputSanitizer`` recursively walks dicts/lists and checks all string
values against known malicious patterns. On match it raises
``SecurityError`` and the caller logs a CRITICAL alert and drops the
event.
"""

import html
import re


class SecurityError(Exception):
    """Raised when a payload violates the security policy."""



class InputSanitizer:
    """Fail-fast inbound payload scanner.

    Designed as a singleton per orchestrator; one instance guards the
    entire message ingress pipeline.
    """

    DANGEROUS_PATTERNS: list[str] = [  # noqa: RUF012
        r"<script",  # Script tag injection
        r"javascript:",  # Protocol-based URI injection
        r"eval\(",  # Dynamic execution sinks
        r"exec\(",  # System-level execution sinks
        r"\.\./\.\./",  # Path traversal / directory climbing
        r"SELECT\s+.*\s+FROM",  # SQL injection
        r"DROP\s+TABLE",  # SQL destructive
        r"rm\s+-rf",  # POSIX destructive
        r"base64\s+--decode",  # Obfuscated payload execution
    ]

    MAX_STR_LENGTH: int = 8192

    def __init__(self, logger) -> None:
        self._compiled: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self.DANGEROUS_PATTERNS
        ]
        self.logger = logger

    def sanitize(self, data):
        if isinstance(data, dict):
            return {k: self.sanitize(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.sanitize(item) for item in data]
        if isinstance(data, str):
            return self._sanitize_string(data)
        return data

    def _sanitize_string(self, text: str) -> str:
        for compiled, raw in zip(self._compiled, self.DANGEROUS_PATTERNS):
            if compiled.search(text):
                self.logger.critical(
                    "GUARDRAIL BLOCKED — matched pattern %r | payload excerpt: %r",
                    raw,
                    text[:200],
                )
                raise SecurityError(
                    f"Security Policy Violation: pattern '{raw}' detected."
                )
        safe = html.escape(text)
        if len(safe) > self.MAX_STR_LENGTH:
            safe = safe[: self.MAX_STR_LENGTH]
        return safe
