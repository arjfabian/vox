"""
VOX observability subsystem.

Error handling contract — every exception handler in the codebase must
follow one of these patterns:

  1. LOG:     except Exception:
              logger.exception("context was active")
  2. RERAISE: except Exception: raise
  3. EXPLAIN: except Exception: pass  # noqa: swallow -- reason documented here

Rule of thumb:
  - ``logger.exception`` renders the full traceback of the in-flight
    exception; keep the *message* for static context and do not re-embed the
    exception text in it (the traceback already carries it).
  - Transport layers (clients) may return errors as data — the caller logs.
  - All other layers must log the exception (its full traceback), never a
    bare ``str(exc)`` one-liner or static text alone.
  - ``except Exception: pass`` without # noqa: swallow is a defect.
  - ``asyncio.CancelledError`` is the only exception that can be silently
    caught without logging (and only at loop boundaries).
"""

from .constants import LOG_LEVEL_OK
from .formatters import VOXColorFormatter, VOXPlainFormatter
from .models import VOXForensicLogger, VOXLogSource

__all__ = [
    "LOG_LEVEL_OK",
    "VOXColorFormatter",
    "VOXForensicLogger",
    "VOXLogSource",
    "VOXPlainFormatter",
]
