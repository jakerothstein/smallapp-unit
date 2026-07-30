"""Execute a plan, and reverse one. Every OS call goes through the System seam."""

from __future__ import annotations

from dataclasses import replace

from . import registry
from .naming import Unit
from .plan import Action, Secrets, build
from .render import render
from .system import StepError, System
from .target import Target


def apply_unit(
    system: System, target: Target, unit: Unit, secrets: Secrets
) -> tuple[list[Action], str | None]:
    """Create or update the unit. Returns the executed actions and the one-time token.

    Raises StepError naming the failing step; anything already written stays put so the
    host can be inspected. The registry entry is written *before* the side effects with
    `complete=False`, so a retry after a failed reload redoes the reload instead of
    reporting a success that never happened.
    """
    files = render(target, unit, secrets.secret, secrets.token_hash)
    known = registry.load(system).get(unit.name)
    actions = build(system, target, unit, secrets, known)
    changed = any(action.state != "unchanged" for action in actions)
    created_user = (known.created_user if known else False) or any(
        action.verb == "user" and action.state == "create" for action in actions
    )
    if not changed and known is not None and known.complete:
        return actions, secrets.token
    registry.put(system, replace(unit, created_user=created_user, complete=False))
    for action in actions:
        if action.state == "unchanged":
            continue
        if action.verb == "user":
            system.create_user(action.target)
        elif action.verb == "mkdir":
            system.mkdir(action.target, int(action.detail["mode"], 8))
        elif action.verb == "write":
            rendered = files[action.target]
            system.write(action.target, rendered.content, rendered.mode)
        elif action.verb == "rm":
            system.remove(action.target)
        elif action.verb == "deps":
            system.uv_sync_script(action.target, action.detail["cache"], action.detail["marker"])
        elif action.verb == "chown":
            system.chown(action.target, action.detail["user"])
        elif action.verb == "chgrp":
            system.chgrp(action.target, action.detail["group"])
        elif action.verb == "group":
            if action.detail.get("op") == "remove":
                system.remove_group_member(action.target, action.detail["user"])
            else:
                system.add_group_member(action.target, action.detail["user"])
            # Supplementary groups are read once, at process start: Caddy has to be
            # restarted, not reloaded, before the change takes effect.
            system.systemctl("restart", "caddy")
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
    registry.put(system, replace(unit, created_user=created_user, complete=True))
    system.flush_created()
    return actions, secrets.token


def remove_unit(system: System, name: str) -> list[Action] | None:
    """Undo `apply_unit`. Returns None when the unit is unknown."""
    units = registry.load(system)
    unit = units.get(name)
    if unit is None:
        # Taking the lock may have created the state directory; give back only what
        # smallapp itself made, and leave every pre-existing directory alone.
        system.prune_created()
        return None
    actions: list[Action] = []
    for service in (unit.service_path.name, unit.gw_service_path.name):
        system.systemctl("disable", "--now", service)
        actions.append(Action("systemctl", service, {"verb": "disable --now"}, "create"))
    for path in (
        str(unit.app_dir),
        str(unit.state_dir),
        str(unit.gw_state_dir),
        str(unit.env_path),
        str(unit.service_path),
        str(unit.gw_service_path),
        str(unit.vhost_path),
    ):
        system.remove(path)
        actions.append(Action("rm", path, {}, "create"))
    if unit.created_user:
        # Raises on an unexpected failure, before the registry entry is dropped, so the
        # unit can be removed again once whatever holds the account is gone. Deleting
        # `sa-NAME` takes its group with it, and with it Caddy's membership of it.
        for user in (unit.user, unit.gw_user):
            system.delete_user(user)
            actions.append(Action("rm", user, {"kind": "user"}, "create"))
    # The entry is dropped last: until daemon-reload and Caddy have both accepted the
    # removal, `rm NAME` must stay repeatable rather than answer `not found`.
    system.systemctl("daemon-reload")
    system.caddy_reload()
    actions.append(Action("caddy_reload", "caddy", {}, "create"))
    registry.drop(system, name)
    system.prune_created()
    return actions
