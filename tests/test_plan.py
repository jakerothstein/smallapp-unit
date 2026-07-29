"""Planning: create against an empty host, unchanged against an applied one."""

from __future__ import annotations

from conftest import FIXED_SECRETS, make_unit
from smallapp import registry
from smallapp.apply import apply_unit
from smallapp.plan import build, resolve_secrets, summarise
from smallapp.system import System
from smallapp.target import Target


def test_everything_is_create_against_an_empty_host(host: System, python_target: Target) -> None:
    actions = build(host, python_target, make_unit(), FIXED_SECRETS)
    assert actions, "a plan must not be empty"
    assert {action.state for action in actions} == {"create"}
    assert "0 unchanged" in summarise(actions)


def test_everything_is_unchanged_after_apply(host: System, python_target: Target) -> None:
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    actions = build(host, python_target, unit, FIXED_SECRETS, registry.load(host).get(unit.name))
    assert {action.state for action in actions} == {"unchanged"}
    assert summarise(actions).endswith("no changes.")


def test_a_touched_file_is_reported_as_change(host: System, python_target: Target) -> None:
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    host.write(str(unit.vhost_path), b"tampered\n", 0o644)
    actions = build(host, python_target, unit, FIXED_SECRETS, registry.load(host).get(unit.name))
    states = {action.target: action.state for action in actions}
    assert states[str(unit.vhost_path)] == "change"
    assert states["caddy"] == "create"


def test_a_wrong_mode_is_reported_as_change(host: System, python_target: Target) -> None:
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    host.path(str(unit.env_path)).chmod(0o644)
    actions = build(host, python_target, unit, FIXED_SECRETS, registry.load(host).get(unit.name))
    states = {action.target: action.state for action in actions}
    assert states[str(unit.env_path)] == "change"


def test_plan_lines_are_marked(host: System, python_target: Target) -> None:
    actions = build(host, python_target, make_unit(), FIXED_SECRETS)
    lines = [action.line() for action in actions]
    assert any(line.startswith("  + write") for line in lines)
    assert any("(0600)" in line for line in lines)


def test_secrets_are_reused_once_written(host: System, python_target: Target) -> None:
    unit = make_unit()
    fresh = resolve_secrets(host, unit)
    assert fresh.token is not None
    apply_unit(host, python_target, unit, fresh)
    again = resolve_secrets(host, unit, registry.load(host).get(unit.name))
    assert again.token is None
    assert again.secret == fresh.secret
    assert again.token_hash == fresh.token_hash
