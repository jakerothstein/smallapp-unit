"""`/var/lib/smallapp/registry.json`: which units exist, and on which ports."""

from __future__ import annotations

import json
from dataclasses import asdict

from .naming import APP_PORT_BASE, REGISTRY_PATH, Unit, ValidationError, gw_port_for, port_for
from .system import System

VERSION = 1
REGISTRY = str(REGISTRY_PATH)


class RegistryError(RuntimeError):
    """The registry file exists but cannot be understood."""


def load(system: System) -> dict[str, Unit]:
    """Every recorded unit. An absent registry is an empty one; a corrupt one is fatal."""
    raw = system.read(REGISTRY)
    if raw is None:
        return {}
    try:
        document = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{REGISTRY} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise RegistryError(f"{REGISTRY} is not a version {VERSION} smallapp registry")
    units = document.get("units")
    if not isinstance(units, dict):
        raise RegistryError(f"{REGISTRY} has no units object")
    out: dict[str, Unit] = {}
    for name, record in units.items():
        if not isinstance(record, dict):
            raise RegistryError(f"{REGISTRY}: entry {name!r} is not an object")
        try:
            out[name] = Unit(**record)
        except (TypeError, ValidationError) as exc:
            raise RegistryError(f"{REGISTRY}: entry {name!r} is invalid: {exc}") from exc
    return out


def save(system: System, units: dict[str, Unit]) -> None:
    """Write the registry, or remove it entirely when the last unit goes away."""
    if not units:
        system.remove(REGISTRY)
        return
    document = {
        "version": VERSION,
        "units": {name: asdict(unit) for name, unit in sorted(units.items())},
    }
    system.write(REGISTRY, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(), 0o644)


def put(system: System, unit: Unit) -> None:
    units = load(system)
    units[unit.name] = unit
    save(system, units)


def drop(system: System, name: str) -> bool:
    units = load(system)
    if name not in units:
        return False
    del units[name]
    save(system, units)
    return True


def allocate_ports(system: System, name: str) -> tuple[int, int]:
    """Ports for `name`: its own if already registered, else the first free deterministic slot."""
    units = load(system)
    existing = units.get(name)
    if existing is not None:
        return existing.port, existing.gw_port
    taken = {unit.port for unit in units.values()}
    taken |= {
        unit.gw_port - 1000 for unit in units.values() if unit.gw_port - 1000 >= APP_PORT_BASE
    }
    port = port_for(name, taken)
    return port, gw_port_for(port)
