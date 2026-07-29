"""Hygiene: no unfinished code ships, and every README command really parses."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from smallapp.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
README = ROOT / "README.md"
FORBIDDEN = ("TODO", "FIXME", "XXX", "NotImplementedError")

FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)
PLACEHOLDER = re.compile(r"[A-Z_]{3,}")


def test_no_unfinished_markers_in_shipped_code() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        for marker in FORBIDDEN
        if marker in line
    ]
    assert offenders == [], "unfinished markers in shipped code:\n" + "\n".join(offenders)


def readme_commands() -> list[str]:
    commands: list[str] = []
    for block in FENCE.findall(README.read_text()):
        for line in block.splitlines():
            stripped = line.strip().removeprefix("$ ").removeprefix("sudo ")
            if stripped.startswith("smallapp "):
                commands.append(stripped)
    return commands


def test_readme_has_a_quickstart() -> None:
    text = README.read_text()
    assert "## Quickstart" in text
    assert "smallapp apply" in text
    assert "import smallapp.d/*.caddy" in text, "a stranger needs the Caddy wiring"


def test_readme_commands_exist() -> None:
    assert len(readme_commands()) >= 6


@pytest.mark.parametrize("command", readme_commands())
def test_every_readme_command_parses(command: str) -> None:
    parser = build_parser()
    args = shlex.split(command)[1:]
    assert not any(PLACEHOLDER.fullmatch(part) for part in args), (
        f"{command!r} contains an unsubstituted placeholder"
    )
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exit_:  # --help and --version are legitimate documented usage
        assert exit_.code == 0, f"{command!r} does not parse"
        return
    assert parsed.command
