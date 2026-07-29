"""The real user path: apply into a prefix, run what was rendered, then log in.

No root, no network, no Caddy. The app and gateway are started from the *rendered*
ExecStart lines and the *rendered* env file, so this test breaks if any of them is
wrong. Only the two ports are overridden, because a test may not assume a fixed port
is free on the machine running it.
"""

from __future__ import annotations

import http.client
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from smallapp.tokens import COOKIE_NAME

STARTUP_TIMEOUT = 15.0


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smallapp", *args], capture_output=True, text=True, check=False
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def exec_start(unit_file: Path, root: Path) -> list[str]:
    """The rendered ExecStart, re-pointed at the prefixed root and a runnable smallapp."""
    line = next(
        raw for raw in unit_file.read_text().splitlines() if raw.startswith("ExecStart=")
    ).removeprefix("ExecStart=")
    command = shlex.split(line)
    prefixed = [
        part.replace("/opt/smallapp/", f"{root}/opt/smallapp/")
        if "/opt/smallapp/" in part
        else part
        for part in command
    ]
    return prefixed


@contextmanager
def running(command: list[str], env: dict[str, str], port: int) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(  # noqa: S603
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        wait_for_port(port, process)
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            process.kill()
            process.wait(timeout=10)


def wait_for_port(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.communicate()[1]
            raise AssertionError(f"process exited early ({process.returncode}): {output}")
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"nothing listening on 127.0.0.1:{port} after {STARTUP_TIMEOUT}s")


def request(
    port: int,
    method: str,
    path: str,
    cookie: str | None = None,
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    body = b""
    if cookie is not None:
        headers["Cookie"] = cookie
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(body))
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def apply_unit_cli(source: Path, name: str, root: Path) -> str:
    result = run_cli(
        "apply",
        str(source),
        "--name",
        name,
        "--domain",
        f"{name}.example.com",
        "--tls",
        "internal",
        "--root",
        str(root),
    )
    assert result.returncode == 0, result.stderr
    token_line = next(line for line in result.stdout.splitlines() if line.startswith("login token"))
    return token_line.split(": ", 1)[1].strip()


def gateway_env(root: Path, name: str, app_port: int, gw_port: int) -> dict[str, str]:
    values = read_env(root / f"etc/smallapp/{name}.env")
    values["PORT"] = str(app_port)
    values["SMALLAPP_GW_PORT"] = str(gw_port)
    values["SMALLAPP_UPSTREAM"] = f"127.0.0.1:{app_port}"
    # The rendered unit runs `/usr/bin/env smallapp gateway`, so smallapp must be on
    # PATH exactly as it is on a host that installed it.
    values["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    )
    values["HOME"] = str(root)
    return values


def walk_login_path(gw_port: int, token: str, expected_body: bytes) -> None:
    status, headers, _ = request(gw_port, "GET", "/")
    assert status == 401
    assert headers["Location"] == "/_smallapp/login"

    status, headers, _ = request(gw_port, "POST", "/_smallapp/login", form={"token": token})
    assert status == 303
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"].split(";")[0]
    assert cookie.startswith(f"{COOKIE_NAME}=")

    status, _, body = request(gw_port, "GET", "/", cookie=cookie)
    assert status == 200
    assert body == expected_body

    status, headers, _ = request(gw_port, "POST", "/_smallapp/logout", cookie=cookie)
    assert status == 303
    expired = headers["Set-Cookie"].split(";")[0]
    assert expired == f"{COOKIE_NAME}="

    status, headers, _ = request(gw_port, "GET", "/", cookie=expired)
    assert status == 401
    assert headers["Location"] == "/_smallapp/login"


@pytest.mark.slow
def test_python_unit_end_to_end(tmp_path: Path, app_file: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    token = apply_unit_cli(app_file, "expenses", root)

    app_port, gw_port = free_port(), free_port()
    env = gateway_env(root, "expenses", app_port, gw_port)
    app_cmd = exec_start(root / "etc/systemd/system/smallapp-expenses.service", root)
    gw_cmd = exec_start(root / "etc/systemd/system/smallapp-expenses-gw.service", root)

    with running(app_cmd, env, app_port), running(gw_cmd, env, gw_port):
        walk_login_path(gw_port, token, b"hello")


@pytest.mark.slow
def test_static_unit_end_to_end(tmp_path: Path, static_dir: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    token = apply_unit_cli(static_dir, "notes", root)

    vhost = (root / "etc/caddy/smallapp.d/notes.caddy").read_text()
    assert "file_server" in vhost
    assert f"root * {'/opt/smallapp/notes'}" in vhost
    assert "tls internal" in vhost

    app_port, gw_port = free_port(), free_port()
    env = gateway_env(root, "notes", app_port, gw_port)
    app_cmd = exec_start(root / "etc/systemd/system/smallapp-notes.service", root)
    gw_cmd = exec_start(root / "etc/systemd/system/smallapp-notes-gw.service", root)

    with running(app_cmd, env, app_port), running(gw_cmd, env, gw_port):
        walk_login_path(gw_port, token, b"<h1>hello</h1>\n")


@pytest.mark.slow
def test_wrong_token_never_opens_the_app(tmp_path: Path, app_file: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    apply_unit_cli(app_file, "expenses", root)

    app_port, gw_port = free_port(), free_port()
    env = gateway_env(root, "expenses", app_port, gw_port)
    app_cmd = exec_start(root / "etc/systemd/system/smallapp-expenses.service", root)
    gw_cmd = exec_start(root / "etc/systemd/system/smallapp-expenses-gw.service", root)

    with running(app_cmd, env, app_port), running(gw_cmd, env, gw_port):
        status, headers, _ = request(
            gw_port, "POST", "/_smallapp/login", form={"token": "not-the-token"}
        )
        assert status == 401
        assert "Set-Cookie" not in headers
        status, _, body = request(gw_port, "GET", "/")
        assert status == 401
        assert b"hello" not in body
