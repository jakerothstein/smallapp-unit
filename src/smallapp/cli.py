"""Command line entry point. Argument parsing, output formatting, exit codes only."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import COMMANDS, __version__, registry
from .apply import apply_unit, remove_unit
from .gateway import main as gateway_main
from .naming import Unit, ValidationError, validate_domain, validate_name
from .plan import build, resolve_secrets, summarise
from .registry import RegistryError
from .render import RenderedFile, render
from .system import StepError, System, preflight
from .target import Target, TargetError, detect

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smallapp",
        description="Turn one file into one hardened, private, self-hosted deploy unit.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    subs = {name: subparsers.add_parser(name, help=HELP[name]) for name in COMMANDS}

    for name in ("plan", "apply"):
        sub = subs[name]
        sub.add_argument("target", metavar="TARGET", help="a .py file or a dir with index.html")
        sub.add_argument("--name", required=True, help="unit name, [a-z0-9-], 1-32 chars")
        sub.add_argument("--domain", required=True, help="hostname to serve the unit on")
        sub.add_argument("--tls", choices=("acme", "internal"), default="acme")
    subs["plan"].add_argument("--out", metavar="DIR", help="write rendered artifacts into DIR")
    subs["apply"].add_argument("--root", default="/", help="apply into this prefix instead of /")
    subs["status"].add_argument("name", metavar="NAME", nargs="?", help="only this unit")
    subs["status"].add_argument("--json", action="store_true", help="machine-readable output")
    subs["status"].add_argument("--root", default="/", help="read a prefixed host")
    subs["rm"].add_argument("name", metavar="NAME", help="unit to remove")
    subs["rm"].add_argument("--root", default="/", help="remove from this prefix instead of /")
    subs["doctor"].add_argument("--root", default="/", help="check this prefix instead of /")
    return parser


def _unit(system: System, args: argparse.Namespace, target: Target) -> Unit:
    name = validate_name(args.name)
    port, gw_port = registry.allocate_ports(system, name)
    return Unit(
        name=name,
        domain=validate_domain(args.domain),
        kind=target.kind,
        port=port,
        gw_port=gw_port,
        tls=args.tls,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def cmd_plan(args: argparse.Namespace) -> int:
    system = System("/")
    try:
        target = detect(args.target)
        unit = _unit(system, args, target)
        secrets = resolve_secrets(system, unit)
        actions = build(system, target, unit, secrets)
    except (ValidationError, TargetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_TARGET
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    _print_header(unit)
    for action in actions:
        print(action.line())
    print()
    if args.out:
        files = render(target, unit, secrets.secret, secrets.token_hash)
        written = write_out(files, Path(args.out))
        print(f"{summarise(actions)}  (nothing applied; {written} files written to {args.out})")
    else:
        print(f"{summarise(actions)}  (nothing applied; run `smallapp apply`)")
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    system = System(args.root)
    if not system.prefixed and not system.is_root():
        print("error: apply needs root; re-run with sudo or use --root DIR", file=sys.stderr)
        return EXIT_FAIL
    try:
        target = detect(args.target)
        unit = _unit(system, args, target)
    except (ValidationError, TargetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_TARGET
    try:
        secrets = resolve_secrets(system, unit)
        actions, token = apply_unit(system, target, unit, secrets)
    except (StepError, RegistryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    _print_header(unit)
    for action in actions:
        print(action.line())
    print()
    print(summarise(actions))
    if token is not None:
        print()
        print(f"login token (shown once): {token}")
        print(f"  open https://{unit.domain} and paste it to sign in")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    system = System(args.root)
    try:
        units = registry.load(system)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.name is not None:
        unit = units.get(args.name)
        if unit is None:
            print(f"error: no unit named {args.name}", file=sys.stderr)
            return EXIT_FAIL
        units = {args.name: unit}
    records = {
        name: {
            "kind": unit.kind,
            "port": unit.port,
            "gw_port": unit.gw_port,
            "domain": unit.domain,
            "tls": unit.tls,
            "created_at": unit.created_at,
            "app": system.systemctl_output("is-active", unit.service_path.name),
            "gateway": system.systemctl_output("is-active", unit.gw_service_path.name),
        }
        for name, unit in sorted(units.items())
    }
    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
        return EXIT_OK
    if not records:
        print("no units")
        return EXIT_OK
    for name, record in records.items():
        print(
            f"{name:<20} {record['kind']:<7} 127.0.0.1:{record['port']} "
            f"{record['domain']}  app={record['app']} gw={record['gateway']}"
        )
    return EXIT_OK


def cmd_rm(args: argparse.Namespace) -> int:
    system = System(args.root)
    if not system.prefixed and not system.is_root():
        print("error: rm needs root; re-run with sudo or use --root DIR", file=sys.stderr)
        return EXIT_FAIL
    try:
        actions = remove_unit(system, args.name)
    except (StepError, RegistryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if actions is None:
        print(f"{args.name}: not found, nothing to do")
        return EXIT_OK
    for action in actions:
        print(f"  - {action.verb:<12} {action.target}")
    print()
    print(f"removed {args.name}.")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = preflight(System(args.root))
    failed = [check for check in checks if not check.ok]
    for check in checks:
        print(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}")
    if not failed:
        print("\nthis host can host smallapp units.")
        return EXIT_OK
    print(file=sys.stderr)
    for check in failed:
        print(f"error: {check.name}\n       fix: {check.fix}", file=sys.stderr)
    return EXIT_FAIL


def _print_header(unit: Unit) -> None:
    print(f"plan: {unit.name} ({unit.kind}, 127.0.0.1:{unit.port}) -> https://{unit.domain}")
    print(f"      gateway 127.0.0.1:{unit.gw_port}, tls {unit.tls}")
    print()


def write_out(files: dict[str, RenderedFile], out: Path) -> int:
    """Mirror every rendered file under `out`, keeping its absolute path shape."""
    for path, rendered in files.items():
        destination = out / path.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(rendered.content)
        destination.chmod(rendered.mode)
    return len(files)


COMMAND_FUNCTIONS = {
    "plan": cmd_plan,
    "apply": cmd_apply,
    "status": cmd_status,
    "rm": cmd_rm,
    "doctor": cmd_doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command: str = args.command
    if command == "gateway":
        return gateway_main()
    return COMMAND_FUNCTIONS[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
