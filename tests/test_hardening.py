"""systemd-analyze security on the rendered units (acceptance criterion 26).

Runs natively on Linux with systemd. Everywhere else it borrows a Linux container,
because a criterion that only ever skips is a criterion nobody has checked.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXED_SECRETS, make_unit
from smallapp.render import render
from smallapp.target import Target

MAX_EXPOSURE = 4.0
SERVICES = ("smallapp-expenses.service", "smallapp-expenses-gw.service")
IMAGE = "debian:bookworm-slim"
# `--json=short` reports per-setting rows whose "exposure" is null; the overall score
# exists only in the human output, so that is what gets parsed.
OVERALL = re.compile(r"Overall exposure level for (\S+): ([0-9.]+)")


def _native() -> bool:
    return sys.platform == "linux" and shutil.which("systemd-analyze") is not None


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"{command}\n{result.stdout}\n{result.stderr}"
    return result.stdout


def exposures(directory: Path) -> dict[str, float]:
    """Overall exposure per rendered service in `directory`, keyed by file name."""
    if _native():
        output = "".join(
            _run(["systemd-analyze", "security", "--offline=true", str(directory / service)])  # noqa: S607
            for service in SERVICES
        )
    else:
        analyse = " && ".join(
            f"systemd-analyze security --offline=true /u/{service}" for service in SERVICES
        )
        script = (
            "apt-get update -qq >/dev/null && "
            "apt-get install -y -qq systemd >/dev/null 2>&1 && " + analyse
        )
        output = _run(
            ["docker", "run", "--rm", "-v", f"{directory}:/u:ro", IMAGE, "bash", "-c", script]  # noqa: S607
        )
    return {name: float(score) for name, score in OVERALL.findall(output)}


@pytest.mark.slow
@pytest.mark.skipif(
    not _native() and shutil.which("docker") is None,
    reason=f"needs Linux with systemd-analyze, or docker to run {IMAGE}",
)
def test_rendered_units_are_hardened(tmp_path: Path, python_target: Target) -> None:
    files = render(python_target, make_unit(), FIXED_SECRETS.secret, FIXED_SECRETS.token_hash)
    for service in SERVICES:
        (tmp_path / service).write_bytes(files[f"/etc/systemd/system/{service}"].content)
    scores = exposures(tmp_path)
    assert set(scores) == set(SERVICES), f"no exposure score parsed: {scores}"
    for service, score in scores.items():
        assert score < MAX_EXPOSURE, f"{service} scores {score}, the limit is {MAX_EXPOSURE}"
