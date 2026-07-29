"""Name/domain validation, deterministic port allocation, and path constants.

Validation refuses rather than escapes: nothing that reaches a rendered file may
contain a character that could change the meaning of a systemd unit, a Caddyfile,
or an env file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from zlib import crc32

NAME_MAX = 26  # `sa-NAME-gw` must still fit the 32-char unix username limit
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
        raise ValidationError(f"unit name is empty: names must match [a-z0-9-], 1-{NAME_MAX} chars")
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


def gw_user_for(name: str) -> str:
    """The gateway's own uid. Separate from the app's so app code cannot read the
    gateway's `/proc/PID/environ` and lift `SMALLAPP_SECRET` out of it."""
    return f"sa-{validate_name(name)}-gw"


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


@dataclass(frozen=True)
class Unit:
    """One deploy unit's identity and bookkeeping. Never holds a secret.

    `created_user` records that smallapp created the unix users, so `rm` never deletes
    a pre-existing account. `complete` records that the last apply finished its side
    effects, so a retry after a failed reload does the reload again.
    """

    name: str
    domain: str
    kind: str
    port: int
    gw_port: int
    tls: str
    created_at: str
    created_user: bool = False
    complete: bool = False

    def __post_init__(self) -> None:
        validate_name(self.name)
        validate_domain(self.domain)
        if self.kind not in ("python", "static"):
            raise ValidationError(f"unknown unit kind {self.kind!r}")
        if self.tls not in ("acme", "internal"):
            raise ValidationError(f"unknown tls mode {self.tls!r}")
        for port in (self.port, self.gw_port):
            if not 1 <= port <= 65535:
                raise ValidationError(f"port {port} is outside 1..65535")

    @property
    def user(self) -> str:
        return user_for(self.name)

    @property
    def gw_user(self) -> str:
        return gw_user_for(self.name)

    @property
    def app_dir(self) -> PurePosixPath:
        return OPT_DIR / self.name

    @property
    def state_dir(self) -> PurePosixPath:
        return STATE_DIR / self.name

    @property
    def gw_state_dir(self) -> PurePosixPath:
        """The gateway's own writable state, shared with nothing."""
        return STATE_DIR / f"{self.name}-gw"

    @property
    def env_path(self) -> PurePosixPath:
        return ENV_DIR / f"{self.name}.env"

    @property
    def service_path(self) -> PurePosixPath:
        return UNIT_DIR / service_for(self.name)

    @property
    def gw_service_path(self) -> PurePosixPath:
        return UNIT_DIR / gw_service_for(self.name)

    @property
    def vhost_path(self) -> PurePosixPath:
        return CADDY_DIR / f"{self.name}.caddy"
