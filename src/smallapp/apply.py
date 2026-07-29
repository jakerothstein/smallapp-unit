"""Execute a plan, and reverse one. Every OS call goes through the System seam."""

from __future__ import annotations

from . import registry
from .naming import CADDY_DIR, ENV_DIR, OPT_DIR, STATE_DIR, UNIT_DIR, Unit
from .plan import Action, Secrets, build
from .render import render
from .system import StepError, System
from .target import Target

PRUNE_DIRS = (
    str(OPT_DIR),
    str(STATE_DIR / "users"),
    str(STATE_DIR),
    str(ENV_DIR),
    str(CADDY_DIR),
    str(UNIT_DIR),
)


def apply_unit(
    system: System, target: Target, unit: Unit, secrets: Secrets
) -> tuple[list[Action], str | None]:
    """Create or update the unit. Returns the executed actions and the one-time token.

    Raises StepError naming the failing step; anything already written stays put so the
    host can be inspected.
    """
    files = render(target, unit, secrets.secret, secrets.token_hash)
    actions = build(system, target, unit, secrets)
    for action in actions:
        if action.state == "unchanged":
            continue
        if action.verb == "user":
            system.create_user(action.target)
        elif action.verb == "mkdir":
            system.mkdir(action.target)
        elif action.verb == "write":
            rendered = files[action.target]
            system.write(action.target, rendered.content, rendered.mode)
        elif action.verb == "chown":
            system.chown(action.target, action.detail["user"])
        elif action.verb == "systemctl":
            if action.target == "daemon-reload":
                system.systemctl("daemon-reload")
            else:
                system.systemctl("enable", "--now", action.target)
                system.systemctl("restart", action.target)
        elif action.verb == "caddy_reload":
            system.caddy_reload()
        else:  # pragma: no cover - Verb is closed; a new one must be handled above
            raise StepError(action.verb, f"unknown verb for {action.target}")
    registry.put(system, unit)
    return actions, secrets.token


def remove_unit(system: System, name: str) -> list[Action] | None:
    """Undo `apply_unit`. Returns None when the unit is unknown."""
    units = registry.load(system)
    unit = units.get(name)
    if unit is None:
        return None
    actions: list[Action] = []
    for service in (unit.service_path.name, unit.gw_service_path.name):
        system.systemctl("disable", "--now", service)
        actions.append(Action("systemctl", service, {"verb": "disable --now"}, "create"))
    for path in (
        str(unit.app_dir),
        str(unit.state_dir),
        str(unit.env_path),
        str(unit.service_path),
        str(unit.gw_service_path),
        str(unit.vhost_path),
    ):
        system.remove(path)
        actions.append(Action("rm", path, {}, "create"))
    system.delete_user(unit.user)
    actions.append(Action("rm", unit.user, {"kind": "user"}, "create"))
    registry.drop(system, name)
    system.systemctl("daemon-reload")
    system.caddy_reload()
    actions.append(Action("caddy_reload", "caddy", {}, "create"))
    for directory in PRUNE_DIRS:
        system.prune_empty(directory)
    return actions
