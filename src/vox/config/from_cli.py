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
    workload_name: str | None
    verbose: bool | None = None


def load_cli_args() -> VOXCliArgs:
    """
    Parses and returns CLI arguments.

    Supported commands:
        vox start <workload>
        vox stop <workload>
        vox restart <workload>
    """

    parser = argparse.ArgumentParser(description="VOX Workload Orchestrator")

    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "restart", "pause", "resume"],
        help="Workload lifecycle command.",
    )

    parser.add_argument(
        "workload_name",
        nargs="?",
        help="Target workload name.",
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
        workload_name=args.workload_name,
        verbose=args.verbose,
    )
