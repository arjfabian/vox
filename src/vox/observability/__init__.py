"""
VOX observability subsystem.

Error handling contract — every exception handler in the codebase must
follow one of these patterns:

  1. LOG:     except Exception as e: logger.exception("context: %s", e)
  2. RERAISE: except Exception: raise
  3. EXPLAIN: except Exception: pass  # noqa: swallow -- reason documented here

Rule of thumb:
  - Transport layers (clients) may return errors as data — the caller logs.
  - All other layers must log the exception object, not just static text.
  - ``except Exception: pass`` without # noqa: swallow is a defect.
  - ``asyncio.CancelledError`` is the only exception that can be silently
    caught without logging (and only at loop boundaries).
"""

from .formatters import VOXColorFormatter, VOXPlainFormatter
from .models import VOXForensicLogger, VOXLogSource

__all__ = [
    "VOXColorFormatter",
    "VOXForensicLogger",
    "VOXLogSource",
    "VOXPlainFormatter",
]
