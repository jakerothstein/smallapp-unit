"""Deploy target detection: every accept and every rejection message."""

from __future__ import annotations

from pathlib import Path

import pytest

from smallapp.target import TargetError, detect

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
