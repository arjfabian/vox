"""Configuration loading.

Resolves runtime parameters from CLI arguments and environment variables,
merging them into a single VOXConfig instance.
"""

from vox.config.from_cli import load_cli_args
from vox.config.from_env import load_env_config
from vox.config.models import VOXConfig
from vox.config.resolver import resolve_config


def load_config() -> VOXConfig:
    """Resolve configuration from all available sources.

    Precedence:
        CLI args > env vars > defaults

    Returns:
        VOXConfig: fully resolved runtime configuration.
    """
    cli_args = load_cli_args()
    env_config = load_env_config()

    return resolve_config(
        cli_args=cli_args,
        env_config=env_config,
    )
