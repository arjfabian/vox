"""VOX CLI entry point.

Parses config, bootstraps observability, then builds and runs the VOX
runtime.  Invoked by the ``vox`` console-script (``main()``) or
``python -m vox`` (via :mod:`vox.__main__`).
"""
import asyncio
import logging
import signal
import sys

from vox.config import load_config
from vox.observability import (
    VOXColorFormatter,
    VOXForensicLogger,
    VOXPlainFormatter,
)
from vox.runtime import build_vox, run_vox


def main() -> None:
    """Synchronous entry point for the ``vox`` CLI binary."""
    asyncio.run(_main())


def _cancel_child_tasks() -> None:
    """Cancel all tasks except the current one (signal-handler helper)."""
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current:
            task.cancel()


async def _main() -> None:
    """Bootstrap config, logging and runtime, then start VOX."""

    # Logger isn't ready yet — use print for early diagnostics.
    print("Loading config")
    config = load_config()

    print(f"Initializing logger at {config.log_path}")

    # Root logger for the "vox" hierarchy — all child loggers inherit.
    base_logger = logging.getLogger("vox")
    base_logger.setLevel(logging.DEBUG)

    # Human-friendly color output to stdout.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        VOXColorFormatter()
    )

    # Machine-friendly plain-text file for forensic analysis.
    file_handler = logging.FileHandler(
        config.log_path,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        VOXPlainFormatter(),
    )

    base_logger.addHandler(console_handler)
    base_logger.addHandler(file_handler)

    # Thin wrapper that adds VOXLogSource metadata to every record.
    logger = VOXForensicLogger(base_logger)

    logger.info("Logger initialized")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _cancel_child_tasks)

    logger.info("Building runtime")
    runtime = await build_vox(
        config=config,
        logger=logger,
    )

    logger.info("Starting VOX runtime")
    await run_vox(
        runtime=runtime,
        logger=logger,
    )