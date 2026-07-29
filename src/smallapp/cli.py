"""Command line entry point. Argument parsing and exit codes only; no logic here."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import COMMANDS, __version__
from .gateway import main as gateway_main
from .naming import ValidationError, gw_port_for, port_for, validate_domain, validate_name
from .target import TargetError, detect

HELP = {
    "plan": "render a deploy unit and show what would change (writes nothing)",
    "apply": "create or update a deploy unit on this host",
    "status": "show deployed units and whether their services are running",
    "rm": "remove a deploy unit and every artifact it created",
    "gateway": "run the signed-cookie auth gateway (started by the generated unit)",
    "doctor": "check this host can host units, and say how to fix it if not",
}

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD_TARGET = 2
EXIT_UNBUILT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smallapp",
        description="Turn one file into one hardened, private, self-hosted deploy unit.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    subs = {name: subparsers.add_parser(name, help=HELP[name]) for name in COMMANDS}

    plan = subs["plan"]
    plan.add_argument("target", metavar="TARGET", help="a .py file or a directory with index.html")
    plan.add_argument("--name", required=True, help="unit name, [a-z0-9-], 1-32 chars")
    plan.add_argument("--domain", required=True, help="hostname to serve the unit on")
    plan.add_argument("--tls", choices=("acme", "internal"), default="acme")
    plan.add_argument("--out", metavar="DIR", help="write rendered artifacts into DIR")
    return parser


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        name = validate_name(args.name)
        domain = validate_domain(args.domain)
        target = detect(args.target)
    except (ValidationError, TargetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_TARGET
    port = port_for(name)
    print(f"plan: {name} ({target.kind}, 127.0.0.1:{port}) -> https://{domain}")
    print(f"      gateway 127.0.0.1:{gw_port_for(port)}, tls {args.tls}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command: str = args.command
    if command == "plan":
        return cmd_plan(args)
    if command == "gateway":
        return gateway_main()
    print(f"smallapp {command}: not built yet, see IMPLEMENTATION_PLAN.md", file=sys.stderr)
    return EXIT_UNBUILT


if __name__ == "__main__":
    raise SystemExit(main())
