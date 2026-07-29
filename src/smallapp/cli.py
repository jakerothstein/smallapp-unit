"""Command line entry point. Argument parsing and exit codes only; no logic here."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import COMMANDS, __version__
from .gateway import main as gateway_main
from .naming import (
    Unit,
    ValidationError,
    gw_port_for,
    port_for,
    validate_domain,
    validate_name,
)
from .render import RenderedFile, render
from .target import TargetError, detect
from .tokens import generate_secret, generate_token, hash_token

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
    unit = Unit(
        name=name,
        domain=domain,
        kind=target.kind,
        port=port,
        gw_port=gw_port_for(port),
        tls=args.tls,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    files = render(target, unit, generate_secret(), hash_token(generate_token()))
    print(f"plan: {name} ({target.kind}, 127.0.0.1:{port}) -> https://{domain}")
    print(f"      gateway 127.0.0.1:{unit.gw_port}, tls {args.tls}")
    print()
    for path in sorted(files):
        rendered = files[path]
        mode = "  (0600)" if rendered.mode == 0o600 else ""
        print(f"  + file      {path}{mode}")
    print(f"  + user      {unit.user}")
    print("  + reload    caddy")
    if args.out:
        written = write_out(files, Path(args.out))
        print(f"\nwrote {written} files under {args.out}")
    else:
        print("\nnothing applied; run `smallapp apply`")
    return EXIT_OK


def write_out(files: dict[str, RenderedFile], out: Path) -> int:
    """Mirror every rendered file under `out`, keeping its absolute path shape."""
    for path, rendered in files.items():
        destination = out / path.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(rendered.content)
        destination.chmod(rendered.mode)
    return len(files)


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
