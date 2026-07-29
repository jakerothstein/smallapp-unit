# smallapp-unit

`smallapp` — turn one file into one hardened, private, self-hosted deploy unit.

## Problem

Agents made small software cheap to write and it is still expensive to ship. A
person ends up with `expenses.py` or a folder of HTML that is genuinely useful to
exactly one user — themselves — and the only paths to running it on the internet are
a PaaS (an account, a container image, a per-app meter, a cold start) or an afternoon
of writing a systemd unit, a Caddy vhost, a `useradd` line, and some kind of login so
the whole internet cannot read their expenses. Most of these tools therefore never
leave the laptop. This is the YC "A Cloud for Small Software" complaint: the
infrastructure tax is charged **per app**, and small software's whole point is that
there are many apps.

Who has it: one technical-enough person with one VPS they already pay for (a $5
Hetzner/DO box), a directory of single-file tools written mostly by an agent, and no
appetite for Docker, Kubernetes, or a control-plane daemon that idles at 500 MB. They
want the app reachable from any browser, over TLS, private to them, restarted on
boot, and confined enough that unaudited agent-written code cannot read the rest of
the box.

## Scope (v1)

A single-binary-feel Python CLI, **zero runtime dependencies**, that on a fresh
Debian/Ubuntu host converts a *deploy target* into a *unit*.

A **deploy target** is exactly one of:
- a single Python file that serves HTTP on `127.0.0.1:$PORT` (deps declared inline
  via PEP 723 `# /// script` metadata, installed by `uv`), or
- a directory containing `index.html` (served as static files).

A **unit** named `NAME` is this set of artifacts, and nothing else:

| Artifact | Path |
| --- | --- |
| dedicated app unix user | `sa-NAME` (system, `/usr/sbin/nologin`, no home) |
| dedicated gateway unix user | `sa-NAME-gw` (system, `/usr/sbin/nologin`, no home) |
| app payload | `/opt/smallapp/NAME/` (root-owned, world-readable, app cannot write it) |
| writable state | `/var/lib/smallapp/NAME/` and `/var/lib/smallapp/NAME-gw/` (via `StateDirectory=`) |
| secrets | `/etc/smallapp/NAME.env` (mode 0600, root-owned) |
| app service | `/etc/systemd/system/smallapp-NAME.service` (hardened) |
| auth gateway service | `/etc/systemd/system/smallapp-NAME-gw.service` (hardened) |
| vhost | `/etc/caddy/smallapp.d/NAME.caddy` |
| registry entry | `/var/lib/smallapp/registry.json` |

