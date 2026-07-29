"""The only module allowed to touch the OS: files, users, systemctl, Caddy.

A `--root DIR` prefix makes every path land inside DIR and turns the privileged verbs
(useradd, systemctl, caddy) into recorded no-ops, which is how apply is testable on a
laptop. Everything above this module is pure and needs no root to exercise.
"""

from __future__ import annotations

import fcntl
import os
import pwd
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

USERADD = "/usr/sbin/useradd"
USERDEL = "/usr/sbin/userdel"
USERDEL_NO_SUCH_USER = 6  # userdel(8): "specified user doesn't exist"
SYSTEMCTL = "systemctl"
CADDY = "caddy"
USER_MARKER_DIR = "var/lib/smallapp/users"
LOCK_DIR = "/var/lib/smallapp"


class StepError(RuntimeError):
    """A privileged step failed. Carries the step name so apply can name it."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step


class System:
    """Filesystem and service operations, confined to a prefix and to real directories."""

    def __init__(self, root: str | Path = "/") -> None:
        self.root = Path(root).resolve()
        self.prefixed = self.root != Path("/")

    def path(self, absolute: str) -> Path:
        """Map an absolute artifact path into this system's root.

        Under a prefix every component is checked with `lstat`: a symlinked or `..`
        component is refused rather than followed, so `--root DIR` really confines to
        DIR. At the real root there is nothing to confine to — and `/etc` is genuinely
        a symlink on macOS — so only `..` is refused there.

        ponytail: this is a check-then-use, so it is not proof against an attacker who
        can win a rename race inside the prefix. Everything under the prefix is created
        by this process; upgrade to openat(O_NOFOLLOW) walks if untrusted users ever
        get write access to an artifact directory.
        """
        if not absolute.startswith("/"):
            raise ValueError(f"{absolute!r} is not an absolute path")
        target = self.root
        for part in PurePosixPath(absolute).parts[1:]:
            if part == "..":
                raise StepError(f"resolve {absolute}", "path contains '..'")
            target = target / part
            if self.prefixed and target.is_symlink():
                raise StepError(
                    f"resolve {absolute}",
                    f"{target} is a symlink; refusing to follow it out of {self.root}",
                )
        return target

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

    def list_files(self, absolute: str) -> list[str]:
        """Absolute logical paths of every regular file under `absolute`."""
        target = self.path(absolute)
        if not target.is_dir():
            return []
        base = absolute.rstrip("/")
        return sorted(
            f"{base}/{item.relative_to(target).as_posix()}"
            for item in target.rglob("*")
            if item.is_file() and not item.is_symlink()
        )

    def write(self, absolute: str, content: bytes, mode: int) -> None:
        """Write atomically. The temporary file is *created* at `mode`, never at 0644:
        a secret must not exist world-readable for even one scheduling slice."""
        target = self.path(absolute)
        self.mkdir_p(target.parent)
        temporary = target.with_name(f".{target.name}.smallapp-tmp")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            os.fchmod(descriptor, mode)  # umask may not weaken the declared mode
            os.write(descriptor, content)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)

    def mkdir_p(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)

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

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Serialise port allocation through registration across concurrent applies.

        The lock is `flock` on the state directory itself, so it leaves no artifact for
        `rm` to have to clean up.

        ponytail: `rm` of the last unit prunes that directory, which invalidates the
        lock for anyone holding it. One box, one operator — give the lock its own
        never-pruned file if simultaneous rm and apply ever becomes real.
        """
        target = self.path(LOCK_DIR)
        target.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

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
        """Delete a user smallapp created. An already-absent user is fine; anything
        else (a live process, a busy home) is a failure the caller must surface."""
        if self.prefixed:
            marker = self.root / USER_MARKER_DIR / user
            if marker.exists():
                marker.unlink()
            return
        self._run(f"delete user {user}", [USERDEL, user], tolerate=(USERDEL_NO_SUCH_USER,))

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

    def _run(self, step: str, command: list[str], tolerate: tuple[int, ...] = ()) -> None:
        try:
            result = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise StepError(step, str(exc)) from exc
        if result.returncode != 0 and result.returncode not in tolerate:
            detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
            raise StepError(step, detail)


CADDYFILE = "/etc/caddy/Caddyfile"
CADDY_IMPORT = "import smallapp.d/*.caddy"
CADDY_ADMIN = "admin unix//run/caddy/admin.sock"
IMPORT_RE = re.compile(r"^import\s+smallapp\.d/\*\.caddy$")
ADMIN_RE = re.compile(r"^admin\s+(?P<endpoint>\S+)$")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    fix: str


def _directives(text: str) -> list[str]:
    """Active Caddyfile directives: comments and blank lines are not configuration."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped.split(" #")[0].split("\t#")[0].strip())
    return lines


def caddy_imports_smallapp(text: str) -> bool:
    return any(IMPORT_RE.match(line) for line in _directives(text))


def caddy_admin_is_private(text: str) -> bool:
    """True when the admin API is off or on a unix socket, i.e. not on 127.0.0.1:2019.

    A TCP admin endpoint is reachable by every deployed app, and it can rewrite the
    whole Caddy config — including deleting a unit's `forward_auth`.
    """
    for line in _directives(text):
        match = ADMIN_RE.match(line)
        if match:
            endpoint = match.group("endpoint")
            return endpoint == "off" or endpoint.startswith("unix/")
    return False


def preflight(system: System) -> list[Check]:
    """Can this host host units? Every failure carries the command that fixes it."""
    checks = [
        _binary(system, "systemctl", "this host has no systemd; smallapp only targets systemd"),
        _binary(system, "caddy", "install Caddy: https://caddyserver.com/docs/install"),
        _binary(system, "uv", "install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"),
    ]
    raw = system.read(CADDYFILE)
    caddyfile = "" if raw is None else raw.decode("utf-8", "replace")
    checks.append(
        Check(
            f"{CADDYFILE} imports smallapp.d/*.caddy",
            raw is not None and caddy_imports_smallapp(caddyfile),
            f"add a line `{CADDY_IMPORT}` to {CADDYFILE}, then: caddy reload",
        )
    )
    checks.append(
        Check(
            "caddy admin API is off the network",
            raw is not None and caddy_admin_is_private(caddyfile),
            f"put `{{ {CADDY_ADMIN} }}` at the top of {CADDYFILE} so deployed apps "
            "cannot reach it on 127.0.0.1:2019, then: systemctl restart caddy",
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
