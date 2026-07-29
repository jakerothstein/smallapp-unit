"""Detect and validate a deploy target: one Python file, or one directory of files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["python", "static"]

PORT_RE = re.compile(r"\bPORT\b")
INDEX = "index.html"
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024


class TargetError(ValueError):
    """Raised when a path cannot be deployed as a unit."""


@dataclass(frozen=True)
class Target:
    kind: Kind
    root: Path
    entry: Path | None

    @property
    def files(self) -> list[Path]:
        """Payload files, relative-sorted, that must be copied into /opt/smallapp/NAME."""
        if self.kind == "python":
            if self.entry is None:
                raise TargetError(f"{self.root}: python target has no entry file")
            return [self.entry]
        return sorted(p for p in self.root.rglob("*") if p.is_file())


def detect(path: str | Path) -> Target:
    """Classify `path` as a deploy target or raise TargetError explaining why not."""
    candidate = Path(path)
    if not candidate.exists():
        raise TargetError(f"{candidate}: no such file or directory")
    resolved = candidate.resolve()
    if resolved.is_dir():
        return _detect_static(candidate, resolved)
    if resolved.is_file():
        return _detect_python(candidate, resolved)
    raise TargetError(f"{candidate}: not a regular file or directory")


def _detect_python(shown: Path, resolved: Path) -> Target:
    if resolved.suffix != ".py":
        raise TargetError(
            f"{shown}: a file target must be a single .py file that serves HTTP on "
            "127.0.0.1:$PORT (a directory target must contain index.html)"
        )
    try:
        source = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TargetError(f"{shown}: not valid UTF-8 Python source ({exc.reason})") from exc
    if not PORT_RE.search(source):
        raise TargetError(
            f"{shown} never reads the PORT environment variable.\n"
            "       smallapp apps must serve on 127.0.0.1:$PORT. Example:\n"
            '           port = int(os.environ["PORT"])'
        )
    return Target(kind="python", root=resolved.parent, entry=resolved)


def _detect_static(shown: Path, resolved: Path) -> Target:
    if not (resolved / INDEX).is_file():
        raise TargetError(f"{shown}: a directory target must contain {INDEX}")
    total = 0
    for file in resolved.rglob("*"):
        if file.is_symlink():
            raise TargetError(f"{shown}: contains a symlink ({file.name}); copy the real file in")
        if file.is_file():
            total += file.stat().st_size
    if total > MAX_PAYLOAD_BYTES:
        raise TargetError(f"{shown}: payload is {total} bytes, the maximum is {MAX_PAYLOAD_BYTES}")
    return Target(kind="static", root=resolved, entry=None)
