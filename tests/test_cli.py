"""Skeleton check: the CLI is importable, runnable, and exposes the six commands."""

from __future__ import annotations

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
    for command in COMMANDS:
        assert parser.parse_args([command]).command == command


def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "smallapp", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
