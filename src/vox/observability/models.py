import logging
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
