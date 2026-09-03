from dataclasses import dataclass

from vox.api_server import VOXAPIServer
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration import VOXOrchestrator


@dataclass
class VOXRuntime:
    """Composition root of the VOX process.

    ``VOXRuntime`` is the wiring container produced by :func:`build_vox` and
    driven by :func:`run_vox`. Its single responsibility is to hold the fully
    assembled top-level components (``config``, ``logger``, ``orchestrator``,
    ``api_server``) so the daemon can boot and tear them down. It carries no
    orchestration, capability, or configuration logic of its own.
    """

    config: VOXConfig
    logger: VOXForensicLogger
    orchestrator: VOXOrchestrator
    api_server: VOXAPIServer
