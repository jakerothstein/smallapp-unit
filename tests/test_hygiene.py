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


SERVICE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin"
REPO_URL = "https://github.com/jakerothstein/smallapp-unit"


def test_no_nonexistent_repository_is_advertised() -> None:
    """QA round 3 #8: `github.com/smallapp/unit` does not exist and never installed."""
    for path in [README, *sorted(SRC.rglob("*.py"))]:
        assert "github.com/smallapp/unit" not in path.read_text(), path


def test_the_documented_install_lands_on_the_service_path() -> None:
    """The generated units run `/usr/bin/env smallapp gateway` as `sa-NAME-gw`, so the
    README must install into a directory on the service PATH, not under /root."""
    text = README.read_text()
    install = next(line for line in text.splitlines() if "uv tool install" in line)
    previous = text.splitlines()[text.splitlines().index(install) - 1]
    command = f"{previous} {install}"
    assert f"--from git+{REPO_URL}" in command
    bin_dir = re.search(r"UV_TOOL_BIN_DIR=(\S+)", command)
    tool_dir = re.search(r"UV_TOOL_DIR=(\S+)", command)
    assert bin_dir is not None and tool_dir is not None, command
    assert bin_dir.group(1) in SERVICE_PATH.split(":"), "the executable is off the service PATH"
    assert not tool_dir.group(1).startswith("/root"), "the tool env is unreadable to sa-NAME-gw"


def test_readme_does_not_overstate_what_doctor_checks() -> None:
    """QA round 3 #16: doctor checks neither DNS nor open ports."""
    text = README.read_text()
    requirements = text[text.index("## Requirements") : text.index("## Quickstart")]
    assert "checks all of this" not in requirements
    assert "does not check these" in requirements


def test_readme_documents_the_caddy_admin_hardening() -> None:
    text = README.read_text()
    assert "admin unix//run/caddy/admin.sock" in text
    assert "2019" in text, "a stranger needs to know what the default admin port exposes"
