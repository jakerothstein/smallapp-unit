"""systemd-analyze security on the rendered units. Linux + systemd only."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXED_SECRETS, make_unit
from smallapp.render import render
from smallapp.target import Target

MAX_EXPOSURE = 4.0

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("systemd-analyze") is None,
    reason=(
        "systemd-analyze security needs Linux with systemd; this host is "
        f"{sys.platform} with systemd-analyze "
        f"{'present' if shutil.which('systemd-analyze') else 'absent'}"
    ),
)


def exposure(unit_file: Path) -> float:
    result = subprocess.run(  # noqa: S603
        [
            "systemd-analyze",  # noqa: S607 - resolved via PATH on the host under test
            "security",
            "--offline=true",
            "--json=short",
            str(unit_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    entry = report[0] if isinstance(report, list) else report
    return float(entry["exposure"])


@pytest.mark.parametrize("service", ["smallapp-expenses.service", "smallapp-expenses-gw.service"])
def test_rendered_units_are_hardened(tmp_path: Path, python_target: Target, service: str) -> None:
    files = render(python_target, make_unit(), FIXED_SECRETS.secret, FIXED_SECRETS.token_hash)
    unit_file = tmp_path / service
    unit_file.write_bytes(files[f"/etc/systemd/system/{service}"].content)
    score = exposure(unit_file)
    assert score < MAX_EXPOSURE, f"{service} scores {score}, the limit is {MAX_EXPOSURE}"