Properties v1 guarantees:
1. **Idempotent.** `apply` twice = same state; the second run reports no changes.
2. **Hardened.** Generated units carry `NoNewPrivileges`, `ProtectSystem=strict`,
   `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `ProtectKernelTunables`,
   `ProtectControlGroups`, `RestrictSUIDSGID`, `RestrictAddressFamilies`,
   `SystemCallFilter=@system-service`, `MemoryMax`, and score < 4.0 under
   `systemd-analyze security`.
3. **Private by default.** Every unit sits behind a self-contained signed-cookie
   gateway. No OAuth provider, no Redis, no Cloudflare account, no Caddy plugin —
   stock Caddy `forward_auth` pointing at a ~150-line stdlib HTTP service. The gateway
   runs as its own uid with its own state directory, so app code cannot read the
   gateway's environment and forge an owner cookie.
4. **Free TLS.** Caddy + Let's Encrypt: no signup, no credential, no env var.
   `--tls internal` uses Caddy's local CA for CI and LAN use.
5. **Reversible.** `smallapp rm NAME` removes every artifact above and leaves the
   host as it was. It never deletes a unix user smallapp did not create.

### Threat model (v1)

Trusted: root, systemd, Caddy, and the operator. Untrusted: the deployed app, which is
assumed to be unaudited agent-written code, and the internet.

What is enforced: an app cannot read another unit's files, the gateway's environment,
or the rest of the box (`ProtectSystem=strict`, per-unit uids, `ProtectProc=invisible`,
no capabilities, a syscall filter); it cannot bind a port other than its own
(`SocketBindDeny=any`); it cannot reach Caddy's admin API, which `doctor` requires to
be `off` or on a unix socket rather than `127.0.0.1:2019`; and nothing reaches the app
from the internet without an owner cookie.

What is **not** enforced: apps share the host loopback interface, so one deployed app
can connect to another unit's `127.0.0.1:18xxx`. Per-cgroup packet filtering would be
needed to close that, and firewall management is a non-goal; the gateway, not the
network, is the auth boundary. This is stated so it is a documented limit rather than
an assumed guarantee.

### Non-goals (v1)

Ruthlessly out: Docker/OCI anything · multi-server or clustering · zero-downtime or
blue-green deploys · rollbacks and release history · git-push deploy · multi-file
apps, requirements.txt, repos, Procfiles, or buildpacks · framework detection
(Flask/FastAPI/Django) · databases, queues, cron, workers · log aggregation, metrics,
dashboards · multi-user sharing, invites, per-person ACLs, revocation UI (v1 auth is
**owner-only, one shared token**) · OAuth/OIDC/SSO/MFA · a web UI of any kind ·
non-systemd inits · non-Caddy proxies · macOS/Windows *hosts* (the CLI must *run* and
be *testable* on macOS; it only *applies* to Linux) · DNS record management ·
firewall management · secret rotation automation · resource metering or billing.

## User surface

Six commands. `smallapp --help` lists exactly these.

### `smallapp plan TARGET --name NAME --domain DOMAIN [--tls internal|acme] [--out DIR]`

Pure function: reads the target, writes nothing to the system, prints the rendered
artifacts and the ordered list of actions. Exit 0 if plannable, 2 if the target is
invalid (with the reason on stderr).

```
$ smallapp plan ./expenses.py --name expenses --domain expenses.example.com
plan: expenses (python, 127.0.0.1:18412) -> https://expenses.example.com

  + user      sa-expenses
  + dir       /opt/smallapp/expenses
  + file      /opt/smallapp/expenses/app.py
  + file      /etc/smallapp/expenses.env            (0600)
  + unit      /etc/systemd/system/smallapp-expenses.service
  + unit      /etc/systemd/system/smallapp-expenses-gw.service
  + vhost     /etc/caddy/smallapp.d/expenses.caddy
  + reload    caddy

8 actions, 0 unchanged.  (nothing applied; run `smallapp apply`)
```

`--out DIR` writes every rendered file into `DIR` mirroring its final absolute path,
so the artifacts are diffable and testable without root. Errors are specific:

```
$ smallapp plan ./expenses.py --name expenses --domain expenses.example.com
error: ./expenses.py never reads the PORT environment variable.
       smallapp apps must serve on 127.0.0.1:$PORT. Example:
           port = int(os.environ["PORT"])
```

### `smallapp apply TARGET --name NAME --domain DOMAIN [--tls ...] [--root /]`

Executes the plan. Requires root when `--root /`. `--root DIR` applies into a prefix
(this is how the end-to-end test runs unprivileged). Prints the same action list with
`+` (created), `~` (changed), `=` (unchanged). Exit 0 on success, 1 on failure —
failure names the step that failed on stderr and leaves already-written artifacts in
place for inspection.

Second identical run:

```
8 actions, 8 unchanged.  no changes.
```

On first apply it prints the owner token exactly once:

```
login token (shown once): 8f3c2a7e91b4d6...
  open https://expenses.example.com and paste it to sign in
