"""Pure rendering: (Target, Unit, secrets) -> {absolute path: RenderedFile}.

Pure means pure — no clock, no randomness, no filesystem writes. It reads the target's
payload bytes and returns text. Everything that varies is an argument, which is what
makes the golden test meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import templates
from .naming import Unit, validate_domain, validate_name
from .target import Target

APP_MEMORY_MAX = "512M"
GW_MEMORY_MAX = "128M"
PYTHON_ENTRY = "app.py"
PEP723_MARKER = "# /// script"

MODE_FILE = 0o644
MODE_SECRET = 0o600


@dataclass(frozen=True)
class RenderedFile:
    content: bytes
    mode: int

    @property
    def text(self) -> str:
        return self.content.decode()


def exec_start(target: Target, unit: Unit) -> str:
    """The ExecStart line for the app service.

    ponytail: a PEP 723 header is the only signal that the app needs third-party
    packages, so only then is `uv` involved. Dependency-free scripts run under plain
    python3, which is why the end-to-end test needs no network.
    """
    app_dir = unit.app_dir
    if target.kind == "static":
        return (
            '/bin/sh -c \'exec python3 -m http.server "$PORT" '
            f"--bind 127.0.0.1 --directory {app_dir}'"
        )
    entry = app_dir / PYTHON_ENTRY
    source = target.files[0].read_text(encoding="utf-8")
    if PEP723_MARKER in source:
        return f"/usr/bin/env uv run --script {entry}"
    return f"/usr/bin/env python3 {entry}"


def gw_exec_start() -> str:
    return "/usr/bin/env smallapp gateway"


def render(target: Target, unit: Unit, secret: str, token_hash: str) -> dict[str, RenderedFile]:
    """Every file the unit consists of, keyed by its final absolute path."""
    validate_name(unit.name)
    validate_domain(unit.domain)
    if target.kind != unit.kind:
        raise ValueError(f"unit kind {unit.kind!r} does not match target kind {target.kind!r}")
    for label, value in (("secret", secret), ("token hash", token_hash)):
        if not value or any(c in value for c in "\n\r "):
            raise ValueError(f"{label} is empty or contains whitespace")

    files: dict[str, RenderedFile] = {}
    files.update(_payload(target, unit))
    files[str(unit.env_path)] = RenderedFile(
        templates.ENV_FILE.format(
            name=unit.name,
            secret=secret,
            token_hash=token_hash,
            gw_port=unit.gw_port,
            port=unit.port,
        ).encode(),
        MODE_SECRET,
    )
    files[str(unit.service_path)] = RenderedFile(
        templates.APP_UNIT.format(
            name=unit.name,
            kind=unit.kind,
            user=unit.user,
            app_dir=unit.app_dir,
            port=unit.port,
            exec_start=exec_start(target, unit),
            memory_max=APP_MEMORY_MAX,
            hardening=templates.HARDENING,
        ).encode(),
        MODE_FILE,
    )
    files[str(unit.gw_service_path)] = RenderedFile(
        templates.GW_UNIT.format(
            name=unit.name,
            user=unit.user,
            app_dir=unit.app_dir,
            env_path=unit.env_path,
            gw_exec_start=gw_exec_start(),
            gw_memory_max=GW_MEMORY_MAX,
            hardening=templates.HARDENING,
        ).encode(),
        MODE_FILE,
    )
    files[str(unit.vhost_path)] = RenderedFile(_vhost(unit).encode(), MODE_FILE)
    return files


def _payload(target: Target, unit: Unit) -> dict[str, RenderedFile]:
    if target.kind == "python":
        entry = target.files[0]
        return {
            str(unit.app_dir / PYTHON_ENTRY): RenderedFile(entry.read_bytes(), MODE_FILE),
        }
    payload: dict[str, RenderedFile] = {}
    for file in target.files:
        relative = file.relative_to(target.root)
        payload[str(unit.app_dir / relative.as_posix())] = RenderedFile(
            file.read_bytes(), MODE_FILE
        )
    return payload


def _vhost(unit: Unit) -> str:
    head = templates.VHOST_HEAD.format(
        name=unit.name,
        domain=unit.domain,
        gw_port=unit.gw_port,
        tls_line=templates.TLS_INTERNAL if unit.tls == "internal" else "",
    )
    if unit.kind == "static":
        return head + templates.VHOST_STATIC_TAIL.format(app_dir=unit.app_dir)
    return head + templates.VHOST_PROXY_TAIL.format(port=unit.port)
