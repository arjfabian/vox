"""
VOX runtime factory.

Builds the fully-wired VOX runtime container.
"""

from vox.api_server import VOXAPIServer
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration import VOXOrchestrator

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

    api_server = VOXAPIServer(orchestrator)
    logger.info("API server wired")

    runtime = VOXRuntime(
        config=config,
        logger=logger,
        orchestrator=orchestrator,
        api_server=api_server,
    )
    logger.info("Runtime container built")

    return runtime
