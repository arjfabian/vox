"""VOX daemon runtime bootstrap."""

import asyncio

from vox.observability import VOXForensicLogger
from vox.runtime.control_plane import start_control_plane
from vox.runtime.models import VOXRuntime


def _silence_playwright_futures(loop, context):
    """Suppress 'Future exception was never retrieved' for Playwright's TargetClosedError."""
    msg = context.get("message", "")
    exc = context.get("exception", None)
    if "TargetClosedError" in msg or (
        exc and "TargetClosedError" in type(exc).__name__
    ):
        return
    loop.default_exception_handler(context)


async def _keepalive(interval_seconds: int, logger: VOXForensicLogger) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.warning("Main loop cancelled.")


async def run_vox(runtime: VOXRuntime, logger: VOXForensicLogger) -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_silence_playwright_futures)

    logger.info("Welcome to VOX - Virtual Orchestrator eXperience")

    try:
        operative = await runtime.orchestrator.boot()
    except Exception:  # noqa: BLE001 — boot failure triggers shutdown
        logger.error("VOX boot failed.")
        await runtime.orchestrator.shutdown()
        return

    if not operative:
        logger.error("No operative workloads. VOX cannot start.")
        await runtime.orchestrator.shutdown()
        return

    logger.info("Starting API server")
    asyncio.create_task(runtime.api_server.start())

    logger.info("Starting control plane")
    logger.ok("System ready. VOX is monitoring in the background.")

    try:
        await asyncio.gather(
            _keepalive(interval_seconds=3600, logger=logger),
            start_control_plane(runtime=runtime, logger=logger),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("VOX runtime cancelled.")
    finally:
        await runtime.api_server.shutdown()
        await runtime.orchestrator.shutdown()
