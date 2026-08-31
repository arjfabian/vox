"""
Configuration resolution.

Merges CLI args, environment variables, and defaults into a final VOXConfig.
"""

from .from_cli import VOXCliArgs
from .from_env import VOXEnvConfig
from .models import (
    DEFAULT_LOG_PATH,
    DEFAULT_UDS_PATH,
    DEFAULT_VERBOSE_LOGGING,
    VOXConfig,
)


def resolve_config(
    cli_args: VOXCliArgs,
    env_config: VOXEnvConfig,
) -> VOXConfig:
    """
    Resolves and returns the final configuration.

    Precedence:
        CLI args > env vars > defaults
    """

    war_room_id = env_config.war_room_id

    if not war_room_id:
        raise ValueError("Missing required environment variable: VOX_WAR_ROOM_ID")

    if cli_args.verbose is not None:
        verbose_logging = cli_args.verbose
    elif env_config.verbose_logging:
        verbose_logging = True
    else:
        verbose_logging = DEFAULT_VERBOSE_LOGGING

    return VOXConfig(
        verbose_logging=verbose_logging,
        log_path=DEFAULT_LOG_PATH,
        uds_path=DEFAULT_UDS_PATH,
        war_room_id=war_room_id,
    )
