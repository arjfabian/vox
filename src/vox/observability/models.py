import logging
import sys
from dataclasses import dataclass
from typing import Any

from .constants import LOG_LEVEL_OK


@dataclass(slots=True)
class VOXLogSource:
    source_type: str
    source_name: str = ""
    source_uuid: str = ""

    @property
    def short_uuid(self) -> str:
        if not self.source_uuid:
            return ""
        return f"{self.source_uuid[:4]}...{self.source_uuid[-4:]}"

    @property
    def display_name(self) -> str:
        if self.source_name and self.source_uuid:
            return f"{self.source_type}.{self.source_name}:{self.short_uuid}"

        if self.source_name:
            return f"{self.source_type}.{self.source_name}"

        return self.source_type


class VOXForensicLogger:
    def __init__(self, logger: logging.Logger, verbose: bool = False) -> None:
        self._logger = logger
        self.verbose = verbose

    def _extra(self, source: VOXLogSource | None) -> dict:
        return {"vox_source": source}

    def ok(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("extra", None)
        self._logger.log(
            LOG_LEVEL_OK,
            message,
            *args,
            extra=self._extra(source),
            **kwargs,
        )

    def info(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("extra", None)
        self._logger.info(message, *args, extra=self._extra(source), **kwargs)

    def warning(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("extra", None)
        self._logger.warning(message, *args, extra=self._extra(source), **kwargs)

    def error(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("extra", None)
        self._logger.error(message, *args, extra=self._extra(source), **kwargs)

    def exception(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        """Log an ERROR record carrying the current exception traceback.

        Mirrors ``logging.Logger.exception`` (defaults to ``exc_info=True``).
        Call it from inside an ``except`` block: the in-flight exception is
        captured up front with its full traceback.

        Never raises — a logging failure must not mask the operational
        exception being reported. If the primary emission fails, the record is
        handed to logging's last-resort handler (stderr); if even that fails,
        the failure is suppressed so the original exception continues to
        propagate through the caller's boundary.
        """
        kwargs.pop("extra", None)
        kwargs.setdefault("exc_info", True)
        exc_info = kwargs.pop("exc_info")
        if exc_info is True:
            exc_info = sys.exc_info()
        if exc_info is False:
            exc_info = None
        if isinstance(exc_info, tuple) and exc_info[1] is None:
            exc_info = None
        try:
            self._logger.error(
                message,
                *args,
                extra=self._extra(source),
                exc_info=exc_info,
                **kwargs,
            )
        except Exception:  # noqa: BLE001 - never mask the reported exception
            try:
                record = self._logger.makeRecord(
                    self._logger.name,
                    logging.ERROR,
                    __file__,
                    0,
                    message,
                    args,
                    exc_info,
                    None,
                    None,
                )
                last_resort = getattr(logging, "lastResort", None)
                if last_resort is not None:
                    last_resort.handle(record)
            except Exception:  # noqa: BLE001, S110 - best effort, never raise
                pass

    def debug(
        self,
        message: str,
        *args: Any,
        source: VOXLogSource | None = None,
        **kwargs: Any,
    ) -> None:
        if not self.verbose:
            return
        kwargs.pop("extra", None)
        self._logger.debug(message, *args, extra=self._extra(source), **kwargs)

    def get_child(self, name: str) -> "VOXForensicLogger":
        """Returns a child logger carrying ``name`` as a dotted suffix (e.g.
        ``vox.workload1``).

        Formatters split on ``.`` and render only the last segment so that
        workload-scoped messages show ``[workload1]`` instead of ``[vox]``.
        """
        return VOXForensicLogger(
            self._logger.getChild(name),
            verbose=self.verbose,
        )
