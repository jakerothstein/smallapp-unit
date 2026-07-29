"""Name/domain validation, deterministic port allocation, and path constants.

Validation refuses rather than escapes: nothing that reaches a rendered file may
contain a character that could change the meaning of a systemd unit, a Caddyfile,
or an env file.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from zlib import crc32

NAME_MAX = 32
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOMAIN_MAX = 253

APP_PORT_BASE = 18000
APP_PORT_SPAN = 1000
GW_PORT_OFFSET = 1000

OPT_DIR = PurePosixPath("/opt/smallapp")
STATE_DIR = PurePosixPath("/var/lib/smallapp")
ENV_DIR = PurePosixPath("/etc/smallapp")
UNIT_DIR = PurePosixPath("/etc/systemd/system")
CADDY_DIR = PurePosixPath("/etc/caddy/smallapp.d")
REGISTRY_PATH = STATE_DIR / "registry.json"


class ValidationError(ValueError):
    """Raised when user input may not be rendered into an artifact."""


def validate_name(name: str) -> str:
    """Return `name` if it is a legal unit name, else raise naming it."""
    if not name:
        raise ValidationError("unit name is empty: names must match [a-z0-9-], 1-32 chars")
    if len(name) > NAME_MAX:
        raise ValidationError(f"unit name {name!r} is {len(name)} chars, the maximum is {NAME_MAX}")
    if not NAME_RE.match(name):
        raise ValidationError(
            f"unit name {name!r} is invalid: use lowercase letters, digits and inner "
            "hyphens only (e.g. 'expenses', 'my-app')"
        )
    return name


def validate_domain(domain: str) -> str:
    """Return `domain` if it is a legal hostname, else raise naming it."""
    if not domain:
        raise ValidationError("domain is empty: pass --domain like app.example.com")
    if len(domain) > DOMAIN_MAX:
        raise ValidationError(
            f"domain {domain!r} is {len(domain)} chars, the maximum is {DOMAIN_MAX}"
        )
    labels = domain.split(".")
    if len(labels) < 2:
        raise ValidationError(f"domain {domain!r} is invalid: expected a dotted hostname")
    for label in labels:
        if not label or len(label) > 63 or not DOMAIN_LABEL_RE.match(label):
            raise ValidationError(
                f"domain {domain!r} is invalid: each label must match [a-z0-9-], "
                "1-63 chars, not starting or ending with a hyphen"
            )
    return domain


def user_for(name: str) -> str:
    return f"sa-{validate_name(name)}"


def service_for(name: str) -> str:
    return f"smallapp-{validate_name(name)}.service"


def gw_service_for(name: str) -> str:
    return f"smallapp-{validate_name(name)}-gw.service"


def port_for(name: str, taken: frozenset[int] | set[int] | None = None) -> int:
    """Deterministic app port for `name`, linearly probed past `taken`.

    Stable across processes: crc32 is defined by the zlib spec, unlike hash().
    """
    validate_name(name)
    taken = taken or frozenset()
    start = crc32(name.encode()) % APP_PORT_SPAN
    for step in range(APP_PORT_SPAN):
        port = APP_PORT_BASE + (start + step) % APP_PORT_SPAN
        if port not in taken:
            return port
    raise ValidationError(
        f"no free port in {APP_PORT_BASE}..{APP_PORT_BASE + APP_PORT_SPAN - 1}: "
        f"all {APP_PORT_SPAN} are allocated"
    )


def gw_port_for(port: int) -> int:
    return port + GW_PORT_OFFSET
