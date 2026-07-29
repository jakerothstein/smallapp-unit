# smallapp

Turn one file into one hardened, private, self-hosted deploy unit.

You wrote `expenses.py`. It serves HTTP, it is useful to exactly one person — you —
and getting it onto the internet currently costs an account, a container image, or an
afternoon of systemd and nginx. `smallapp` does it in one command, on a VPS you
already have, with no Docker, no control-plane daemon, and no PaaS.

```sh
smallapp apply ./expenses.py --name expenses --domain expenses.example.com
```

That gives you, on the host:

- a dedicated unprivileged unix user, `sa-expenses`
- a **hardened** systemd service (`ProtectSystem=strict`, `NoNewPrivileges`, syscall
  filter, memory cap) so unaudited agent-written code cannot read the rest of the box
- a Caddy vhost with free automatic TLS from Let's Encrypt
- a signed-cookie auth gateway in front of it, so the app is **private to you** — no
  OAuth provider, no Cloudflare account, no Redis, no Caddy plugin
- everything reversible with `smallapp rm expenses`

## Status

Skeleton. The specification is `specs/smallapp-unit.md`; the build order is
`IMPLEMENTATION_PLAN.md`. Commands currently parse and exit 3.

## Requirements

- **Your laptop:** Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).
- **The host:** Debian or Ubuntu with systemd, [Caddy](https://caddyserver.com), and
  `uv`. A DNS A record pointing your domain at it. Ports 80 and 443 open.
  `smallapp doctor` checks all of this and tells you the command to fix each gap.

## Quickstart

```sh
uv sync --all-extras
uv run smallapp --help
```

On the host, as root:

```sh
smallapp doctor
smallapp apply ./expenses.py --name expenses --domain expenses.example.com
smallapp status
```

`apply` prints a login token once. Open `https://expenses.example.com`, paste it, and
the app is yours. To take it all back down:

```sh
smallapp rm expenses
```

## What your app must do

A Python target is **one file** that serves HTTP on `127.0.0.1:$PORT`:

```python
import os
from http.server import HTTPServer, BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"hello")


HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
```

Dependencies go inline, [PEP 723](https://peps.python.org/pep-0723/) style, and `uv`
installs them:

```python
# /// script
# dependencies = ["flask"]
# ///
```

A static target is a directory containing `index.html`. That is the whole contract.

## Development

See `AGENTS.md` for every command. In short:

```sh
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
```

**Runtime dependencies: none.** Python's standard library only — nothing else
survives `ProtectSystem=strict` for free, and the whole product is string rendering
plus `subprocess`. `pytest`, `ruff`, and `mypy` are dev-only.

## What this is not

Not a PaaS, not a container platform, not multi-server, not a team tool. No
databases, no rollbacks, no dashboard, no git-push deploy. One person, one box, many
tiny apps. The full list is under **Non-goals** in the spec.
