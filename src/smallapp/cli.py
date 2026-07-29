"""Command line entry point. Argument parsing and exit codes only; no logic here."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import COMMANDS, __version__

HELP = {
    "plan": "render a deploy unit and show what would change (writes nothing)",
    "apply": "create or update a deploy unit on this host",
    "status": "show deployed units and whether their services are running",
    "rm": "remove a deploy unit and every artifact it created",
    "gateway": "run the signed-cookie auth gateway (started by the generated unit)",
    "doctor": "check this host can host units, and say how to fix it if not",
}

EXIT_UNBUILT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smallapp",
        description="Turn one file into one hardened, private, self-hosted deploy unit.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in COMMANDS:
        subparsers.add_parser(name, help=HELP[name])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command: str = args.command
    print(f"smallapp {command}: not built yet, see IMPLEMENTATION_PLAN.md", file=sys.stderr)
    return EXIT_UNBUILT


if __name__ == "__main__":
    raise SystemExit(main())
