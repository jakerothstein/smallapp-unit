# AGENTS.md

Operational reference for `smallapp-unit`. Spec: `specs/smallapp-unit.md`.
Order of work: `IMPLEMENTATION_PLAN.md`.

## Bootstrap

```sh
uv sync --all-extras
```

Requires `uv` (https://docs.astral.sh/uv/) and Python >= 3.11. No other setup.

## Commands

| Purpose | Command |
| --- | --- |
| Run the CLI | `uv run smallapp --help` |
| Test | `uv run pytest` |
| Test (one file) | `uv run pytest tests/test_gateway.py` |
| Test (fast only) | `uv run pytest -m "not slow"` (skips the subprocess e2e tests) |
| Refresh golden files | `UPDATE_GOLDEN=1 uv run pytest tests/test_render.py` |
| Deploy into a prefix | `uv run smallapp apply APP.py --name n --domain n.example.com --root /tmp/h` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Format (fix) | `uv run ruff format .` |
| Typecheck | `uv run mypy src tests` |
| All gates | `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest` |

## Dependencies

Runtime: **none**. Python 3.11+ stdlib only. Adding one requires a one-line
justification in `README.md` and a line here.
Dev only: `pytest`, `ruff`, `mypy`.

## Env vars

None are required to build, test, or lint. No external service, no account, no
credential: TLS comes from Caddy + Let's Encrypt, which is free and needs no signup.

The `smallapp gateway` process (started by the generated systemd unit, and by the
end-to-end test) reads these from the generated `/etc/smallapp/NAME.env`:

| Var | Meaning | Missing behaviour |
| --- | --- | --- |
| `SMALLAPP_SECRET` | HMAC key for session cookies | exit 1, message names the var |
| `SMALLAPP_TOKEN_HASH` | salted scrypt hash of the owner token | exit 1, message names the var |
| `SMALLAPP_GW_PORT` | gateway listen port on 127.0.0.1 | defaults to 19000 |
| `SMALLAPP_UPSTREAM` | `host:port` of the app being gated | defaults to 127.0.0.1:18000 |
| `PORT` | port the deployed app must bind | set by the app unit |

Never hardcode any of these. Never commit a generated `.env`.

## Ports

- App units: `18000-18999`, bound to `127.0.0.1` only.
- Gateway units: `19000-19999`, bound to `127.0.0.1` only.
- Caddy admin API: `127.0.0.1:2019` (used for reloads, never exposed).
- Tests bind ephemeral ports (`:0`) and must never assume a fixed one.

## Host targets

The CLI runs and is fully tested on macOS and Linux. It only *applies* to Linux with
systemd + Caddy. Tests that need real systemd (`tests/test_hardening.py`) skip with an
explicit reason elsewhere; they must never pass vacuously.

Use `--root DIR` to apply into a prefix without root. That is how the end-to-end test
works, and it is the only supported way to test apply on a laptop. Under a prefix,
`useradd`, `systemctl` and `caddy reload` become no-ops and users are recorded as
marker files in `<root>/var/lib/smallapp/users/`.

`systemd-analyze security` (criterion 26) has only ever skipped on macOS. On a Linux
box run `uv run pytest tests/test_hardening.py` and confirm it does not skip.
