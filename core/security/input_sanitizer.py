"""
VOX Input Sanitizer
Internal Name: THE TRANSLATOR

Enforces data integrity and security across the agent's input pipeline.
Detects malicious patterns, prevents injection attacks, and enforces
structural constraints on incoming payloads.
"""

import re
import html
from typing import Any, Dict, List, Union

class SecurityError(Exception):
    """Raised when a payload violates the security policy or contains malicious patterns."""
    pass

class InputSanitizer:
    """
    High-assurance data validator.
    Implements recursive sanitization and pattern-based threat detection.
    """

    # Advanced threat patterns for Cross-Site Scripting, SQLi, and Command Injection
    DANGEROUS_PATTERNS = [
        r'<script',               # Script tag injection
        r'javascript:',           # Protocol-based URI injection
        r'eval\(',                # Dynamic execution sinks
        r'exec\(',                # System-level execution sinks
        r'\.\./\.\./',            # Path traversal / Directory climbing
        r'SELECT.*FROM',          # SQL injection patterns
        r'DROP\s+TABLE',          # SQL destructive patterns
        r'rm\s+-rf',              # POSIX destructive commands
        r'base64\s+--decode',     # Obfuscated payload execution
    ]

    def sanitize(self, data: Any) -> Any:
        """
        Recursively sanitizes incoming data structures.
        Supports nested Dictionaries, Lists, and Strings.
        """
        if isinstance(data, dict):
            return {k: self.sanitize(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.sanitize(item) for item in data]
        elif isinstance(data, str):
            return self._sanitize_string(data)
        return data

    def _sanitize_string(self, text: str) -> str:
        """
        String-level security enforcement protocol.
        
        1. Threat Detection: Scans for known malicious signatures.
        2. HTML Normalization: Escapes entities to prevent injection.
        3. Size Constraints: Enforces limits to mitigate Buffer Overflow/DoS.
        """
        
        # Phase 1: Fail-Fast Pattern Analysis
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                # Security policy: Any malicious signature results in an immediate block
                raise SecurityError(f"Security Policy Violation: Malicious pattern '{pattern}' detected.")
        
        # Phase 2: HTML Encoding (Neutralization)
        # Prevents rendering of accidental or intentional HTML in communication links
        text = html.escape(text)
        
        # Phase 3: Volumetric Control
        # Prevents memory exhaustion attacks (DoS) via oversized payloads
        MAX_STR_LENGTH = 8192  # Optimized for LLM contexts (approx 2k tokens)
        
        if len(text) > MAX_STR_LENGTH:
            return text[:MAX_STR_LENGTH]
            
        return text