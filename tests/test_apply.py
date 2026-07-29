"""Apply and rm: modes, idempotence, reversal, and loud failure."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXED_SECRETS, make_unit, tree_hash
from smallapp import registry
from smallapp.apply import apply_unit, remove_unit
from smallapp.plan import resolve_secrets
from smallapp.system import StepError, System
from smallapp.target import Target


def test_apply_writes_every_artifact_with_the_right_mode(
    host: System, python_target: Target
) -> None:
    unit = make_unit()
    actions, token = apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert token is None  # FIXED_SECRETS came from disk, not fresh generation
    for path in (
        "/opt/smallapp/expenses/app.py",
        "/etc/smallapp/expenses.env",
        "/etc/systemd/system/smallapp-expenses.service",
        "/etc/systemd/system/smallapp-expenses-gw.service",
        "/etc/caddy/smallapp.d/expenses.caddy",
    ):
        assert host.path(path).is_file(), f"{path} was not written"
    assert host.mode_of("/etc/smallapp/expenses.env") == 0o600
    assert host.path("/var/lib/smallapp/expenses").is_dir()
    assert host.user_exists("sa-expenses")
    assert registry.load(host)["expenses"].port == 18412
    assert all(action.state == "create" for action in actions)


def test_apply_twice_is_byte_identical(host: System, python_target: Target) -> None:
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    first = tree_hash(host.root)
    actions, _ = apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert tree_hash(host.root) == first
    assert [action for action in actions if action.state != "unchanged"] == []


def test_rm_restores_the_tree_exactly(host: System, python_target: Target) -> None:
    before = tree_hash(host.root)
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert tree_hash(host.root) != before
    assert remove_unit(host, "expenses") is not None
    assert tree_hash(host.root) == before
    assert registry.load(host) == {}
    assert not host.user_exists("sa-expenses")


def test_rm_of_an_unknown_unit_is_a_no_op(host: System) -> None:
    assert remove_unit(host, "ghost") is None


def test_static_apply_writes_the_payload(host: System, static_target: Target) -> None:
    unit = make_unit(kind="static", name="notes")
    apply_unit(host, static_target, unit, FIXED_SECRETS)
    assert host.path("/opt/smallapp/notes/index.html").read_bytes() == b"<h1>hello</h1>\n"


def test_failure_names_the_failing_step(
    host: System, python_target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = System.write

    def exploding_write(self: System, absolute: str, content: bytes, mode: int) -> None:
        if absolute.endswith("app.py"):
            raise StepError(f"write {absolute}", "disk on fire")
        real_write(self, absolute, content, mode)

    monkeypatch.setattr(System, "write", exploding_write, raising=True)
    with pytest.raises(StepError) as exc:
        apply_unit(host, python_target, make_unit(), FIXED_SECRETS)
    assert "app.py" in exc.value.step
    assert "disk on fire" in str(exc.value)
    assert host.path("/etc/caddy/smallapp.d/expenses.caddy").is_file(), "earlier work stays put"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smallapp", *args], capture_output=True, text=True, check=False
    )


def test_cli_apply_status_rm_round_trip(tmp_path: Path, app_file: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    applied = run_cli(
        "apply",
        str(app_file),
        "--name",
        "expenses",
        "--domain",
        "expenses.example.com",
        "--tls",
        "internal",
        "--root",
        str(root),
    )
    assert applied.returncode == 0, applied.stderr
    assert "login token (shown once):" in applied.stdout

    again = run_cli(
        "apply",
        str(app_file),
        "--name",
        "expenses",
        "--domain",
        "expenses.example.com",
        "--tls",
        "internal",
        "--root",
        str(root),
    )
    assert again.returncode == 0, again.stderr
    assert "no changes." in again.stdout
    assert "login token" not in again.stdout

    status = run_cli("status", "--json", "--root", str(root))
    assert status.returncode == 0, status.stderr
    record = json.loads(status.stdout)["expenses"]
    assert record["kind"] == "python"
    assert record["domain"] == "expenses.example.com"
    assert record["tls"] == "internal"
    assert 18000 <= record["port"] <= 18999
    assert record["gw_port"] == record["port"] + 1000

    plain = run_cli("status", "expenses", "--root", str(root))
    assert plain.returncode == 0
    assert "expenses.example.com" in plain.stdout

    removed = run_cli("rm", "expenses", "--root", str(root))
    assert removed.returncode == 0, removed.stderr
    assert run_cli("rm", "expenses", "--root", str(root)).stdout.startswith("expenses: not found")
    assert not any(root.rglob("*"))


def test_cli_status_of_unknown_unit_fails(tmp_path: Path) -> None:
    result = run_cli("status", "ghost", "--root", str(tmp_path))
    assert result.returncode == 1
    assert "ghost" in result.stderr


def test_secret_never_leaks_into_plan_output(tmp_path: Path, app_file: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    run_cli(
        "apply",
        str(app_file),
        "--name",
        "expenses",
        "--domain",
        "e.example.com",
        "--root",
        str(root),
    )
    system = System(root)
    secrets = resolve_secrets(system, make_unit())
    planned = run_cli("plan", str(app_file), "--name", "expenses", "--domain", "e.example.com")
    assert secrets.secret not in planned.stdout
    assert secrets.token_hash not in planned.stdout
