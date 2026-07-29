"""Ordered actions: what the host needs done to match the rendered artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .naming import Unit
from .render import RenderedFile, render
from .system import System
from .target import Target
from .tokens import generate_secret, generate_token, hash_token

Verb = Literal["mkdir", "write", "chown", "chmod", "user", "systemctl", "caddy_reload", "rm"]
State = Literal["create", "change", "unchanged"]

MARK = {"create": "+", "change": "~", "unchanged": "="}


@dataclass(frozen=True)
class Action:
    verb: Verb
    target: str
    detail: dict[str, str] = field(default_factory=dict)
    state: State = "create"

    def line(self) -> str:
        note = f"  ({self.detail['mode']})" if "mode" in self.detail else ""
        return f"  {MARK[self.state]} {self.verb:<12} {self.target}{note}"


@dataclass(frozen=True)
class Secrets:
    secret: str
    token_hash: str
    token: str | None  # plaintext, present only when freshly generated


def resolve_secrets(system: System, unit: Unit) -> Secrets:
    """Reuse the secrets already on disk, so a second apply changes nothing."""
    raw = system.read(str(unit.env_path))
    if raw is not None:
        values = _parse_env(raw)
        secret = values.get("SMALLAPP_SECRET", "")
        token_hash = values.get("SMALLAPP_TOKEN_HASH", "")
        if secret and token_hash:
            return Secrets(secret=secret, token_hash=token_hash, token=None)
    token = generate_token()
    return Secrets(secret=generate_secret(), token_hash=hash_token(token), token=token)


def _parse_env(raw: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def file_state(system: System, path: str, rendered: RenderedFile) -> State:
    current = system.read(path)
    if current is None:
        return "create"
    if current == rendered.content and system.mode_of(path) == rendered.mode:
        return "unchanged"
    return "change"


def build(system: System, target: Target, unit: Unit, secrets: Secrets) -> list[Action]:
    """The full ordered plan for one unit against the current state of `system`."""
    files = render(target, unit, secrets.secret, secrets.token_hash)
    actions: list[Action] = [
        Action(
            "user",
            unit.user,
            {},
            "unchanged" if system.user_exists(unit.user) else "create",
        )
    ]
    for directory in (str(unit.app_dir), str(unit.state_dir)):
        exists = system.path(directory).is_dir()
        actions.append(Action("mkdir", directory, {}, "unchanged" if exists else "create"))
    for path in sorted(files):
        rendered = files[path]
        actions.append(
            Action(
                "write",
                path,
                {"mode": f"0{rendered.mode:o}"},
                file_state(system, path, rendered),
            )
        )
    actions.append(
        Action(
            "chown",
            str(unit.state_dir),
            {"user": unit.user},
            "unchanged" if system.path(str(unit.state_dir)).is_dir() else "create",
        )
    )
    changed = any(action.state != "unchanged" for action in actions)
    service_state: State = "create" if changed else "unchanged"
    actions.append(Action("systemctl", "daemon-reload", {"verb": "daemon-reload"}, service_state))
    for service in (unit.service_path.name, unit.gw_service_path.name):
        actions.append(Action("systemctl", service, {"verb": "enable --now"}, service_state))
    actions.append(Action("caddy_reload", "caddy", {}, service_state))
    return actions


def summarise(actions: list[Action]) -> str:
    unchanged = sum(1 for action in actions if action.state == "unchanged")
    if unchanged == len(actions):
        return f"{len(actions)} actions, {unchanged} unchanged.  no changes."
    return f"{len(actions)} actions, {unchanged} unchanged."
