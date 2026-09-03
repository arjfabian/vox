"""VOX CLI entry point.

Parses config, bootstraps observability, then builds and runs the VOX runtime.
Invoked by the ``vox`` console-script (``main()``) or
``python -m vox`` (via :mod:`vox.__main__`).
"""

import asyncio
import json
import logging
import os
import signal
import sys

from vox.config import load_config
from vox.config.from_cli import load_cli_args
from vox.config.models import DEFAULT_UDS_PATH
from vox.observability import (
    VOXColorFormatter,
    VOXForensicLogger,
    VOXPlainFormatter,
)
from vox.runtime import build_vox, run_vox


def main() -> None:
    """Synchronous entry point for the ``vox`` CLI binary."""
    asyncio.run(_main())


async def _send_uds_command(cmd: str, args: list[str]) -> dict:
    uds_path = os.environ.get("VOX_UDS_PATH", DEFAULT_UDS_PATH)
    reader, writer = await asyncio.open_unix_connection(uds_path)
    try:
        payload = json.dumps({"cmd": cmd, "args": args}) + "\n"
        writer.write(payload.encode())
        await writer.drain()
        response = await reader.readline()
        return json.loads(response.decode().strip())
    finally:
        writer.close()
        await writer.wait_closed()


def _cancel_child_tasks() -> None:
    """Cancel all tasks except the current one (signal-handler helper)."""
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current:
            task.cancel()


async def _main() -> None:
    """Bootstrap config, logging and runtime, then start VOX."""

    cli_args = load_cli_args()
    if cli_args.command:
        try:
            resp = await _send_uds_command(
                cli_args.command,
                ([cli_args.workload_name] if cli_args.workload_name else []),
            )
            if resp.get("ok"):
                print(resp["data"])
            else:
                print(f"Error: {resp.get('error', resp)}", file=sys.stderr)
                sys.exit(1)
        except FileNotFoundError:
            print(
                "Error: VOX control plane socket not found. Is VOX running?",
                file=sys.stderr,
            )
            sys.exit(1)
        except ConnectionRefusedError:
            print(
                "Error: Connection refused. Is VOX running?",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    # Logger isn't ready yet — use print for early diagnostics.
    print("Loading config")
    config = load_config()

    print(f"Initializing logger at {config.log_path}")

    # Root logger for the "vox" hierarchy — all child loggers inherit.
    base_logger = logging.getLogger("vox")
    base_logger.setLevel(logging.DEBUG)

    # Human-friendly color output to stdout.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(VOXColorFormatter())

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
