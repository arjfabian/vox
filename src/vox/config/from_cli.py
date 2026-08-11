"""
CLI argument parsing.

Exposes parsed command-line arguments as a typed dataclass for downstream
runtime resolution.
"""

import argparse
from dataclasses import dataclass


@dataclass
class VOXCliArgs:
    """
    Raw CLI arguments before runtime resolution.

    Fields are None when not provided on the command line.
    """

    command: str | None
    agent_name: str | None
    verbose: bool | None = None


def load_cli_args() -> VOXCliArgs:
    """
    Parses and returns CLI arguments.

    Supported commands:
        vox start <agent>
        vox stop <agent>
        vox restart <agent>
    """

    parser = argparse.ArgumentParser(description="VOX Agent Orchestrator")

    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "restart", "pause", "resume"],
        help="Agent lifecycle command.",
    )

    parser.add_argument(
        "agent_name",
        nargs="?",
        help="Target agent name.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args = parser.parse_args()

    return VOXCliArgs(
        command=args.command,
        agent_name=args.agent_name,
        verbose=args.verbose,
    )
