"""Shared fixtures: a real single-file app, a static site, and a prefixed System."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from smallapp.naming import Unit
from smallapp.plan import Secrets
from smallapp.system import System
from smallapp.target import Target, detect

SAMPLE_APP = '''\
"""A smallapp sample: serves `hello` on 127.0.0.1:$PORT with the stdlib only."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ["PORT"])
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''

FIXED_SECRETS = Secrets(
    secret="FIXED-TEST-SECRET",
    token_hash="scrypt$16384$8$1$YWFhYWFhYWFhYWFhYWFhYQ$Zml4ZWQtdGVzdC1oYXNoLXZhbHVlLXh4eHh4",
    token=None,
)


@pytest.fixture
def app_file(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    app = root / "expenses.py"
    app.write_text(SAMPLE_APP)
    return app


@pytest.fixture
def python_target(app_file: Path) -> Target:
    return detect(app_file)


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text("<h1>hello</h1>\n")
    return root


@pytest.fixture
def static_target(static_dir: Path) -> Target:
    return detect(static_dir)


@pytest.fixture
def host(tmp_path: Path) -> System:
    root = tmp_path / "root"
    root.mkdir()
    return System(root)


def make_unit(kind: str = "python", name: str = "expenses", tls: str = "acme") -> Unit:
    return Unit(
        name=name,
        domain=f"{name}.example.com",
        kind=kind,
        port=18412,
        gw_port=19412,
        tls=tls,
        created_at="2024-01-01T00:00:00+00:00",
    )


def tree_hash(root: Path) -> str:
    """A recursive hash of names, modes and bytes, for before/after comparisons."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(f"{path.stat().st_mode & 0o777:o}".encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
