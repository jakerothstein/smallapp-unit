"""Ordered actions: what the host needs done to match the rendered artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .naming import Unit, ValidationError
from .render import (
    MODE_APP_DIR,
    MODE_STATE_DIR,
    PYTHON_ENTRY,
    RenderedFile,
    render,
    uv_cache_dir,
)
from .system import CADDY_USER, System
from .target import Target, declares_dependencies
from .tokens import generate_secret, generate_token, hash_token

Verb = Literal[
    "mkdir",
    "write",
    "chown",
    "chgrp",
    "chmod",
    "user",
    "group",
    "deps",
    "systemctl",
    "caddy_reload",
    "rm",
]
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


def resolve_secrets(system: System, unit: Unit, known: Unit | None = None) -> Secrets:
    """Reuse the secrets already on disk, so a second apply changes nothing.

    Only a *finished* apply may reuse them. An interrupted first apply leaves an env
    file whose plaintext token was never printed; reusing that hash would lock the
    owner out of their own app with no way back except deleting the file by hand. So
    unless the registry says the unit is complete, both secrets are generated afresh
    and the retry prints a token that works.
    """
    if known is not None and known.complete:
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


def dir_state(system: System, absolute: str, mode: int) -> State:
    """A directory is unchanged only when it exists *and* already carries `mode`."""
    if not system.path(absolute).is_dir():
        return "create"
    return "unchanged" if system.mode_of(absolute) == mode else "change"


def build(
    system: System, target: Target, unit: Unit, secrets: Secrets, known: Unit | None = None
) -> list[Action]:
    """The full ordered plan for one unit against the current state of `system`.

    `known` is the unit's registry entry, or None if it has never been applied. It is
    what tells an adopted unix user from one smallapp made, and an interrupted apply
    from a finished one.
    """
    files = render(target, unit, secrets.secret, secrets.token_hash)
    actions: list[Action] = []
    for user in (unit.user, unit.gw_user):
        exists = system.user_exists(user)
        if exists and known is None:
            raise ValidationError(
                f"unix user {user!r} already exists but smallapp did not create it. "
                f"Refusing to adopt an account whose shell, home and uid are unknown "
                f"— remove it, or deploy under a different --name."
            )
        actions.append(Action("user", user, {}, "unchanged" if exists else "create"))
    directories = (
        (str(unit.app_dir), MODE_APP_DIR),
        (str(unit.state_dir), MODE_STATE_DIR),
        (str(unit.gw_state_dir), MODE_STATE_DIR),
    )
    for directory, mode in directories:
        actions.append(
            Action("mkdir", directory, {"mode": f"0{mode:o}"}, dir_state(system, directory, mode))
        )
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
    for stale in stale_payload(system, unit, files):
        actions.append(Action("rm", stale, {}, "change"))
    actions.extend(_dependency_actions(system, target, unit, actions))
    actions.append(
        Action(
            "chgrp",
            str(unit.app_dir),
            {"group": unit.user},
            "unchanged" if system.path(str(unit.app_dir)).is_dir() else "create",
        )
    )
    if unit.kind == "static":
        # Caddy serves these bytes itself, so it — and only it — joins the unit's group.
        member = CADDY_USER in system.group_members(unit.user)
        actions.append(
            Action(
                "group",
                unit.user,
                {"user": CADDY_USER},
                "unchanged" if member else "create",
            )
        )
    owned = ((str(unit.state_dir), unit.user), (str(unit.gw_state_dir), unit.gw_user))
    for directory, owner in owned:
        actions.append(
            Action(
                "chown",
                directory,
                {"user": owner},
                "unchanged" if system.path(directory).is_dir() else "create",
            )
        )
    changed = any(action.state != "unchanged" for action in actions)
    incomplete = known is None or not known.complete
    service_state: State = "create" if changed or incomplete else "unchanged"
    actions.append(Action("systemctl", "daemon-reload", {"verb": "daemon-reload"}, service_state))
    for service in (unit.service_path.name, unit.gw_service_path.name):
        actions.append(Action("systemctl", service, {"verb": "enable --now"}, service_state))
    actions.append(Action("caddy_reload", "caddy", {}, service_state))
    return actions


def _dependency_actions(
    system: System, target: Target, unit: Unit, so_far: list[Action]
) -> list[Action]:
    """Pre-build a PEP 723 script's environment, because the unit gets no network.

    Re-run whenever the payload changed or the cache is missing; `uv sync` is itself
    idempotent, so the only cost of being wrong is a redundant resolve.
    """
    if target.kind != "python" or not declares_dependencies(
        target.files[0].read_text(encoding="utf-8")
    ):
        return []
    cache = uv_cache_dir(unit)
    entry = str(unit.app_dir / PYTHON_ENTRY)
    payload_changed = any(
        action.target == entry and action.state != "unchanged" for action in so_far
    )
    fresh = system.path(cache).is_dir() and not payload_changed
    return [Action("deps", entry, {"cache": cache}, "unchanged" if fresh else "create")]


def stale_payload(system: System, unit: Unit, files: dict[str, RenderedFile]) -> list[str]:
    """Deployed payload files the new target no longer contains. Removing a file from
    a static site must un-deploy it, or yesterday's page stays on the internet."""
    return [path for path in system.list_files(str(unit.app_dir)) if path not in files]


def summarise(actions: list[Action]) -> str:
    unchanged = sum(1 for action in actions if action.state == "unchanged")
    if unchanged == len(actions):
        return f"{len(actions)} actions, {unchanged} unchanged.  no changes."
    return f"{len(actions)} actions, {unchanged} unchanged."
