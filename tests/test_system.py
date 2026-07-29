"""The OS seam: path prefixing, atomic writes, pruning, and host preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from smallapp.system import CADDY_IMPORT, CADDYFILE, StepError, System, preflight


def test_paths_are_confined_to_the_root(tmp_path: Path) -> None:
    system = System(tmp_path)
    assert system.path("/etc/smallapp/x.env") == tmp_path.resolve() / "etc/smallapp/x.env"
    with pytest.raises(ValueError, match="absolute"):
        system.path("relative/path")


def test_real_root_is_not_prefixed() -> None:
    assert System("/").prefixed is False
    assert System("/").path("/etc/hosts") == Path("/etc/hosts")


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.write("/etc/smallapp/x.env", b"one\n", 0o600)
    system.write("/etc/smallapp/x.env", b"two\n", 0o600)
    assert system.read("/etc/smallapp/x.env") == b"two\n"
    assert system.mode_of("/etc/smallapp/x.env") == 0o600
    assert [p.name for p in (tmp_path / "etc/smallapp").iterdir()] == ["x.env"]


def test_read_and_mode_of_absent_paths_are_none(tmp_path: Path) -> None:
    system = System(tmp_path)
    assert system.read("/nope") is None
    assert system.mode_of("/nope") is None


def test_remove_handles_files_dirs_and_absences(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.write("/opt/smallapp/a/app.py", b"x", 0o644)
    system.remove("/opt/smallapp/a")
    assert not system.path("/opt/smallapp/a").exists()
    system.remove("/opt/smallapp/a")


def test_prune_empty_stops_at_the_root(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.mkdir("/var/lib/smallapp/expenses")
    system.prune_empty("/var/lib/smallapp/expenses")
    assert tmp_path.exists()
    assert not (tmp_path / "var").exists()


def test_prune_empty_keeps_non_empty_dirs(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.write("/var/lib/smallapp/keep.json", b"{}", 0o644)
    system.mkdir("/var/lib/smallapp/expenses")
    system.prune_empty("/var/lib/smallapp/expenses")
    assert system.path("/var/lib/smallapp/keep.json").is_file()


def test_prefixed_user_lifecycle_is_recorded(tmp_path: Path) -> None:
    system = System(tmp_path)
    assert not system.user_exists("sa-x")
    system.create_user("sa-x")
    assert system.user_exists("sa-x")
    system.delete_user("sa-x")
    assert not system.user_exists("sa-x")
    system.delete_user("sa-x")


def test_prefixed_privileged_verbs_are_no_ops(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.systemctl("daemon-reload")
    system.caddy_reload()
    system.chown("/nothing", "nobody")
    assert system.systemctl_output("is-active", "anything") == "prefixed"


def test_run_raises_step_error_naming_the_step(tmp_path: Path) -> None:
    system = System(tmp_path)
    with pytest.raises(StepError) as exc:
        system._run("do the thing", ["/bin/sh", "-c", "exit 3"])
    assert exc.value.step == "do the thing"
    with pytest.raises(StepError, match="missing"):
        system._run("missing binary", ["/nonexistent/binary-xyz"])


def prepare_host(root: Path) -> None:
    (root / "usr/bin").mkdir(parents=True)
    for program in ("systemctl", "caddy", "uv"):
        (root / "usr/bin" / program).write_text("#!/bin/sh\n")
    caddyfile = root / CADDYFILE.lstrip("/")
    caddyfile.parent.mkdir(parents=True)
    caddyfile.write_text(f"{CADDY_IMPORT}\n")


def test_preflight_passes_on_a_prepared_root(tmp_path: Path) -> None:
    prepare_host(tmp_path)
    assert all(check.ok for check in preflight(System(tmp_path)))


def test_preflight_reports_each_missing_prerequisite(tmp_path: Path) -> None:
    failed = {check.name: check for check in preflight(System(tmp_path)) if not check.ok}
    assert "systemctl present" in failed
    assert "caddy present" in failed
    assert "uv present" in failed
    assert any("imports smallapp.d" in name for name in failed)
    assert all(check.fix for check in failed.values())


def test_preflight_notices_a_caddyfile_without_the_import(tmp_path: Path) -> None:
    prepare_host(tmp_path)
    (tmp_path / CADDYFILE.lstrip("/")).write_text("# nothing here\n")
    failed = [check for check in preflight(System(tmp_path)) if not check.ok]
    assert len(failed) == 1
    assert "imports smallapp.d" in failed[0].name
