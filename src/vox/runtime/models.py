from dataclasses import dataclass
from pathlib import Path
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration import VOXOrchestrator
from vox.api_server import VOXAPIServer


@dataclass(frozen=True)
class VOXRuntimeConfig:
    uds_path: Path = Path("/tmp/vox.sock")
    keepalive_interval: int = 3600

@dataclass
class VOXRuntime:
    config: VOXConfig
    logger: VOXForensicLogger
    orchestrator: VOXOrchestrator
    api_server: VOXAPIServer