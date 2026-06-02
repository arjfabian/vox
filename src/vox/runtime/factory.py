"""
VOX runtime factory.

Builds the fully-wired VOX runtime container.
"""

from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration import VOXOrchestrator
from vox.services.api_server import VOXAPIServer

from .models import VOXRuntime


async def build_vox(
    config: VOXConfig,
    logger: VOXForensicLogger,
) -> VOXRuntime:
    """
    Builds and returns the VOX runtime container.
    """

    orchestrator = VOXOrchestrator(
        config=config,
        logger=logger,
    )
    logger.ok("Orchestrator initialized")

    runtime = VOXRuntime(
        config=config,
        logger=logger,
        orchestrator=orchestrator,
    )
    logger.info("Runtime container built")

    runtime.api_server = VOXAPIServer(runtime.orchestrator)
    logger.info("API server wired")

    return runtime