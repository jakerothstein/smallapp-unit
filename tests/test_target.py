"""Deploy target detection: every accept and every rejection message."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from smallapp.target import TargetError, declares_dependencies, detect, reads_port

APP = 'import os\nport = int(os.environ["PORT"])\n'


def test_python_file_reading_port_is_python(tmp_path: Path) -> None:
    app = tmp_path / "expenses.py"
    app.write_text(APP)
    target = detect(app)
    assert target.kind == "python"
    assert target.entry == app.resolve()
    assert target.files == [app.resolve()]


def test_directory_with_index_html_is_static(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / "style.css").write_text("body{}")
    target = detect(tmp_path)
    assert target.kind == "static"
    assert target.entry is None
    assert [p.name for p in target.files] == ["index.html", "style.css"]


def test_python_file_without_port_is_rejected(tmp_path: Path) -> None:
    app = tmp_path / "expenses.py"
    app.write_text("print('hello')\n")
    with pytest.raises(TargetError, match="PORT"):
        detect(app)


def test_missing_path_is_rejected_by_name(tmp_path: Path) -> None:
    missing = tmp_path / "nope.py"
    with pytest.raises(TargetError, match="nope.py"):
        detect(missing)


def test_directory_without_index_html_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "other.html").write_text("<h1>hi</h1>")
    with pytest.raises(TargetError, match="index.html"):
        detect(tmp_path)


def test_non_python_file_is_rejected(tmp_path: Path) -> None:
    other = tmp_path / "app.rb"
    other.write_text("puts 1")
    with pytest.raises(TargetError, match=r"\.py"):
        detect(other)


def test_non_utf8_python_file_is_rejected(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_bytes(b"PORT = \xff\xfe\n")
    with pytest.raises(TargetError, match="UTF-8"):
        detect(app)


def test_static_directory_with_symlink_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / "link.html").symlink_to(tmp_path / "index.html")
    with pytest.raises(TargetError, match="symlink"):
        detect(tmp_path)


def test_port_substring_does_not_count(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_text("SUPPORTED = 1\n")
    with pytest.raises(TargetError, match="PORT"):
        detect(app)


PORT_FALSE_POSITIVES = [
    pytest.param("# PORT\nprint('not a server')\n", id="comment"),
    pytest.param('print("not a server: PORT")\n', id="string-literal"),
    pytest.param('"""A docstring mentioning PORT."""\n', id="docstring"),
    pytest.param("PORT = 8080\nprint(PORT)\n", id="local-variable"),
    pytest.param("class PORT:\n    pass\n", id="class-name"),
    pytest.param('import os\nos.environ["PORTAL"]\n', id="different-variable"),
]

PORT_REAL_READS = [
    pytest.param('import os\nport = int(os.environ["PORT"])\n', id="subscript"),
    pytest.param('import os\nport = os.environ.get("PORT", "8080")\n', id="environ-get"),
    pytest.param('import os\nport = os.getenv("PORT")\n', id="os-getenv"),
    pytest.param('from os import environ\nport = environ["PORT"]\n', id="imported-environ"),
    pytest.param('from os import getenv\nport = getenv("PORT")\n', id="imported-getenv"),
]


@pytest.mark.parametrize("source", PORT_FALSE_POSITIVES)
def test_mentioning_port_is_not_reading_it(tmp_path: Path, source: str) -> None:
    """QA round 3 #11: a comment or a string is not an environment variable read."""
    app = tmp_path / "app.py"
    app.write_text(source)
    with pytest.raises(TargetError, match="PORT"):
        detect(app)


@pytest.mark.parametrize("source", PORT_REAL_READS)
def test_every_real_way_of_reading_port_is_accepted(tmp_path: Path, source: str) -> None:
    app = tmp_path / "app.py"
    app.write_text(source)
    assert detect(app).kind == "python"


def test_unparseable_python_is_rejected_with_its_line(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_text('import os\ndef (:\nos.environ["PORT"]\n')
    with pytest.raises(TargetError, match="not valid Python"):
        detect(app)


def test_plan_exits_2_on_a_comment_only_port(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_text('# PORT\nprint("not a server")\n')
    result = subprocess.run(
        [sys.executable, "-m", "smallapp", "plan", str(app), "--name", "x", "--domain", "x.io"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout
    assert "PORT" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        'import os\nos.getenv(key="PORT")\n',
        'import os\nos.getenv("PORT", "8000")\n',
        'from os import getenv\ngetenv("PORT")\n',
        'import os\nos.environ.get("PORT")\n',
        'from os import environ\nenviron["PORT"]\n',
        'import os\nos.environb[b"PORT"]\n',
    ],
)
def test_real_environment_reads_are_accepted(source: str) -> None:
    """QA round 6 #6: keyword and positional reads of the real `os` APIs all count."""
    assert reads_port(source)


@pytest.mark.parametrize(
    "source",
    [
        'Fake().getenv("PORT")\n',
        'class Fake:\n    def getenv(self, k): ...\nFake().environ.get("PORT")\n',
        'config.getenv("PORT")\n',
        'settings.environ["PORT"]\n',
        '"PORT"\n',
    ],
)
def test_impostor_environment_reads_are_rejected(source: str) -> None:
    """QA round 6 #6: a method merely named `getenv` does not serve on $PORT."""
    assert not reads_port(source)


def test_pep723_block_detection_needs_the_whole_block() -> None:
    assert declares_dependencies('# /// script\n# dependencies = ["httpx"]\n# ///\n')
    assert declares_dependencies("# /// script\n# requires-python = '>=3.11'\n# ///\n")
    assert not declares_dependencies('"""a docstring saying # /// script"""\n')
    assert not declares_dependencies("# /// script\n# dependencies = []\n")
