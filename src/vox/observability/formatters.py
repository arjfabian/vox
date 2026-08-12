"""Console log formatters."""

import logging
from datetime import datetime

from vox.observability.constants import LOG_LEVEL_OK


def _source_tag(record: logging.LogRecord) -> str:
    """Extract the last segment of a dotted logger name.

    ``vox.agent1`` -> ``agent1``
    ``vox``      -> ``vox``
    """
    name = record.name
    return name.split(".")[-1] if "." in name else name


class VOXColorFormatter(logging.Formatter):
    _RESET = "\x1b[0m"

    _GREY = "\x1b[90;20m"
    _WHITE = "\x1b[38;5;250m"
    _BLUE = "\x1b[34m"
    _GREEN = "\x1b[32m"
    _YELLOW = "\x1b[33m"
    _RED = "\x1b[31m"
    _BOLD_RED = "\x1b[31;1m"
    _BOLD_PINK = "\x1b[35;1m"

    # noqa: RUF012 — immutable color mapping; intentionally shared, never mutated.
    _LEVEL_COLORS = {
        logging.DEBUG: _GREY,
        logging.INFO: _WHITE,
        LOG_LEVEL_OK: _GREEN,
        logging.WARNING: _YELLOW,
        logging.ERROR: _RED,
        logging.CRITICAL: _BOLD_RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ006 — log formatting, local time intentional

        level_color = self._LEVEL_COLORS.get(
            record.levelno,
            self._WHITE,
        )

        entry_ts = f"{self._GREY}{timestamp}{self._RESET}"
        entry_level = f"{level_color}{record.levelname:>7}{self._RESET}"
        src_color = self._BOLD_PINK if "." in record.name else self._BLUE
        entry_src = f"{src_color}[{_source_tag(record)}]{self._RESET}"
        entry_message = f"{self._WHITE}{record.getMessage()}{self._RESET}"

        return f"{entry_ts} {entry_level} {entry_src} {entry_message}"


class VOXPlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ006 — log formatting, local time intentional

        tag = _source_tag(record)

        return f"{timestamp} [{record.levelname}] [{tag}] {record.getMessage()}"
