import re
import html
from typing import Any

class SecurityError(Exception):
    pass

class InputSanitizer:
    """
    Sanitizes all inputs based on known dangerous patterns and length limits.
    """
    DANGEROUS_PATTERNS = [
        r'<script',           # Basic XSS
        r'javascript:',       # Protocol injection
        r'eval\(',            # Dynamic code execution
        r'exec\(',            # Python command execution
        r'\.\./\.\./',        # Path Traversal (LFI/RFI)
        r'SELECT.*FROM',      # SQL Injection for DB-based Roles
        r'rm\s+-rf',          # Destructive Command Injection
    ]

    def sanitize(self, data: Any) -> Any:
        """Sanitizes dictionaries, lists and strings recursively."""
        if isinstance(data, dict):
            return {k: self.sanitize(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.sanitize(item) for item in data]
        elif isinstance(data, str):
            return self._sanitize_string(data)
        return data

    def _sanitize_string(self, text: str) -> str:
        # 1. Dangerous pattern detection (Fail Fast)
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                # Instead of just clearing the action, block for security reasons
                raise SecurityError(f"Security pattern detected: {pattern}")
        
        # 2. HTML escape to prevent accidental rendering
        text = html.escape(text)
        
        # 3. Length limit (prevents memory-based DoS attacks)
        max_len = 5000 
        return text[:max_len] if len(text) > max_len else text