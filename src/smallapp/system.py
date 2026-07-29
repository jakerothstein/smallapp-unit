"""The only module allowed to touch the OS: files, users, systemctl, Caddy.

A `--root DIR` prefix makes every path land inside DIR and turns the privileged verbs
(useradd, systemctl, caddy) into recorded no-ops, which is how apply is testable on a
laptop. Everything above this module is pure and needs no root to exercise.
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

USERADD = "/usr/sbin/useradd"
SYSTEMCTL = "systemctl"
CADDY = "caddy"
USER_MARKER_DIR = "var/lib/smallapp/users"


class StepError(RuntimeError):
    """A privileged step failed. Carries the step name so apply can name it."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step


class System:
    """Filesystem and service operations, optionally confined to a prefix."""

    def __init__(self, root: str | Path = "/") -> None:
        self.root = Path(root).resolve()
        self.prefixed = self.root != Path("/")

    def path(self, absolute: str) -> Path:
        """Map an absolute artifact path into this system's root."""
        if not absolute.startswith("/"):
            raise ValueError(f"{absolute!r} is not an absolute path")
        return self.root / absolute.lstrip("/")

    def read(self, absolute: str) -> bytes | None:
        target = self.path(absolute)
        if not target.is_file():
            return None
        return target.read_bytes()

    def mode_of(self, absolute: str) -> int | None:
        target = self.path(absolute)
        if not target.exists():
            return None
        return target.stat().st_mode & 0o777

    def write(self, absolute: str, content: bytes, mode: int) -> None:
        target = self.path(absolute)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.smallapp-tmp")
        temporary.write_bytes(content)
        os.chmod(temporary, mode)
        os.replace(temporary, target)

    def mkdir(self, absolute: str, mode: int = 0o755) -> None:
        target = self.path(absolute)
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, mode)

    def remove(self, absolute: str) -> None:
        target = self.path(absolute)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()

    def prune_empty(self, absolute: str) -> None:
        """Remove `absolute` and its parents while they are empty, staying inside root."""
        target = self.path(absolute)
        while target != self.root and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
            target = target.parent

    def user_exists(self, user: str) -> bool:
        if self.prefixed:
            return (self.root / USER_MARKER_DIR / user).exists()
        try:
            pwd.getpwnam(user)
        except KeyError:
            return False
        return True

    def create_user(self, user: str) -> None:
        if self.prefixed:
            marker = self.root / USER_MARKER_DIR / user
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("created by smallapp --root\n")
            return
        self._run(
            f"create user {user}",
            [USERADD, "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", user],
        )

    def delete_user(self, user: str) -> None:
        if self.prefixed:
            marker = self.root / USER_MARKER_DIR / user
            if marker.exists():
                marker.unlink()
            return
        self._run(f"delete user {user}", ["/usr/sbin/userdel", user], allow_fail=True)

    def chown(self, absolute: str, user: str) -> None:
        """Give `absolute` to `user`. A no-op under a prefix, where there is no such uid."""
        if self.prefixed:
            return
        try:
            entry = pwd.getpwnam(user)
        except KeyError as exc:
            raise StepError(f"chown {absolute}", f"no such user {user}") from exc
        os.chown(self.path(absolute), entry.pw_uid, entry.pw_gid)

    def systemctl(self, *args: str) -> None:
        if self.prefixed:
            return
        self._run(f"systemctl {' '.join(args)}", [SYSTEMCTL, *args])

    def systemctl_output(self, *args: str) -> str:
        if self.prefixed:
            return "prefixed"
        result = subprocess.run(  # noqa: S603
            [SYSTEMCTL, *args], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() or result.stderr.strip()

    def caddy_reload(self, caddyfile: str = "/etc/caddy/Caddyfile") -> None:
        if self.prefixed:
            return
        self._run("reload caddy", [CADDY, "reload", "--config", caddyfile])

    def which(self, program: str) -> str | None:
        return shutil.which(program)

    def is_root(self) -> bool:
        return os.geteuid() == 0

    def _run(self, step: str, command: list[str], allow_fail: bool = False) -> None:
        try:
            result = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise StepError(step, str(exc)) from exc
        if result.returncode != 0 and not allow_fail:
            detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
            raise StepError(step, detail)


CADDYFILE = "/etc/caddy/Caddyfile"
CADDY_IMPORT = "import smallapp.d/*.caddy"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    fix: str


def preflight(system: System) -> list[Check]:
    """Can this host host units? Every failure carries the command that fixes it."""
    checks = [
        _binary(system, "systemctl", "this host has no systemd; smallapp only targets systemd"),
        _binary(system, "caddy", "install Caddy: https://caddyserver.com/docs/install"),
        _binary(system, "uv", "install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"),
    ]
    caddyfile = system.read(CADDYFILE)
    checks.append(
        Check(
            f"{CADDYFILE} imports smallapp.d/*.caddy",
            caddyfile is not None and CADDY_IMPORT in caddyfile.decode("utf-8", "replace"),
            f"add a line `{CADDY_IMPORT}` to {CADDYFILE}, then: caddy reload",
        )
    )
    checks.append(
        Check(
            "running as root",
            system.prefixed or system.is_root(),
            "re-run with sudo, or use --root DIR to apply into a prefix",
        )
    )
    return checks


def _binary(system: System, program: str, fix: str) -> Check:
    if system.prefixed:
        found = any(
            (system.root / directory / program).exists()
            for directory in ("usr/bin", "usr/local/bin", "bin", "usr/sbin")
        )
    else:
        found = system.which(program) is not None
    return Check(f"{program} present", found, fix)
