"""Name/domain validation and deterministic port allocation."""

from __future__ import annotations

import subprocess
import sys

import pytest

from smallapp.naming import (
    APP_PORT_BASE,
    ValidationError,
    gw_port_for,
    port_for,
    validate_domain,
    validate_name,
)


@pytest.mark.parametrize("name", ["a", "my-app", "app2", "x" * 26])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "-lead", "trail-", "Upper", "has_underscore", "x" * 27, "x" * 33]
)
def test_invalid_names_name_the_offender(name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_name(name)
    assert name in str(exc.value) or name == ""


@pytest.mark.parametrize("domain", ["a.example.com", "expenses.example.com", "x-y.co"])
def test_valid_domains(domain: str) -> None:
    assert validate_domain(domain) == domain


@pytest.mark.parametrize(
    "domain",
    ["", "localhost", "bad_underscore.com", "-lead.com", "trail-.com", "a..com", "up.EXAMPLE.com"],
)
def test_invalid_domains(domain: str) -> None:
    with pytest.raises(ValidationError):
        validate_domain(domain)


@pytest.mark.parametrize("hostile", ["a\nb", "a b", "a%b", "a}b", "a;b", "a$b"])
def test_hostile_input_is_refused_not_escaped(hostile: str) -> None:
    with pytest.raises(ValidationError):
        validate_name(hostile)
    with pytest.raises(ValidationError):
        validate_domain(f"{hostile}.example.com")


def test_port_is_in_range_and_deterministic_across_processes() -> None:
    port = port_for("expenses")
    assert APP_PORT_BASE <= port < APP_PORT_BASE + 1000
    result = subprocess.run(
        [sys.executable, "-c", "from smallapp.naming import port_for; print(port_for('expenses'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(result.stdout.strip()) == port


def test_occupied_port_probes_to_the_next_free_one() -> None:
    natural = port_for("expenses")
    assert port_for("expenses", {natural}) == natural + 1
    assert port_for("expenses", {natural, natural + 1}) == natural + 2


def test_probe_wraps_within_the_band() -> None:
    natural = port_for("expenses")
    taken = set(range(natural, APP_PORT_BASE + 1000))
    assert port_for("expenses", taken) == APP_PORT_BASE


def test_no_free_port_raises() -> None:
    with pytest.raises(ValidationError, match="no free port"):
        port_for("expenses", set(range(APP_PORT_BASE, APP_PORT_BASE + 1000)))


def test_gateway_port_is_app_port_plus_1000() -> None:
    assert gw_port_for(18412) == 19412
