"""Rendering: golden bytes, every hardening directive, and refusal of hostile input."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from smallapp.naming import Unit, ValidationError
from smallapp.render import render, uv_cache_dir
from smallapp.target import Target, detect

GOLDEN = Path(__file__).parent / "golden"
SECRET = "GOLDEN-SECRET-DO-NOT-USE"
TOKEN_HASH = "scrypt$16384$8$1$YWFhYWFhYWFhYWFhYWFhYQ$Z29sZGVuLWhhc2gtZm9yLXRlc3Rpbmctb25s"

APP_SOURCE = 'import os\n\nPORT = int(os.environ["PORT"])\nprint(PORT)\n'

REQUIRED_DIRECTIVES = [
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "PrivateTmp=yes",
    "PrivateDevices=yes",
    "ProtectKernelTunables=yes",
    "ProtectControlGroups=yes",
    "RestrictSUIDSGID=yes",
    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
    "SystemCallFilter=@system-service",
    "MemoryMax=",
    # QA round 6 #2: without these an app that binds 0.0.0.0 answers around Caddy.
    "IPAddressDeny=any",
    "IPAddressAllow=localhost",
]


def python_unit() -> Unit:
    return Unit(
        name="expenses",
        domain="expenses.example.com",
        kind="python",
        port=18412,
        gw_port=19412,
        tls="acme",
        created_at="2024-01-01T00:00:00+00:00",
    )


def static_unit() -> Unit:
    return Unit(
        name="notes",
        domain="notes.example.com",
        kind="static",
        port=18500,
        gw_port=19500,
        tls="internal",
        created_at="2024-01-01T00:00:00+00:00",
    )


@pytest.fixture
def python_target(tmp_path: Path) -> Target:
    root = tmp_path / "pysrc"
    root.mkdir()
    app = root / "expenses.py"
    app.write_text(APP_SOURCE)
    return detect(app)


@pytest.fixture
def static_target(tmp_path: Path) -> Target:
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text("<h1>notes</h1>\n")
    (root / "app.css").write_text("body{font-family:sans-serif}\n")
    return detect(root)


def test_python_render_produces_exactly_the_scoped_artifacts(python_target: Target) -> None:
    unit = python_unit()
    files = render(python_target, unit, SECRET, TOKEN_HASH)
    assert set(files) == {
        "/opt/smallapp/expenses/app.py",
        "/etc/smallapp/expenses.env",
        "/etc/systemd/system/smallapp-expenses.service",
        "/etc/systemd/system/smallapp-expenses-gw.service",
        "/etc/caddy/smallapp.d/expenses.caddy",
    }
    assert files["/opt/smallapp/expenses/app.py"].content == APP_SOURCE.encode()
    assert files["/etc/smallapp/expenses.env"].mode == 0o600
    # The payload is 0640 root:sa-NAME: no other unit's uid may read another's source.
    assert files["/opt/smallapp/expenses/app.py"].mode == 0o640
    assert all(
        f.mode == 0o644
        for p, f in files.items()
        if not p.endswith(".env") and not p.startswith("/opt/smallapp/")
    )


def test_static_render_mirrors_every_payload_file(static_target: Target) -> None:
    files = render(static_target, static_unit(), SECRET, TOKEN_HASH)
    assert "/opt/smallapp/notes/index.html" in files
    assert "/opt/smallapp/notes/app.css" in files
    vhost = files["/etc/caddy/smallapp.d/notes.caddy"].text
    assert "file_server" in vhost
    assert "root * /opt/smallapp/notes" in vhost
    assert "tls internal" in vhost
    assert "reverse_proxy 127.0.0.1:18500" not in vhost


def test_python_vhost_reverse_proxies_the_app(python_target: Target) -> None:
    vhost = render(python_target, python_unit(), SECRET, TOKEN_HASH)[
        "/etc/caddy/smallapp.d/expenses.caddy"
    ].text
    assert "expenses.example.com {" in vhost
    assert "reverse_proxy 127.0.0.1:18412" in vhost
    assert "forward_auth 127.0.0.1:19412 {" in vhost
    assert "uri /_smallapp/auth" in vhost
    assert "copy_headers X-Smallapp-User" in vhost
    assert "tls internal" not in vhost


@pytest.mark.parametrize("directive", REQUIRED_DIRECTIVES)
def test_both_units_carry_every_hardening_directive(python_target: Target, directive: str) -> None:
    files = render(python_target, python_unit(), SECRET, TOKEN_HASH)
    for path in (
        "/etc/systemd/system/smallapp-expenses.service",
        "/etc/systemd/system/smallapp-expenses-gw.service",
    ):
        assert directive in files[path].text, f"{directive} missing from {path}"


def test_env_file_carries_every_gateway_variable(python_target: Target) -> None:
    env = render(python_target, python_unit(), SECRET, TOKEN_HASH)["/etc/smallapp/expenses.env"]
    values = dict(
        line.split("=", 1) for line in env.text.splitlines() if line and not line.startswith("#")
    )
    assert values["SMALLAPP_SECRET"] == SECRET
    assert values["SMALLAPP_TOKEN_HASH"] == TOKEN_HASH
    assert values["SMALLAPP_GW_PORT"] == "19412"
    assert values["SMALLAPP_UPSTREAM"] == "127.0.0.1:18412"
    assert values["PORT"] == "18412"


def test_app_unit_never_sees_the_secret(python_target: Target) -> None:
    unit_text = render(python_target, python_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-expenses.service"
    ].text
    assert SECRET not in unit_text
    assert "EnvironmentFile" not in unit_text
    assert "Environment=PORT=18412" in unit_text


def test_dependency_free_script_runs_without_uv(python_target: Target) -> None:
    text = render(python_target, python_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-expenses.service"
    ].text
    assert "ExecStart=/usr/bin/env python3 /opt/smallapp/expenses/app.py" in text


def test_pep723_script_runs_under_uv(tmp_path: Path) -> None:
    app = tmp_path / "expenses.py"
    app.write_text(
        '# /// script\n# dependencies = ["httpx"]\n# ///\nimport os\nos.environ["PORT"]\n'
    )
    text = render(detect(app), python_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-expenses.service"
    ].text
    assert "ExecStart=/usr/bin/env uv run --offline --script /opt/smallapp/expenses/app.py" in text


def test_static_unit_serves_the_directory_on_port(static_target: Target) -> None:
    text = render(static_target, static_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-notes.service"
    ].text
    assert "http.server" in text
    assert "--directory /opt/smallapp/notes" in text
    assert "--bind 127.0.0.1" in text


@pytest.mark.parametrize("hostile", ["ex\npenses", "ex penses", "ex%penses", "ex}penses"])
def test_hostile_names_are_refused_not_escaped(hostile: str) -> None:
    with pytest.raises(ValidationError):
        Unit(
            name=hostile,
            domain="ok.example.com",
            kind="python",
            port=18412,
            gw_port=19412,
            tls="acme",
            created_at="2024-01-01T00:00:00+00:00",
        )


@pytest.mark.parametrize("hostile", ["ex\np.com", "ex p.com", "ex%p.com", "ex}p.com"])
def test_hostile_domains_are_refused_not_escaped(hostile: str) -> None:
    with pytest.raises(ValidationError):
        Unit(
            name="expenses",
            domain=hostile,
            kind="python",
            port=18412,
            gw_port=19412,
            tls="acme",
            created_at="2024-01-01T00:00:00+00:00",
        )


def test_render_refuses_whitespace_in_secrets(python_target: Target) -> None:
    with pytest.raises(ValueError, match="secret"):
        render(python_target, python_unit(), "has space", TOKEN_HASH)
    with pytest.raises(ValueError, match="token hash"):
        render(python_target, python_unit(), SECRET, "bad\nhash")


def test_render_refuses_kind_mismatch(static_target: Target) -> None:
    with pytest.raises(ValueError, match="kind"):
        render(static_target, python_unit(), SECRET, TOKEN_HASH)


@pytest.mark.parametrize("flavour", ["python", "static"])
def test_golden_files_match(python_target: Target, static_target: Target, flavour: str) -> None:
    """Fails if any rendered byte, file name, or file is added or removed."""
    target, unit = (
        (python_target, python_unit()) if flavour == "python" else (static_target, static_unit())
    )
    files = render(target, unit, SECRET, TOKEN_HASH)
    root = GOLDEN / flavour
    rendered = {path: files[path].content for path in files}
    if os.environ.get("UPDATE_GOLDEN"):
        for path, content in rendered.items():
            destination = root / path.lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    stored = {
        "/" + str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    assert set(stored) == set(rendered), "golden file set drifted; UPDATE_GOLDEN=1 to refresh"
    for path in sorted(rendered):
        assert stored[path] == rendered[path], f"{path} changed; UPDATE_GOLDEN=1 to refresh"


def _units(target: Target) -> tuple[str, str]:
    files = render(target, python_unit(), SECRET, TOKEN_HASH)
    return (
        files["/etc/systemd/system/smallapp-expenses.service"].text,
        files["/etc/systemd/system/smallapp-expenses-gw.service"].text,
    )


def test_the_gateway_runs_as_its_own_user(python_target: Target) -> None:
    """QA round 3 #1: same uid means the app can read /proc/<gw>/environ and lift the
    HMAC secret. The two services must not share an account."""
    app_unit, gw_unit = _units(python_target)
    assert "User=sa-expenses\n" in app_unit
    assert "Group=sa-expenses\n" in app_unit
    assert "User=sa-expenses-gw\n" in gw_unit
    assert "Group=sa-expenses-gw\n" in gw_unit
    assert "User=sa-expenses\n" not in gw_unit


def test_the_two_services_share_no_writable_state(python_target: Target) -> None:
    app_unit, gw_unit = _units(python_target)
    assert "StateDirectory=smallapp/expenses\n" in app_unit
    assert "StateDirectory=smallapp/expenses-gw\n" in gw_unit
    assert "WorkingDirectory=/opt/smallapp/expenses\n" in app_unit  # root-owned, read-only
    assert "WorkingDirectory=/var/lib/smallapp/expenses-gw\n" in gw_unit


def test_processes_of_other_users_are_invisible(python_target: Target) -> None:
    for unit_text in _units(python_target):
        assert "ProtectProc=invisible" in unit_text
        assert "ProcSubset=pid" in unit_text


def test_each_service_may_bind_only_its_own_port(python_target: Target) -> None:
    """QA round 3 #3: an app must not be able to squat another unit's loopback port."""
    app_unit, gw_unit = _units(python_target)
    assert "SocketBindAllow=tcp:18412\nSocketBindDeny=any\n" in app_unit
    assert "SocketBindAllow=tcp:19412\nSocketBindDeny=any\n" in gw_unit