```

### `smallapp status [NAME] [--json] [--root /]`

Reads the registry; per unit prints name, kind, port, domain, and `systemctl
is-active` for both services.

### `smallapp rm NAME [--root /]`

Stops and disables both services, removes every artifact listed in Scope, reloads
Caddy, drops the registry entry. Idempotent: removing an unknown unit exits 0 with
`not found, nothing to do`.

### `smallapp gateway`

The auth gateway process itself (what the `-gw.service` unit runs). Config from env:
`SMALLAPP_SECRET` (HMAC key), `SMALLAPP_TOKEN_HASH`, `SMALLAPP_GW_PORT`,
`SMALLAPP_UPSTREAM`. Refuses to start with a clear message if `SMALLAPP_SECRET` or
`SMALLAPP_TOKEN_HASH` is missing — never generates a default.

HTTP contract:

| Request | Response |
| --- | --- |
| `GET /_smallapp/auth` with valid unexpired cookie | `204` |
| `GET /_smallapp/auth` without/with bad/expired cookie | `401` + `Location: /_smallapp/login` |
| `GET /_smallapp/login` | `200` HTML form |
| `POST /_smallapp/login` correct token | `303` to `/` + `Set-Cookie: __Host-smallapp=...; HttpOnly; Secure; SameSite=Lax; Path=/` |
| `POST /_smallapp/login` wrong token | `401` HTML form + error, constant-time compare, no cookie |
| `POST /_smallapp/logout` | `303` to `/_smallapp/login` + expired cookie |

Cookie value is `v1.<b64 sub>.<exp>.<b64 hmac-sha256>`; any tamper, any expiry in the
past, any wrong version prefix fails closed.

### `smallapp doctor [--root /]`

Host preflight (implemented as `system.preflight`): systemd present, Caddy present,
its Caddyfile carries an *active* (not commented-out) `import smallapp.d/*.caddy`
directive, its admin API is `off` or on a unix socket rather than a TCP port, `uv`
present, running as root. Exit 0 if the host can host; exit 1 listing each failed check
and the exact command to fix it. It does not check DNS or that ports 80/443 are open.

## Architecture

Zero runtime dependencies — Python 3.11+ stdlib only. Dev deps: `pytest`, `ruff`,
`mypy`. Justification, one line each: nothing survives `ProtectSystem=strict` for
free, and the entire product is string rendering plus `subprocess`.

### File layout

```
src/smallapp/
  __init__.py      version
  __main__.py      python -m smallapp
  cli.py           argparse, exit codes, output formatting. No logic.
  target.py        detect + validate a deploy target -> Target(kind, path, entry)
  naming.py        name validation, deterministic port allocation, path constants
  render.py        pure: Target + Unit -> dict[abs path, RenderedFile(text, mode)]
  templates.py     the systemd / Caddyfile / env text templates
  plan.py          rendered files + current filesystem -> ordered list[Action]
  apply.py         execute Actions through a System (root-prefix aware)
  system.py        thin seam over os/subprocess: write, chown, useradd, systemctl,
                   caddy reload, and the doctor preflight checks. One real impl;
                   a --root prefix makes it testable.
  registry.py      /var/lib/smallapp/registry.json read/write, port collisions
  gateway.py       stdlib HTTP auth gateway (own module, own test, no CLI coupling)
  tokens.py        secret generation, token hashing, cookie sign/verify (hmac+secrets)
tests/
  test_target.py test_naming.py test_render.py test_plan.py test_apply.py
  test_registry.py test_tokens.py test_gateway.py test_cli.py test_system.py
  conftest.py        sample app, sample site, prefixed System, tree hash
  test_e2e.py        full apply into a temp root + live gateway + live app
  test_hardening.py  systemd-analyze security (Linux+systemd only)
  test_hygiene.py    no unfinished markers; every README command parses
specs/  IMPLEMENTATION_PLAN.md  AGENTS.md  README.md  pyproject.toml
```

Dependency direction is one-way: `cli -> plan/apply -> render -> templates`, and
`gateway`/`tokens` import nothing above them. `render` and `tokens` are pure and are
where the tests are densest.

### Data model

```python
Target  = (kind: Literal["python","static"], root: Path, entry: Path | None)
Unit    = (name: str, domain: str, kind: str, port: int, gw_port: int,
           tls: Literal["acme","internal"], created_at: str)   # lives in naming.py
Action  = (verb: Literal["mkdir","write","chown","chmod","user","systemctl",
                         "caddy_reload","rm"], target: str, detail: dict,
           state: Literal["create","change","unchanged"])
```

`Unit` also records `created_user` (smallapp created `sa-NAME`/`sa-NAME-gw`, so `rm`
may delete them) and `complete` (the last apply finished its side effects, so a retry
after a failed reload redoes the reload instead of reporting success).

Registry is `{"version": 1, "units": {NAME: Unit}}`. Allocation through registration
runs under an exclusive `flock` on `/var/lib/smallapp`, so concurrent applies cannot
pick the same port or drop each other's entry. Ports are allocated
deterministically (`18000 + crc32(name) % 1000`, gateway `+1000`) then linearly
probed against the registry, so a name keeps its port forever unless it is taken.

### Generated vhost (shape)

```caddyfile
expenses.example.com {
    handle /_smallapp/* { reverse_proxy 127.0.0.1:19412 }
    forward_auth 127.0.0.1:19412 {
        uri /_smallapp/auth
        copy_headers X-Smallapp-User
    }
    reverse_proxy 127.0.0.1:18412
}
```

Static units swap the final line for `root * /opt/smallapp/NAME` + `file_server`, so
Caddy serves the bytes directly. They still get an app service (stdlib
`python3 -m http.server` on `$PORT`), which keeps every unit uniform for `status`,
restart-on-boot, and the end-to-end test's upstream.
`--tls internal` adds a `tls internal` line.

## § Acceptance criteria

Every item is checkable by `pytest` or by a command's exit code.

1. `uv sync && uv run pytest` exits 0 from a clean checkout, needing no network
   beyond the Python package index.
2. `uv run ruff check . && uv run ruff format --check .` exits 0.
3. `uv run mypy src tests` exits 0 with `strict = true`.
4. `uv run smallapp --help` exits 0 and its output names exactly the six commands
   `plan, apply, status, rm, gateway, doctor`.
5. `target.detect()` classifies: a `.py` file reading `PORT` → `python`; a directory
   with `index.html` → `static`; a `.py` file that never mentions `PORT` → error
   mentioning `PORT`; a nonexistent path → error mentioning the path; a directory
   without `index.html` → error mentioning `index.html`. All five asserted.
6. `naming.validate()` accepts `a`, `my-app`, `app2`; rejects empty, `-lead`,
   `trail-`, `Upper`, `has_underscore`, and anything over 26 chars (`sa-NAME-gw` must
   fit the 32-character unix username limit). Each rejection raises with the offending
   name in the message.
7. `naming.port_for("expenses")` is deterministic across processes and within
   `18000..18999`; a name whose slot is occupied in the registry gets the next free
   port, asserted by a test that pre-seeds a collision.
8. `render()` for a python target produces exactly the artifact set in Scope; a
   golden-file test compares all rendered bytes against `tests/golden/` and fails if
   any file is added, removed, or changed without updating the golden.
9. Rendered unit files contain every directive listed in Scope property 2, asserted
   directive-by-directive, for both the app unit and the gateway unit.
10. No unescaped user input reaches a rendered file: a test feeds names and domains
    containing newlines, `%`, spaces, and `}` and asserts each is rejected before
    render rather than escaped.
11. `plan()` against an empty root reports every action as `create`; run against a
    root where `apply()` already ran, it reports every action as `unchanged`.
12. `apply(root=tmp)` creates every file at the right prefixed path with the right
    mode; `/etc/smallapp/NAME.env` is asserted mode `0o600`.
13. `apply()` run twice into the same root is byte-identical (compare a recursive
    hash of the tree) and the second run reports `0 changed`.
14. `rm(root=tmp)` after `apply(root=tmp)` leaves the tree identical to before the
    apply, asserted by the same recursive hash, and the registry has no entry.
15. `apply()` that fails at step *k* (injected failure in the `System` seam) exits
    non-zero and names the failing step on stderr.
16. `tokens.sign/verify` round-trips; verify returns false for a tampered payload, a
    tampered signature, an expired `exp`, a wrong version prefix, a wrong secret, and
    a truncated value. All six asserted.
17. `tokens.hash_token` uses a salted `hashlib.scrypt`; a test asserts the same token
    hashes differently under different salts and verifies correctly under its own.
18. `gateway` exits non-zero within 2 seconds with a message naming `SMALLAPP_SECRET`
    when that env var is absent, and likewise for `SMALLAPP_TOKEN_HASH`.
19. **Gateway HTTP contract**: a test starts the real gateway on an ephemeral port
    and asserts every row of the `smallapp gateway` table above, including that the
    `Set-Cookie` carries `HttpOnly`, `SameSite=Lax`, and `Path=/`.
20. A wrong-token `POST /_smallapp/login` returns 401 and sets no cookie, and 20
    consecutive wrong attempts still return 401; a test asserts `gateway.py` and
    `tokens.py` compare secrets only via `hmac.compare_digest`.
21. **End-to-end (real user path, no root, no network):** the test (a) writes a
    sample single-file Python app that serves `hello` on `$PORT`, (b) runs
    `smallapp apply` into a temp root with `--tls internal`, (c) starts the rendered
    app and gateway commands as real subprocesses using the rendered env file,
    (d) requests `/` through the gateway with no cookie → 401 + login redirect,
    (e) posts the token from apply's output → 303 + cookie,
    (f) re-requests `/` with that cookie → 200 with body `hello`,
    (g) `POST /_smallapp/logout` then re-requests → 401 again.
    One test, asserted at every step.
22. **End-to-end static:** same as 21 for a static directory target, asserting the
    rendered vhost contains `file_server` and `root * /opt/smallapp/NAME`. The vhost is
    rendered for the real host, so it names the final absolute path even when written
    into a `--root` prefix; Caddy is not running under the prefix.
23. `smallapp status --json` after an apply emits valid JSON whose object for `NAME`
    has `kind`, `port`, `gw_port`, `domain`, `tls`; asserted by parsing.
24. `smallapp doctor` on a host missing prerequisites exits 1 and its stderr names
    each missing one; against a prepared fake root with all checks satisfied it
    exits 0.
25. `smallapp plan` on an invalid target exits **2** (not 1), asserted via
    `subprocess.run(...).returncode`.
26. **Hardening (Linux+systemd only, skipped elsewhere with an explicit reason):**
    `systemd-analyze security --json=short` on both rendered units reports exposure
    < 4.0.
27. `grep -rn "TODO\|FIXME\|XXX\|NotImplementedError" src/` returns no matches; a
    test asserts this over the shipped package.
28. `README.md` contains a copy-pasteable quickstart; a test asserts every fenced
    `smallapp ...` command in the README parses under the real argparse parser.

## Quality bar

- Every module in `src/smallapp/` has a matching test module. No stubs, no
  `NotImplementedError`, no `TODO` in shipped code (criterion 27 enforces it).
- `ruff` and `mypy --strict` clean; both are acceptance criteria, not suggestions.
- No runtime dependency may be added without a one-line justification in the README
  and a corresponding note in `AGENTS.md`.
- Secrets never appear in a plan, a log line, an exception message, or the registry —
  only in `/etc/smallapp/NAME.env` at 0600 and in the one-time token print.
- All input reaching a rendered file is validated first; render never escapes, it
  refuses.
- README must take a stranger from a bare VPS to a working private URL with no
  outside knowledge.
