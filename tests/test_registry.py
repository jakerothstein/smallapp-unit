"""Registry read/write, corruption handling, and port collision probing."""

from __future__ import annotations

from pathlib import Path

import pytest

from smallapp import registry
from smallapp.naming import Unit, port_for
from smallapp.registry import REGISTRY, RegistryError
from smallapp.system import System


def unit(name: str, port: int) -> Unit:
    return Unit(
        name=name,
        domain=f"{name}.example.com",
        kind="python",
        port=port,
        gw_port=port + 1000,
        tls="acme",
        created_at="2024-01-01T00:00:00+00:00",
    )


@pytest.fixture
def system(tmp_path: Path) -> System:
    return System(tmp_path)


def test_absent_registry_is_empty(system: System) -> None:
    assert registry.load(system) == {}


def test_put_then_load_round_trips(system: System) -> None:
    registry.put(system, unit("expenses", 18412))
    loaded = registry.load(system)
    assert loaded["expenses"].domain == "expenses.example.com"
    assert loaded["expenses"].gw_port == 19412


def test_drop_removes_the_entry_and_the_file_when_last(system: System) -> None:
    registry.put(system, unit("expenses", 18412))
    registry.put(system, unit("notes", 18500))
    assert registry.drop(system, "expenses") is True
    assert set(registry.load(system)) == {"notes"}
    assert registry.drop(system, "notes") is True
    assert system.read(REGISTRY) is None
    assert registry.drop(system, "gone") is False


def test_corrupt_registry_is_loud(system: System) -> None:
    system.write(REGISTRY, b"{not json", 0o644)
    with pytest.raises(RegistryError, match="not valid JSON"):
        registry.load(system)
    system.write(REGISTRY, b'{"version": 99, "units": {}}', 0o644)
    with pytest.raises(RegistryError, match="version 1"):
        registry.load(system)
    system.write(REGISTRY, b'{"version": 1}', 0o644)
    with pytest.raises(RegistryError, match="units"):
        registry.load(system)
    system.write(REGISTRY, b'{"version": 1, "units": {"a": {"name": "a"}}}', 0o644)
    with pytest.raises(RegistryError, match="invalid"):
        registry.load(system)


def test_allocate_keeps_a_units_port_forever(system: System) -> None:
    first = registry.allocate_ports(system, "expenses")
    registry.put(system, unit("expenses", first[0]))
    assert registry.allocate_ports(system, "expenses") == first


def test_allocate_probes_past_a_seeded_collision(system: System) -> None:
    natural = port_for("expenses")
    registry.put(system, unit("squatter", natural))
    port, gw_port = registry.allocate_ports(system, "expenses")
    assert port == natural + 1
    assert gw_port == natural + 1001


def test_registry_never_stores_a_secret(system: System) -> None:
    registry.put(system, unit("expenses", 18412))
    raw = system.read(REGISTRY)
    assert raw is not None
    assert b"SECRET" not in raw
    assert b"scrypt" not in raw
