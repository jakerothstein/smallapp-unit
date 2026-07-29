"""Skeleton check: the CLI is importable, runnable, and exposes the six commands."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from smallapp import COMMANDS
from smallapp.cli import build_parser, main
from smallapp.system import CADDY_ADMIN, CADDY_IMPORT, CADDYFILE


def test_help_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


def test_every_command_is_reachable() -> None:
    parser = build_parser()
    target_args = ["t.py", "--name", "x", "--domain", "x.example.com"]
    extra = {"plan": target_args, "apply": target_args, "rm": ["x"]}
    for command in COMMANDS:
        args = parser.parse_args([command, *extra.get(command, [])])
        assert args.command == command


def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "smallapp", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip()


def test_plan_prints_kind_and_ports(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = tmp_path / "expenses.py"
    app.write_text('import os\nport = int(os.environ["PORT"])\n')
    assert main(["plan", str(app), "--name", "expenses", "--domain", "expenses.example.com"]) == 0
    out = capsys.readouterr().out
    assert "plan: expenses (python, 127.0.0.1:" in out
    assert "-> https://expenses.example.com" in out
    assert "gateway 127.0.0.1:" in out


def test_plan_on_invalid_target_exits_2(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "nope.py"
    bad.write_text("print('no port here')\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "smallapp",
            "plan",
            str(bad),
            "--name",
            "x",
            "--domain",
            "x.example.com",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "PORT" in result.stderr


def test_plan_rejects_hostile_name(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = tmp_path / "app.py"
    app.write_text('import os\nos.environ["PORT"]\n')
    code = main(["plan", str(app), "--name", "Bad Name", "--domain", "x.example.com"])
    assert code == 2
    assert "Bad Name" in capsys.readouterr().err


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smallapp", *args], capture_output=True, text=True, check=False
    )


def test_doctor_through_the_cli_fails_on_a_bare_root(tmp_path: pathlib.Path) -> None:
    """QA round 2 #10: criterion 24 is about the command, so drive the command."""
    result = run_cli("doctor", "--root", str(tmp_path))
    assert result.returncode == 1
    for missing in ("systemctl present", "caddy present", "uv present", "imports smallapp.d"):
        assert missing in result.stderr
    assert "fix:" in result.stderr


def test_doctor_through_the_cli_passes_on_a_prepared_root(tmp_path: pathlib.Path) -> None:
    (tmp_path / "usr/bin").mkdir(parents=True)
    for program in ("systemctl", "caddy", "uv"):
        (tmp_path / "usr/bin" / program).write_text("#!/bin/sh\n")
    caddyfile = tmp_path / CADDYFILE.lstrip("/")
    caddyfile.parent.mkdir(parents=True)
    caddyfile.write_text(f"{{\n    {CADDY_ADMIN}\n}}\n\n{CADDY_IMPORT}\n")
    result = run_cli("doctor", "--root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "this host can host smallapp units." in result.stdout


def test_plan_previews_against_the_prefix_it_is_given(
    tmp_path: pathlib.Path, app_file: pathlib.Path
) -> None:
    """`plan` reads registry and disk state, so it needs the same `--root` as `apply`;
    without it a prefixed deploy could only ever be previewed against the real host."""
    root = tmp_path / "root"
    root.mkdir()
    args = (
        str(app_file),
        "--name",
        "preview",
        "--domain",
        "preview.example.com",
        "--tls",
        "internal",
        "--root",
        str(root),
    )
    before = run_cli("plan", *args)
    assert before.returncode == 0, before.stderr
    assert "no changes" not in before.stdout

    applied = run_cli("apply", *args)
    assert applied.returncode == 0, applied.stderr

    after = run_cli("plan", *args)
    assert after.returncode == 0, after.stderr
    assert "no changes" in after.stdout, after.stdout
    assert "+ " not in after.stdout, "a second plan wants to redo work that is already done"


def test_plan_out_reports_a_filesystem_error_without_a_traceback(
    tmp_path: pathlib.Path, app_file_for_cli: pathlib.Path
) -> None:
    """QA round 6 #7: --out onto a regular file is a user error, not a crash."""
    blocked = tmp_path / "already-a-file"
    blocked.write_text("not a directory\n")
    result = run_cli(
        "plan",
        str(app_file_for_cli),
        "--name",
        "x",
        "--domain",
        "x.example.com",
        "--out",
        str(blocked),
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert str(blocked) in result.stderr


def test_apply_on_an_invalid_target_exits_1(tmp_path: pathlib.Path) -> None:
    """QA round 6 #8: every apply failure is exit 1; exit 2 belongs to plan."""
    root = tmp_path / "root"
    root.mkdir()
    result = run_cli(
        "apply",
        str(tmp_path / "nonexistent.py"),
        "--name",
        "x",
        "--domain",
        "x.example.com",
        "--root",
        str(root),
    )
    assert result.returncode == 1
    assert "nonexistent.py" in result.stderr


def test_apply_on_a_hostile_name_exits_1(tmp_path: pathlib.Path, app_file: pathlib.Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    result = run_cli(
        "apply",
        str(app_file),
        "--name",
        "Bad Name",
        "--domain",
        "x.example.com",
        "--root",
        str(root),
    )
    assert result.returncode == 1
    assert "Bad Name" in result.stderr


@pytest.fixture
def app_file_for_cli(tmp_path: pathlib.Path) -> pathlib.Path:
    app = tmp_path / "app.py"
    app.write_text('import os\nos.environ["PORT"]\n')
    return app
