"""Configuration models and defaults."""

from dataclasses import dataclass

DEFAULT_VERBOSE_LOGGING = False
DEFAULT_LOG_PATH = "logs/vox.log"
DEFAULT_UDS_PATH = "/tmp/vox.sock"


@dataclass
class VOXConfig:
    verbose_logging: bool
    log_path: str
    uds_path: str
    war_room_id: str
