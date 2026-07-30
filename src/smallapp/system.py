"""The only module allowed to touch the OS: files, users, systemctl, Caddy.

A `--root DIR` prefix makes every path land inside DIR and turns the privileged verbs
(useradd, systemctl, caddy) into recorded no-ops, which is how apply is testable on a
laptop. Everything above this module is pure and needs no root to exercise.
"""

from __future__ import annotations

import fcntl
import grp
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
USERMOD = "/usr/sbin/usermod"
GPASSWD = "/usr/bin/gpasswd"
USERDEL_NO_SUCH_USER = 6  # userdel(8): "specified user doesn't exist"
SYSTEMCTL = "systemctl"
CADDY = "caddy"
UV = "uv"
CADDY_USER = "caddy"
USER_MARKER_DIR = "var/lib/smallapp/users"
GROUP_MARKER_DIR = "var/lib/smallapp/groups"
LOCK_DIR = "/var/lib/smallapp"
CREATED_DIRS = "/var/lib/smallapp/created-dirs"


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
        # Logical paths of directories this process had to create. `rm` may prune an
        # empty directory only if smallapp can show it created it.
        self.created: list[str] = []

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
        """Create `target` and any missing parents, recording the topmost one created.

        The record is what makes `rm` safe: an empty `/opt` that was already there when
        smallapp arrived is left alone, and one smallapp had to create is taken away
        again.
        """
        if target.is_dir():
            return
        highest = target
        while (
            highest.parent != highest
            and highest.parent != self.root
            and not highest.parent.is_dir()
        ):
            highest = highest.parent
        self._record_created(highest)
        target.mkdir(parents=True, exist_ok=True)

    def _record_created(self, target: Path) -> None:
        try:
            logical = "/" + target.relative_to(self.root).as_posix()
        except ValueError:  # pragma: no cover - path() already confines to the root
            return
        if logical not in self.created:
            self.created.append(logical)

    def mkdir(self, absolute: str, mode: int = 0o755) -> None:
        target = self.path(absolute)
        self.mkdir_p(target)
        os.chmod(target, mode)

    def remove(self, absolute: str) -> None:
        target = self.path(absolute)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()

    def flush_created(self) -> None:
        """Persist the created-directory record so a later `rm` process can read it."""
        if not self.created:
            return
        known = self._read_created()
        merged = sorted(set(known) | set(self.created))
        if merged != sorted(known):
            self.write(CREATED_DIRS, ("\n".join(merged) + "\n").encode(), 0o644)

    def _read_created(self) -> list[str]:
        raw = self.read(CREATED_DIRS)
        if raw is None:
            return []
        text = raw.decode("utf-8", "replace")
        return [line for line in text.splitlines() if line.startswith("/")]

    def prune_created(self) -> None:
        """Remove every recorded directory that is now empty, deepest first.

        Directories smallapp never created are never touched, so `rm` cannot delete a
        pre-existing `/etc/caddy/smallapp.d` or walk up into somebody else's `/opt`.
        """
        recorded = sorted(
            set(self._read_created()) | set(self.created),
            key=lambda item: item.count("/"),
            reverse=True,
        )
        self.created = []
        self.remove(CREATED_DIRS)
        left = [logical for logical in recorded if not self._rmdir_empty(self.path(logical))]
        if left and self.path(LOCK_DIR).is_dir():
            self.write(CREATED_DIRS, ("\n".join(sorted(left)) + "\n").encode(), 0o644)

    def _rmdir_empty(self, target: Path) -> bool:
        """Remove `target` if it is an empty directory tree. True when it is gone."""
        if target == self.root or not target.is_dir() or target.is_symlink():
            return False
        for child in target.iterdir():
            if not (child.is_dir() and not child.is_symlink() and self._rmdir_empty(child)):
                return False
        target.rmdir()
        return True

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
        self.mkdir_p(target)
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
            group_marker = self.root / GROUP_MARKER_DIR / user
            if group_marker.exists():
                group_marker.unlink()
            return
        self._run(f"delete user {user}", [USERDEL, user], tolerate=(USERDEL_NO_SUCH_USER,))

    def chown(self, absolute: str, user: str) -> None:
        """Give `absolute` and everything under it to `user`.

        Recursive because a state directory may already hold files this process created
        as root — a pre-built `uv` cache, most of all — and the unit must own them.
        A no-op under a prefix, where there is no such uid.
        """
        if self.prefixed:
            return
        try:
            entry = pwd.getpwnam(user)
        except KeyError as exc:
            raise StepError(f"chown {absolute}", f"no such user {user}") from exc
        self._chown_tree(self.path(absolute), entry.pw_uid, entry.pw_gid)

    def _chown_tree(self, target: Path, uid: int, gid: int) -> None:
        os.chown(target, uid, gid)
        if not target.is_dir():
            return
        for parent, directories, names in os.walk(target):
            for item in (*directories, *names):
                path = Path(parent) / item
                if not path.is_symlink():
                    os.chown(path, uid, gid)

    def chgrp(self, absolute: str, group: str) -> None:
        """Give `absolute` and everything under it to `group`, keeping root as owner.

        This is what stops one unit's uid reading another's payload: the payload stays
        root-owned and unwritable, but only the unit's own group may read it.
        A no-op under a prefix, where there is no such gid.
        """
        if self.prefixed:
            return
        try:
            gid = grp.getgrnam(group).gr_gid
        except KeyError as exc:
            raise StepError(f"chgrp {absolute}", f"no such group {group}") from exc
        self._chown_tree(self.path(absolute), 0, gid)

    def group_members(self, group: str) -> set[str]:
        if self.prefixed:
            marker = self.root / GROUP_MARKER_DIR / group
            if not marker.is_file():
                return set()
            return set(marker.read_text().split())
        try:
            return set(grp.getgrnam(group).gr_mem)
        except KeyError:
            return set()

    def add_group_member(self, group: str, user: str) -> None:
        """Add `user` to `group` as a supplementary member.

        Used to let Caddy — and nothing else — read a static unit's payload. The group
        is the unit's own, created by `useradd --system`, so it disappears with the
        unit and takes the membership with it.
        """
        if self.prefixed:
            marker = self.root / GROUP_MARKER_DIR / group
            self.mkdir_p(marker.parent)
            members = self.group_members(group) | {user}
            marker.write_text("\n".join(sorted(members)) + "\n")
            return
        self._run(f"add {user} to group {group}", [USERMOD, "--append", "--groups", group, user])

    def remove_group_member(self, group: str, user: str) -> None:
        """Revoke a supplementary membership, e.g. when a unit stops being static.

        Not an error if the membership is already gone: apply must stay retryable.
        """
        if self.prefixed:
            marker = self.root / GROUP_MARKER_DIR / group
            members = self.group_members(group) - {user}
            if not marker.parent.is_dir() and not members:
                return
            self.mkdir_p(marker.parent)
            marker.write_text("\n".join(sorted(members)) + "\n" if members else "")
            return
        if user not in self.group_members(group):
            return
        self._run(f"remove {user} from group {group}", [GPASSWD, "--delete", user, group])

    def uv_sync_script(self, script: str, cache_dir: str, marker: str) -> None:
        """Pre-build a PEP 723 script's environment, so the running unit needs no network.

        The app service denies every non-loopback address, which is what stops untrusted
        app code from being reachable around Caddy. That also means `uv` cannot resolve
        anything at start-up, so resolution happens here, at apply time, into a cache the
        unit can read.

        `marker` is written only once `uv sync` has returned successfully, and cleared
        first: the cache directory exists from before the resolve starts, so its presence
        never means the resolve finished.
        """
        cache = self.path(cache_dir)
        self.mkdir_p(cache)
        self.remove(marker)
        self._run(
            f"install dependencies for {script}",
            [UV, "sync", "--script", str(self.path(script))],
            env={"UV_CACHE_DIR": str(cache)},
        )
        self.write(marker, b"", 0o600)

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

    def _run(
        self,
        step: str,
        command: list[str],
        tolerate: tuple[int, ...] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        try:
            result = subprocess.run(  # noqa: S603
                command,
                capture_output=True,
                text=True,
                check=False,
                env=None if env is None else {**os.environ, **env},
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