def test_generated_units_document_the_real_repository(python_target: Target) -> None:
    """QA round 3 #8: `smallapp/unit` does not exist; a stranger must find the source."""
    for unit_text in _units(python_target):
        assert "Documentation=https://github.com/jakerothstein/smallapp-unit" in unit_text
        assert "github.com/smallapp/unit" not in unit_text


def test_a_wildcard_binding_app_is_still_unreachable_from_the_internet(
    python_target: Target, static_target: Target
) -> None:
    """QA round 6 #2: `SocketBindAllow=` restricts the port, not the local address.

    Only the cgroup address filter makes reachability independent of what the untrusted
    app chose to bind, so both units must carry it, in that order.
    """
    for target, unit in ((python_target, python_unit()), (static_target, static_unit())):
        files = render(target, unit, SECRET, TOKEN_HASH)
        for path, text in ((p, f.text) for p, f in files.items() if p.endswith(".service")):
            assert "IPAddressDeny=any\nIPAddressAllow=localhost" in text, path


def test_a_pep723_script_runs_offline_from_a_cache_inside_its_state_dir(tmp_path: Path) -> None:
    """QA round 6 #2: the unit has no egress, so `uv` may not resolve at start-up."""
    app = tmp_path / "expenses.py"
    app.write_text(
        '# /// script\n# dependencies = ["httpx"]\n# ///\nimport os\nos.environ["PORT"]\n'
    )
    text = render(detect(app), python_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-expenses.service"
    ].text
    assert "ExecStart=/usr/bin/env uv run --offline --script" in text
    assert "Environment=UV_CACHE_DIR=/var/lib/smallapp/expenses/uv-cache" in text
    assert uv_cache_dir(python_unit()) == "/var/lib/smallapp/expenses/uv-cache"


def test_a_docstring_mentioning_the_marker_is_not_a_dependency_block(tmp_path: Path) -> None:
    app = tmp_path / "expenses.py"
    app.write_text('"""not really: # /// script"""\nimport os\nos.environ["PORT"]\n')
    text = render(detect(app), python_unit(), SECRET, TOKEN_HASH)[
        "/etc/systemd/system/smallapp-expenses.service"
    ].text
    assert "ExecStart=/usr/bin/env python3 /opt/smallapp/expenses/app.py" in text
