"""Live gateway: every row of the HTTP contract, driven against a real server."""

from __future__ import annotations

import hmac
import http.client
import inspect
import socket
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


def test_login_really_reaches_compare_digest(gw: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """QA round 2 #9: source text proves nothing. Watch the real call happen."""
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    status, headers, _ = request(gw, "POST", "/_smallapp/login", form={"token": "wrong"})
    assert status == 401
    assert "Set-Cookie" not in headers
    assert calls, "a wrong-token login did not reach hmac.compare_digest"

    calls.clear()
    status, _, _ = request(gw, "POST", "/_smallapp/login", form={"token": TOKEN})
    assert status == 303
    assert calls, "a correct-token login did not reach hmac.compare_digest"


def test_gateway_and_tokens_compare_secrets_constant_time() -> None:
    assert "compare_digest" in inspect.getsource(tokens)
    assert "==" not in inspect.getsource(gateway.Handler._login)


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE"])
def test_logout_outside_the_contract_is_405_and_touches_no_cookie(gw: int, method: str) -> None:
    """QA round 3 #15: only POST /_smallapp/logout is in the HTTP contract."""
    cookie = sign_cookie(SECRET)
    status, headers, _ = request(gw, method, "/_smallapp/logout", cookie=cookie)
    assert status == 405
    assert headers["Allow"] == "POST"
    assert "Set-Cookie" not in headers
    # The session survives a method that was refused.
    assert request(gw, "GET", "/_smallapp/auth", cookie=cookie)[0] == 204


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("POST", "/_smallapp/login", b"token=" + b"a" * 9000, 401),  # oversized login
        ("POST", "/_smallapp/login", b"token=wrong", 401),  # wrong token
        ("GET", "/_smallapp/auth", b"", 401),  # auth challenge
        ("GET", "/_smallapp/logout", b"x" * 200, 405),  # method not allowed
        ("POST", "/_smallapp/nope", b"y" * 200, 404),  # unknown gateway route
        ("POST", "/app", b"z" * 200, 401),  # unauthorised proxy, body never forwarded
    ],
)
def test_a_rejected_request_cannot_bleed_into_the_next_one(
    gw: int, method: str, path: str, body: bytes, expected: int
) -> None:
    """QA round 8 #1: an undrained body on a keep-alive connection is parsed as the
    next request. Every rejection must consume its body or close the connection."""
    conn = http.client.HTTPConnection("127.0.0.1", gw, timeout=10)
    try:
        conn.request(
            method, path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        first = conn.getresponse()
        first.read()
        assert first.status == expected
        conn.request("GET", "/_smallapp/login")
        second = conn.getresponse()
        page = second.read()
        assert second.status == 200, f"{path} left {len(body)} bytes on the connection"
        assert b'name="token"' in page
    finally:
        conn.close()


def test_an_unreadable_body_is_refused_and_the_connection_closed(gw: int) -> None:
    """A body the gateway cannot frame exactly (bad length, chunked) cannot be drained,
    so the only safe answer is to refuse and hang up."""
    for header in (b"Content-Length: not-a-number", b"Transfer-Encoding: chunked"):
        sock = socket.create_connection(("127.0.0.1", gw), timeout=10)
        try:
            sock.sendall(b"POST /app HTTP/1.1\r\nHost: x\r\n" + header + b"\r\n\r\n")
            seen = b""
            while chunk := sock.recv(4096):
                seen += chunk
            assert seen.startswith(b"HTTP/1.1 413 "), seen[:60]
        finally:
            sock.close()
