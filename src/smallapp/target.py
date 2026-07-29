"""Detect and validate a deploy target: one Python file, or one directory of files."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["python", "static"]

PORT_VAR = "PORT"
INDEX = "index.html"
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

# The reference regex from PEP 723, restricted to the `script` block.
PEP723_RE = re.compile(
    r"(?m)^# /// script$\s(?P<body>(^#(| .*)$\s)+)^# ///$",
)


def declares_dependencies(source: str) -> bool:
    """True when the script carries a PEP 723 `# /// script` block.

    That block is the only signal that the app needs third-party packages, and so the
    only reason to involve `uv`. A bare marker in a docstring is not a block: the
    regex requires the closing `# ///` too.
    """
    return PEP723_RE.search(source) is not None


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


def _is_os(node: ast.expr) -> bool:
    """The `os` module itself: `os`, or an attribute chain ending in `.os`."""
    if isinstance(node, ast.Attribute):
        return node.attr == "os"
    return isinstance(node, ast.Name) and node.id == "os"


def _is_environ(node: ast.expr) -> bool:
    """`os.environ`/`os.environb`, or a bare `environ` imported from `os`.

    An attribute must hang off `os`: `Fake().environ` is somebody else's mapping and
    reading `PORT` from it says nothing about how the app is served.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in ("environ", "environb") and _is_os(node.value)
    return isinstance(node, ast.Name) and node.id in ("environ", "environb")


def _is_port_literal(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in (PORT_VAR, PORT_VAR.encode())


def reads_port(source: str) -> bool:
    """True only when the source really *reads* `PORT` from the environment.

    A comment mentioning PORT or a bare string "PORT" is not a server; the check is on
    the syntax tree, so neither can satisfy it.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            if _is_port_literal(node.slice):
                return True
        elif isinstance(node, ast.Call) and _is_port_call(node):
            return True
    return False


def _is_port_call(node: ast.Call) -> bool:
    """`os.getenv("PORT")` / `os.environ.get("PORT")` and their keyword spellings.

    The receiver must really be `os` (or a name imported from it): a method merely
    *named* `getenv` on some other object does not read the process environment.
    """
    if not _reads_port_argument(node):
        return False
    function = node.func
    if isinstance(function, ast.Attribute):
        if function.attr in ("getenv", "getenvb"):
            return _is_os(function.value)
        return function.attr == "get" and _is_environ(function.value)
    return isinstance(function, ast.Name) and function.id in ("getenv", "getenvb")


def _reads_port_argument(node: ast.Call) -> bool:
    """`PORT` passed positionally, or as `key=` — the only keyword these APIs name it."""
    if node.args:
        return _is_port_literal(node.args[0])
    return any(
        keyword.arg == "key" and _is_port_literal(keyword.value) for keyword in node.keywords
    )


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
    try:
        found = reads_port(source)
    except SyntaxError as exc:
        raise TargetError(f"{shown}: is not valid Python ({exc.msg} on line {exc.lineno})") from exc
    if not found:
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
