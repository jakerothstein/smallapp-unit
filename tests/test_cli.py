"""Skeleton check: the CLI is importable, runnable, and exposes the six commands."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from smallapp import COMMANDS
from smallapp.cli import build_parser, main


def test_help_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command in out


def test_every_command_is_reachable() -> None:
    parser = build_parser()
    extra = {
        "plan": ["t.py", "--name", "x", "--domain", "x.example.com"],
    }
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
    app.write_text("import os\nPORT\n")
    code = main(["plan", str(app), "--name", "Bad Name", "--domain", "x.example.com"])
    assert code == 2
    assert "Bad Name" in capsys.readouterr().err
