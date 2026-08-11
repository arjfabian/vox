"""
Environment variable parsing.

Reads config from .env and process environment, exposing them as a
typed dataclass for downstream runtime resolution.
"""

import os
from dataclasses import dataclass

from dotenv import dotenv_values


@dataclass
class VOXEnvConfig:
    """
    Raw environment config before runtime resolution.
    """

    war_room_id: str
    verbose_logging: bool = False


def load_env_config() -> VOXEnvConfig:
    """
    Loads and returns environment configuration.

    Reads .env first, then os.environ
    (environment variables override .env).
    """

    env = {
        **dotenv_values(".env"),
        **os.environ,
    }

    return VOXEnvConfig(
        war_room_id=env.get("VOX_WAR_ROOM_ID"),
        verbose_logging=_parse_bool(env.get("VOX_VERBOSE_LOGGING", "false")),
    )


def _parse_bool(val: str) -> bool:
    return val.lower() in ("1", "true", "yes", "on")
