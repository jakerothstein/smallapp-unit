"""Live gateway: every row of the HTTP contract, driven against a real server."""

from __future__ import annotations

import http.client
import inspect
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from smallapp import gateway, tokens
from smallapp.gateway import Config, ConfigError, config_from_env, make_server
from smallapp.tokens import COOKIE_NAME, hash_token, sign_cookie

TOKEN = "correct-horse-battery"
SECRET = "unit-test-secret"


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = f"hello {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(201)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@pytest.fixture
def upstream() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def gw(upstream: int) -> Iterator[int]:
    config = Config(
        secret=SECRET,
        token_hash=hash_token(TOKEN),
        port=0,
        upstream_host="127.0.0.1",
        upstream_port=upstream,
    )
    server = make_server(config, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
        headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
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


def test_auth_with_valid_cookie_is_204(gw: int) -> None:
    status, headers, _ = request(gw, "GET", "/_smallapp/auth", cookie=sign_cookie(SECRET))
    assert status == 204
    assert headers["X-Smallapp-User"] == "owner"


@pytest.mark.parametrize(
    "cookie",
    [None, "garbage", sign_cookie(SECRET, ttl=-10), sign_cookie("other-secret")],
)
def test_auth_without_good_cookie_is_401_with_login_location(gw: int, cookie: str | None) -> None:
    status, headers, _ = request(gw, "GET", "/_smallapp/auth", cookie=cookie)
    assert status == 401
    assert headers["Location"] == "/_smallapp/login"


def test_login_form_is_200_html(gw: int) -> None:
    status, headers, body = request(gw, "GET", "/_smallapp/login")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b'name="token"' in body


def test_login_with_correct_token_sets_cookie_and_redirects(gw: int) -> None:
    status, headers, _ = request(gw, "POST", "/_smallapp/login", form={"token": TOKEN})
    assert status == 303
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=v1.")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/" in cookie


def test_login_with_wrong_token_is_401_with_no_cookie(gw: int) -> None:
    status, headers, body = request(gw, "POST", "/_smallapp/login", form={"token": "nope"})
    assert status == 401
    assert "Set-Cookie" not in headers
    assert b'name="token"' in body
    assert b"not right" in body


def test_twenty_wrong_attempts_still_401(gw: int) -> None:
    for _ in range(20):
        status, headers, _ = request(gw, "POST", "/_smallapp/login", form={"token": "nope"})
        assert status == 401
        assert "Set-Cookie" not in headers


def test_logout_redirects_and_expires_the_cookie(gw: int) -> None:
    status, headers, _ = request(gw, "POST", "/_smallapp/logout", cookie=sign_cookie(SECRET))
    assert status == 303
    assert headers["Location"] == "/_smallapp/login"
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert f"{COOKIE_NAME}=;" in headers["Set-Cookie"]


def test_login_error_message_is_escaped_not_injected(gw: int) -> None:
    _, _, body = request(gw, "POST", "/_smallapp/login", form={"token": "<script>x</script>"})
    assert b"<script>" not in body


def test_proxies_to_upstream_only_when_authorised(gw: int) -> None:
    status, headers, _ = request(gw, "GET", "/notes")
    assert status == 401
    assert headers["Location"] == "/_smallapp/login"

    status, _, body = request(gw, "GET", "/notes", cookie=sign_cookie(SECRET))
    assert status == 200
    assert body == b"hello /notes"

    status, _, body = request(gw, "POST", "/notes", cookie=sign_cookie(SECRET), form={"a": "b"})
    assert status == 201
    assert body == b"a=b"


def test_unknown_smallapp_path_is_404(gw: int) -> None:
    status, _, _ = request(gw, "GET", "/_smallapp/nope")
    assert status == 404


def test_bad_upstream_reports_502() -> None:
    config = Config(
        secret=SECRET,
        token_hash=hash_token(TOKEN),
        port=0,
        upstream_host="127.0.0.1",
        upstream_port=1,
    )
    server = make_server(config, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(
            int(server.server_address[1]), "GET", "/", cookie=sign_cookie(SECRET)
        )
        assert status == 502
        assert b"upstream" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_config_defaults() -> None:
    config = config_from_env({"SMALLAPP_SECRET": "s", "SMALLAPP_TOKEN_HASH": "h"})
    assert config.port == 19000
    assert config.upstream == "127.0.0.1:18000"


def test_config_rejects_bad_ports() -> None:
    base = {"SMALLAPP_SECRET": "s", "SMALLAPP_TOKEN_HASH": "h"}
    with pytest.raises(ConfigError, match="SMALLAPP_GW_PORT"):
        config_from_env({**base, "SMALLAPP_GW_PORT": "eighty"})
    with pytest.raises(ConfigError, match="SMALLAPP_GW_PORT"):
        config_from_env({**base, "SMALLAPP_GW_PORT": "70000"})
    with pytest.raises(ConfigError, match="SMALLAPP_UPSTREAM"):
        config_from_env({**base, "SMALLAPP_UPSTREAM": "no-colon-here"})


@pytest.mark.parametrize("missing", ["SMALLAPP_SECRET", "SMALLAPP_TOKEN_HASH"])
def test_gateway_exits_fast_naming_the_missing_var(missing: str) -> None:
    env = {
        "SMALLAPP_SECRET": SECRET,
        "SMALLAPP_TOKEN_HASH": hash_token(TOKEN),
        "PATH": "/usr/bin:/bin",
    }
    env.pop(missing)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "smallapp", "gateway"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert missing in result.stderr
    assert time.monotonic() - started < 2.0


def test_gateway_and_tokens_compare_secrets_constant_time() -> None:
    for module in (gateway, tokens):
        source = inspect.getsource(module)
        assert "hmac.compare_digest" in source or "verify_token" in source
    assert "compare_digest" in inspect.getsource(tokens)
    assert "==" not in inspect.getsource(gateway.Handler._login)
