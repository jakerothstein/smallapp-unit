"""The OS seam: path prefixing, atomic writes, pruning, and host preflight."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from smallapp.system import (
    CADDY_ADMIN,
    CADDY_IMPORT,
    CADDYFILE,
    StepError,
    System,
    preflight,
)


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


def test_prune_created_removes_only_what_smallapp_made(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.mkdir("/var/lib/smallapp/expenses")
    system.prune_created()
    assert tmp_path.exists()
    assert not (tmp_path / "var").exists()


def test_prune_created_keeps_non_empty_dirs(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.write("/var/lib/smallapp/keep.json", b"{}", 0o644)
    system.mkdir("/var/lib/smallapp/expenses")
    system.prune_created()
    assert system.path("/var/lib/smallapp/keep.json").is_file()


def test_prune_created_never_removes_a_pre_existing_directory(tmp_path: Path) -> None:
    """QA round 6 #5: a directory smallapp found is a directory smallapp leaves."""
    (tmp_path / "etc/caddy/smallapp.d").mkdir(parents=True)
    system = System(tmp_path)
    system.mkdir("/var/lib/smallapp")
    system.prune_created()
    assert (tmp_path / "etc/caddy/smallapp.d").is_dir()
    assert not (tmp_path / "var").exists()


def test_created_record_survives_into_another_process(tmp_path: Path) -> None:
    first = System(tmp_path)
    first.mkdir("/opt/smallapp/expenses")
    first.mkdir("/var/lib/smallapp")
    first.flush_created()

    second = System(tmp_path)
    second.remove("/opt/smallapp/expenses")
    second.prune_created()
    assert not (tmp_path / "opt").exists()
    assert not (tmp_path / "var").exists()


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
    caddyfile.write_text(f"{{\n    {CADDY_ADMIN}\n}}\n\n{CADDY_IMPORT}\n")


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
    (tmp_path / CADDYFILE.lstrip("/")).write_text(f"# nothing here\n{{\n    {CADDY_ADMIN}\n}}\n")
    failed = [check for check in preflight(System(tmp_path)) if not check.ok]
    assert len(failed) == 1
    assert "imports smallapp.d" in failed[0].name


def test_a_symlinked_component_cannot_escape_the_root(tmp_path: Path) -> None:
    """QA round 3 #2: `<root>/etc -> /outside` must not let a write land outside."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "etc").symlink_to(outside)
    system = System(root)

    calls: tuple[Callable[[], object], ...] = (
        lambda: system.write("/etc/escaped", b"pwned\n", 0o600),
        lambda: system.mkdir("/etc/escaped-dir"),
        lambda: system.read("/etc/escaped"),
        lambda: system.remove("/etc/escaped"),
    )
    for call in calls:
        with pytest.raises(StepError, match="symlink"):
            call()
    assert list(outside.iterdir()) == [], "something was written outside the root"


def test_a_symlinked_leaf_is_refused_too(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "etc/smallapp").mkdir(parents=True)
    target = tmp_path / "outside.env"
    target.write_text("original\n")
    (root / "etc/smallapp/x.env").symlink_to(target)
    with pytest.raises(StepError, match="symlink"):
        System(root).write("/etc/smallapp/x.env", b"pwned\n", 0o600)
    assert target.read_text() == "original\n"


def test_dot_dot_components_are_refused(tmp_path: Path) -> None:
    with pytest.raises(StepError, match=r"\.\."):
        System(tmp_path).path("/etc/../../escape")


def test_secret_files_are_created_at_their_final_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA round 3 #9: never 0644-then-chmod; a watcher must never see it readable."""
    seen: list[int] = []
    real_open = os.open

    def spy(path: object, flags: int, mode: int = 0o777, **kwargs: object) -> int:
        if flags & os.O_CREAT:
            seen.append(mode)
            assert flags & os.O_EXCL, "the secret file must be created exclusively"
        return real_open(path, flags, mode, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    monkeypatch.setattr(os, "umask", lambda mask: 0o022)
    System(tmp_path).write("/etc/smallapp/x.env", b"SMALLAPP_SECRET=s\n", 0o600)
    assert seen == [0o600], f"created at {[oct(mode) for mode in seen]}, not 0600"


def test_delete_user_surfaces_an_unexpected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA round 3 #13: a live process holding the account must not be silently ignored."""
    system = System("/")

    def refuse(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 8, "", "userdel: user sa-x is currently used")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(StepError, match="currently used") as exc:
        system.delete_user("sa-x")
    assert exc.value.step == "delete user sa-x"


def test_delete_user_tolerates_an_already_absent_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def absent(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 6, "", "userdel: user 'sa-x' does not exist")

    monkeypatch.setattr(subprocess, "run", absent)
    System("/").delete_user("sa-x")  # no raise


@pytest.mark.parametrize(
    "caddyfile",
    [
        "# import smallapp.d/*.caddy\n",
        "  #import smallapp.d/*.caddy\n",
        'respond "import smallapp.d/*.caddy"\n',
        "importsmallapp.d/*.caddy\n",
        "",
    ],
)
def test_doctor_rejects_an_inactive_import(tmp_path: Path, caddyfile: str) -> None:
    """QA round 3 #12: a commented-out directive is not configuration."""
    prepare_host(tmp_path)
    (tmp_path / CADDYFILE.lstrip("/")).write_text(f"{{\n    {CADDY_ADMIN}\n}}\n{caddyfile}")
    failed = [check for check in preflight(System(tmp_path)) if not check.ok]
    assert [check.name for check in failed] == [f"{CADDYFILE} imports smallapp.d/*.caddy"]


@pytest.mark.parametrize(
    ("admin", "ok"),
    [
        ("admin unix//run/caddy/admin.sock", True),
        ("admin off", True),
        ("admin 127.0.0.1:2019", False),
        ("# admin off", False),
        ("", False),
    ],
)
def test_doctor_requires_the_admin_api_off_the_network(
    tmp_path: Path, admin: str, ok: bool
) -> None:
    """QA round 3 #3: an app that can POST to the admin API can rewrite every vhost."""
    prepare_host(tmp_path)
    (tmp_path / CADDYFILE.lstrip("/")).write_text(f"{{\n    {admin}\n}}\n{CADDY_IMPORT}\n")
    checks = {check.name: check.ok for check in preflight(System(tmp_path))}
    assert checks["caddy admin API is off the network"] is ok


def test_list_files_walks_the_payload(tmp_path: Path) -> None:
    system = System(tmp_path)
    system.write("/opt/smallapp/notes/index.html", b"a", 0o644)
    system.write("/opt/smallapp/notes/sub/old.txt", b"b", 0o644)
    assert system.list_files("/opt/smallapp/notes") == [
        "/opt/smallapp/notes/index.html",
        "/opt/smallapp/notes/sub/old.txt",
    ]
    assert system.list_files("/opt/smallapp/nope") == []


def test_the_lock_is_exclusive_between_holders(tmp_path: Path) -> None:
    """QA round 3 #6: the lock must actually block a second holder."""
    system = System(tmp_path)
    order: list[str] = []
    started = threading.Event()

    def second() -> None:
        started.wait(timeout=5)
        with system.lock():
            order.append("second")

    thread = threading.Thread(target=second)
    with system.lock():
        thread.start()
        started.set()
        time.sleep(0.2)
        order.append("first")
    thread.join(timeout=5)
    assert order == ["first", "second"]
