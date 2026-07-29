"""Apply and rm: modes, idempotence, reversal, and loud failure."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import FIXED_SECRETS, make_unit, tree_hash
from smallapp import registry
from smallapp.apply import apply_unit, remove_unit
from smallapp.naming import ValidationError
from smallapp.system import StepError, System
from smallapp.target import Target, detect


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
    """QA round 2 #8: read the secret *this* plan rendered, not an unrelated one."""
    out = tmp_path / "out"
    planned = run_cli(
        "plan",
        str(app_file),
        "--name",
        "expenses",
        "--domain",
        "e.example.com",
        "--out",
        str(out),
    )
    assert planned.returncode == 0, planned.stderr
    values = dict(
        line.split("=", 1)
        for line in (out / "etc/smallapp/expenses.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["SMALLAPP_SECRET"] and values["SMALLAPP_TOKEN_HASH"]
    for stream in (planned.stdout, planned.stderr):
        assert values["SMALLAPP_SECRET"] not in stream
        assert values["SMALLAPP_TOKEN_HASH"] not in stream
    assert (out / "etc/smallapp/expenses.env").stat().st_mode & 0o777 == 0o600


def test_plan_out_cannot_be_walked_out_of_by_a_symlink(tmp_path: Path, app_file: Path) -> None:
    """QA round 3 #2: `--out` confinement is the same promise as `--root`."""
    out = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    (out / "etc").mkdir(parents=True)
    (out / "etc/smallapp").symlink_to(outside)
    planned = run_cli(
        "plan", str(app_file), "--name", "e", "--domain", "e.example.com", "--out", str(out)
    )
    assert planned.returncode != 0
    assert "symlink" in planned.stderr
    assert list(outside.iterdir()) == []


def apply_args(source: Path, name: str, root: Path) -> list[str]:
    return [
        "apply",
        str(source),
        "--name",
        name,
        "--domain",
        f"{name}.example.com",
        "--root",
        str(root),
    ]


def test_a_delayed_reapply_changes_not_one_byte(tmp_path: Path, app_file: Path) -> None:
    """QA round 3 #7: `no changes.` must mean the registry is untouched, clock or not."""
    root = tmp_path / "root"
    root.mkdir()
    assert run_cli(*apply_args(app_file, "expenses", root)).returncode == 0
    registry_file = root / "var/lib/smallapp/registry.json"
    before_hash, before_tree = registry_file.read_bytes(), tree_hash(root)

    time.sleep(2.1)
    again = run_cli(*apply_args(app_file, "expenses", root))
    assert again.returncode == 0, again.stderr
    assert "no changes." in again.stdout
    assert registry_file.read_bytes() == before_hash
    assert tree_hash(root) == before_tree


def test_a_preexisting_unix_user_is_never_adopted(host: System, python_target: Target) -> None:
    """QA round 3 #4: an account smallapp did not make must not be taken over."""
    host.create_user("sa-expenses")
    with pytest.raises(ValidationError, match="sa-expenses"):
        apply_unit(host, python_target, make_unit(), FIXED_SECRETS)
    assert registry.load(host) == {}
    assert host.user_exists("sa-expenses"), "the foreign account must survive"


def test_rm_never_deletes_a_user_smallapp_did_not_create(
    host: System, python_target: Target
) -> None:
    unit = make_unit()
    apply_unit(host, python_target, unit, FIXED_SECRETS)
    registry.put(host, replace(registry.load(host)["expenses"], created_user=False))
    assert remove_unit(host, "expenses") is not None
    assert host.user_exists("sa-expenses"), "an adopted account must outlive the unit"
    assert host.user_exists("sa-expenses-gw")


def test_rm_deletes_both_users_it_created(host: System, python_target: Target) -> None:
    apply_unit(host, python_target, make_unit(), FIXED_SECRETS)
    assert host.user_exists("sa-expenses") and host.user_exists("sa-expenses-gw")
    remove_unit(host, "expenses")
    assert not host.user_exists("sa-expenses")
    assert not host.user_exists("sa-expenses-gw")


def test_a_retry_after_a_failed_reload_redoes_the_reload(
    host: System, python_target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA round 3 #5: an incomplete apply must never be reported as done."""
    reloads: list[str] = []

    def first_reload_explodes(self: System, caddyfile: str = "/etc/caddy/Caddyfile") -> None:
        reloads.append(caddyfile)
        if len(reloads) == 1:
            raise StepError("reload caddy", "caddy is not running")

    monkeypatch.setattr(System, "caddy_reload", first_reload_explodes)
    unit = make_unit()
    with pytest.raises(StepError, match="reload caddy"):
        apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert registry.load(host)["expenses"].complete is False

    actions, _ = apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert len(reloads) == 2, "the retry skipped the reload that failed"
    assert [a for a in actions if a.verb == "caddy_reload"][0].state == "create"
    assert registry.load(host)["expenses"].complete is True

    third, _ = apply_unit(host, python_target, unit, FIXED_SECRETS)
    assert len(reloads) == 2, "a complete unit must not reload again"
    assert all(action.state == "unchanged" for action in third)


def test_removed_static_files_are_undeployed(host: System, static_dir: Path) -> None:
    """QA round 3 #10: deleting a page from the source must take it off the internet."""
    (static_dir / "old.txt").write_text("yesterday\n")
    unit = make_unit(kind="static", name="notes")
    apply_unit(host, detect(static_dir), unit, FIXED_SECRETS)
    assert host.path("/opt/smallapp/notes/old.txt").is_file()

    (static_dir / "old.txt").unlink()
    actions, _ = apply_unit(host, detect(static_dir), unit, FIXED_SECRETS)
    assert not host.path("/opt/smallapp/notes/old.txt").exists()
    assert any(a.verb == "rm" and a.target.endswith("old.txt") for a in actions)
    assert host.path("/opt/smallapp/notes/index.html").is_file()


def test_concurrent_first_applies_keep_both_units(tmp_path: Path, app_file: Path) -> None:
    """QA round 3 #6: without a lock both applies pick a port and one entry is lost."""
    root = tmp_path / "root"
    root.mkdir()
    names = ["alpha", "beta", "gamma", "delta"]
    barrier = threading.Barrier(len(names))

    def apply_one(name: str) -> subprocess.CompletedProcess[str]:
        barrier.wait(timeout=30)
        return run_cli(*apply_args(app_file, name, root))

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = list(pool.map(apply_one, names))

    for name, result in zip(names, results, strict=True):
        assert result.returncode == 0, f"{name}: {result.stderr}"
    units = registry.load(System(root))
    assert sorted(units) == sorted(names), "a concurrent apply lost a unit"
    ports = [unit.port for unit in units.values()]
    assert len(set(ports)) == len(ports), f"duplicate ports allocated: {ports}"
