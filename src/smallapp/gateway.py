"""The signed-cookie auth gateway: `smallapp gateway`, run by the generated -gw unit.

Serves `/_smallapp/*` itself (Caddy `forward_auth` points at `/_smallapp/auth`) and
reverse-proxies every other path to the app once the cookie checks out. Proxying is
what lets the end-to-end test exercise the real login path with no Caddy present, and
it is harmless in production because Caddy hands it only what it already authorised.
"""

from __future__ import annotations

import html
import http.client
import os
import sys
import urllib.parse
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseServer
from typing import Any

from .tokens import COOKIE_NAME, DEFAULT_TTL, sign_cookie, verify_cookie, verify_token

PREFIX = "/_smallapp"
AUTH_PATH = f"{PREFIX}/auth"
LOGIN_PATH = f"{PREFIX}/login"
LOGOUT_PATH = f"{PREFIX}/logout"
USER_HEADER = "X-Smallapp-User"

DEFAULT_GW_PORT = 19000
DEFAULT_UPSTREAM = "127.0.0.1:18000"
MAX_LOGIN_BODY = 8192
MAX_PROXY_BODY = 32 * 1024 * 1024
UPSTREAM_TIMEOUT = 30.0

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
 body{{font:16px system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh}}
 form{{display:grid;gap:.75rem;width:min(22rem,90vw)}}
 input,button{{font:inherit;padding:.6rem;border:1px solid #999;border-radius:.4rem}}
 button{{background:#111;color:#fff;border-color:#111;cursor:pointer}}
 .err{{color:#b00020}}
</style></head><body>
<form method="post" action="{login}">
  <label for="token">Login token</label>
  <input id="token" name="token" type="password" autocomplete="current-password" autofocus>
  <button type="submit">Sign in</button>
  {error}
</form>
</body></html>
"""


class ConfigError(Exception):
    """Raised when the environment cannot configure a gateway."""


@dataclass(frozen=True)
class Config:
    secret: str
    token_hash: str
    port: int
    upstream_host: str
    upstream_port: int

    @property
    def upstream(self) -> str:
        return f"{self.upstream_host}:{self.upstream_port}"


def config_from_env(env: dict[str, str] | None = None) -> Config:
    """Build a Config or raise ConfigError naming the offending variable."""
    env = dict(os.environ if env is None else env)
    secret = env.get("SMALLAPP_SECRET", "")
    if not secret:
        raise ConfigError("SMALLAPP_SECRET is not set; the gateway will not invent one")
    token_hash = env.get("SMALLAPP_TOKEN_HASH", "")
    if not token_hash:
        raise ConfigError("SMALLAPP_TOKEN_HASH is not set; the gateway will not invent one")
    port = _port(env.get("SMALLAPP_GW_PORT"), DEFAULT_GW_PORT, "SMALLAPP_GW_PORT")
    host, _, raw_port = env.get("SMALLAPP_UPSTREAM", DEFAULT_UPSTREAM).rpartition(":")
    if not host:
        raise ConfigError("SMALLAPP_UPSTREAM must look like host:port, e.g. 127.0.0.1:18000")
    return Config(
        secret=secret,
        token_hash=token_hash,
        port=port,
        upstream_host=host,
        upstream_port=_port(raw_port, 18000, "SMALLAPP_UPSTREAM"),
    )


def _port(raw: str | None, default: int, var: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{var}={raw!r} is not a number") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"{var}={raw!r} is outside 1..65535")
    return port


class Handler(BaseHTTPRequestHandler):
    server_version = "smallapp"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    config: Config
    body: bytes = b""

    def do_GET(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def _route(self) -> None:
        # Drain the request body once, up front. Every branch below answers without
        # reading it, and an undrained body on a keep-alive connection would be parsed
        # as the *next* request.
        body = self._read_body()
        if body is None:
            self.close_connection = True
            self._send(413, b"request too large", "text/plain; charset=utf-8")
            return
        self.body = body
        path = urllib.parse.urlsplit(self.path).path
        if path == AUTH_PATH:
            self._auth()
        elif path == LOGIN_PATH:
            if self.command == "POST":
                self._login()
            else:
                self._login_form(200)
        elif path == LOGOUT_PATH:
            if self.command == "POST":
                self._logout()
            else:
                self._method_not_allowed("POST")
        elif path.startswith(f"{PREFIX}/"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
        else:
            self._proxy()

    def _subject(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar: SimpleCookie = SimpleCookie()
            jar.load(raw)
        except CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        if morsel is None:
            return None
        return verify_cookie(self.config.secret, morsel.value)

    def _auth(self) -> None:
        sub = self._subject()
        if sub is None:
            self._challenge()
            return
        self.send_response(204)
        self.send_header(USER_HEADER, sub)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _challenge(self) -> None:
        body = b"unauthorized"
        self.send_response(401)
        self.send_header("Location", LOGIN_PATH)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _login_form(self, status: int, error: str = "") -> None:
        markup = f'<p class="err">{html.escape(error)}</p>' if error else ""
        page = LOGIN_HTML.format(login=LOGIN_PATH, error=markup).encode()
        self._send(status, page, "text/html; charset=utf-8")

    def _login(self) -> None:
        if len(self.body) > MAX_LOGIN_BODY:
            self._login_form(401, "Invalid request.")
            return
        fields = urllib.parse.parse_qs(self.body.decode("utf-8", "replace"))
        token = (fields.get("token") or [""])[0]
        if not token or not verify_token(token, self.config.token_hash):
            self._login_form(401, "That token is not right.")
            return
        cookie = sign_cookie(self.config.secret, sub="owner", ttl=DEFAULT_TTL)
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={cookie}; HttpOnly; Secure; SameSite=Lax; Path=/; "
            f"Max-Age={DEFAULT_TTL}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _logout(self) -> None:
        self.send_response(303)
        self.send_header("Location", LOGIN_PATH)
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _method_not_allowed(self, allowed: str) -> None:
        """Outside the documented contract: refuse, and touch no cookie doing it."""
        body = f"method not allowed; use {allowed}".encode()
        self.send_response(405)
        self.send_header("Allow", allowed)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _read_body(self) -> bytes | None:
        """The whole declared body, or None when it cannot be read exactly.

        Chunked bodies count as unreadable: this gateway speaks only Content-Length, and
        guessing at framing is how a connection ends up desynchronised.
        """
        if self.headers.get("Transfer-Encoding"):
            return None
        raw = self.headers.get("Content-Length", "0")
        try:
            length = int(raw)
        except ValueError:
            return None
        if not 0 <= length <= MAX_PROXY_BODY:
            return None
        body = self.rfile.read(length) if length else b""
        return body if len(body) == length else None

    def _proxy(self) -> None:
        if self._subject() is None:
            self._challenge()
            return
        body = self.body
        headers = {
            key: value for key, value in self.headers.items() if key.lower() not in HOP_BY_HOP
        }
        headers["Host"] = self.config.upstream
        headers["X-Forwarded-Proto"] = "https"
        headers[USER_HEADER] = "owner"
        conn = http.client.HTTPConnection(
            self.config.upstream_host, self.config.upstream_port, timeout=UPSTREAM_TIMEOUT
        )
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._write(payload)
        except OSError as exc:
            self._send(502, f"upstream unreachable: {exc}".encode(), "text/plain; charset=utf-8")
        finally:
            conn.close()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _write(self, body: bytes) -> None:
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(f"gateway {self.address_string()} {format % args}\n")


def make_server(config: Config, port: int | None = None) -> ThreadingHTTPServer:
    """A gateway bound to 127.0.0.1 only. `port=0` picks an ephemeral one, for tests."""
    handler = type("BoundHandler", (Handler,), {"config": config})
    server = ThreadingHTTPServer(("127.0.0.1", config.port if port is None else port), handler)
    server.daemon_threads = True
    return server


def serve(server: BaseServer) -> None:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    try:
        config = config_from_env()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    server = make_server(config)
    print(
        f"gateway on 127.0.0.1:{config.port} -> {config.upstream}",
        file=sys.stderr,
        flush=True,
    )
    serve(server)
    return 0
