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

## Requirements

- **Your laptop:** Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).
- **The host:** Debian or Ubuntu with systemd, [Caddy](https://caddyserver.com), and
  `uv`. A DNS A record pointing your domain at it. Ports 80 and 443 open.
  `smallapp doctor` checks all of this and names the command that fixes each gap.

## Quickstart

### 1. Prepare a bare VPS (once per box)

As root on the host:

```sh
apt update && apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

Tell Caddy to pick up smallapp vhosts, then reload it:

```sh
mkdir -p /etc/caddy/smallapp.d
echo 'import smallapp.d/*.caddy' >> /etc/caddy/Caddyfile
caddy reload --config /etc/caddy/Caddyfile
```

Install smallapp itself:

```sh
uv tool install --from git+https://github.com/smallapp/unit smallapp-unit
```

Confirm the host is ready — this exits 0 or tells you exactly what to fix:

```sh
smallapp doctor
```

### 2. Point DNS at the box

Create an A record for `expenses.example.com` pointing at the host's public IP. TLS is
issued automatically by Let's Encrypt on first request; there is no account to make and
no credential to store.

### 3. Deploy

Copy your file to the host, then, as root:

```sh
smallapp plan ./expenses.py --name expenses --domain expenses.example.com
smallapp apply ./expenses.py --name expenses --domain expenses.example.com
smallapp status
```

`apply` prints a login token **once**:

```
login token (shown once): 8f3c2a7e91b4d6...
  open https://expenses.example.com and paste it to sign in
```

Open `https://expenses.example.com`, paste the token, and the app is yours. Nobody
without that token gets past the gateway. To take it all back down and leave the host
as it was:

```sh
smallapp rm expenses
```

## Commands

| Command | What it does |
| --- | --- |
| `smallapp plan TARGET --name N --domain D` | render and show what would change; writes nothing |
| `smallapp apply TARGET --name N --domain D` | create or update the unit (needs root) |
| `smallapp status [NAME] [--json]` | what is deployed, and whether it is running |
| `smallapp rm NAME` | remove every artifact the unit created |
| `smallapp gateway` | the auth gateway process; the generated unit runs this |
| `smallapp doctor` | can this host host units, and if not, how to fix it |

Useful flags:

```sh
smallapp plan ./site --name notes --domain notes.example.com --out /tmp/rendered
smallapp apply ./site --name notes --domain notes.example.com --tls internal
smallapp apply ./site --name notes --domain notes.example.com --root /tmp/fakehost
```

`--out DIR` writes every rendered artifact into `DIR`, mirroring its final absolute
path, so you can read the units before they exist. `--tls internal` uses Caddy's local
CA instead of Let's Encrypt, for a LAN or CI box. `--root DIR` applies into a prefix
instead of `/`, which needs no root and is how the test suite deploys.

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
installs them at start:

```python
# /// script
# dependencies = ["flask"]
# ///
```

A file with no such header is run with plain `python3` and needs no network at all.

A static target is a directory containing `index.html`. That is the whole contract.

## Where things land

| Artifact | Path |
| --- | --- |
| unix user | `sa-NAME` |
| app payload | `/opt/smallapp/NAME/` |
| writable state | `/var/lib/smallapp/NAME/` |
| secrets (0600) | `/etc/smallapp/NAME.env` |
| services | `/etc/systemd/system/smallapp-NAME[-gw].service` |
| vhost | `/etc/caddy/smallapp.d/NAME.caddy` |
| registry | `/var/lib/smallapp/registry.json` |

The app service never sees the session secret or the token hash: only the gateway
reads `/etc/smallapp/NAME.env`. Re-running `apply` reuses the secrets already on disk,
so a second run reports `no changes` and does not print a new token.

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
tiny apps. The full list is under **Non-goals** in `specs/smallapp-unit.md`.
