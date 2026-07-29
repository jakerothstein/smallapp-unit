# Implementation plan

## QA FINDINGS (round 4, 2026-07-29)

### 1. HIGH — Python apps can bypass Caddy authentication by binding publicly

- **File:** `src/smallapp/templates.py:64-65`
- **Reproduce:** Deploy a valid target that reads `PORT` but listens on
  `0.0.0.0:$PORT`, then request `http://HOST:18xxx/` directly without a cookie.
  `SocketBindAllow=tcp:PORT` permits every local address, so the request reaches the
  app whenever that port is network-reachable.
- **Fixed:** The service must enforce loopback-only reachability independently of
  untrusted app code; a target choosing `0.0.0.0` or `::` cannot expose the app port.

### 2. HIGH — Every app can read every other unit's payload

- **File:** `src/smallapp/render.py:21,114-120`,
  `src/smallapp/system.py:115-118`
- **Reproduce:** Deploy `victim`, then run
  `runuser -u sa-attacker -- cat /opt/smallapp/victim/app.py` (or
  `index.html`). Payload directories are `0755` and files are `0644`, despite the
  threat-model guarantee that an app cannot read another unit's files.
- **Fixed:** Each app UID can read only its own payload. Static content must grant
  Caddy access without granting all app UIDs access (for example, a per-unit group or
  ACL); Python source needs no cross-unit reader.

### 3. MEDIUM — A failed removal cannot be retried

- **File:** `src/smallapp/apply.py:102-104`
- **Reproduce:** Apply a unit, make `System.caddy_reload()` raise during
  `remove_unit()`, then retry `rm`. The first call drops the registry entry before the
  reload; `registry.load()` is empty and the retry returns `not found`.
- **Fixed:** Keep retry state until daemon-reload and Caddy reload succeed, so the
  same `rm` command completes all remaining cleanup after a transient failure.

### 4. MEDIUM — `rm` deletes empty directories it did not create

- **File:** `src/smallapp/apply.py:77-79,106-107`,
  `src/smallapp/system.py:127-132`
- **Reproduce:** In a prefixed root, pre-create an empty
  `etc/caddy/smallapp.d`, then run `smallapp rm ghost --root ROOT`. The command says
  `not found, nothing to do` but deletes that directory. A pre-existing empty
  `opt/smallapp` can also cause its empty parent chain to be removed.
- **Fixed:** Unknown-unit removal changes nothing, and known-unit removal prunes only
  directories smallapp can prove it created.

### 5. MEDIUM — PORT validation accepts fake reads and rejects a valid read

- **File:** `src/smallapp/target.py:64-73`
- **Reproduce:** `reads_port('Fake().getenv("PORT")')` returns `True`, while
  `reads_port('import os\nos.getenv(key="PORT")')` returns `False`.
- **Fixed:** Accept supported positional and keyword reads from actual `os` environment
  APIs, and reject unrelated methods merely named `getenv`.

### 6. LOW — `plan --out` exposes a Python traceback for filesystem errors

- **File:** `src/smallapp/cli.py:104-107`
- **Reproduce:** Run
  `smallapp plan SITE --name x --domain x.example.com --out README.md`, where
  `README.md` is a file. It exits 1 with an uncaught `FileExistsError` traceback.
- **Fixed:** Report a concise CLI error naming the output path and exit 1 without a
  traceback.

### 7. LOW — Invalid `apply` targets use the wrong documented exit code

- **File:** `src/smallapp/cli.py:118-122`
- **Reproduce:** Run
  `smallapp apply /nonexistent --name x --domain x.example.com --root ROOT`; it exits
  2, while the `apply` contract says every failure exits 1.
- **Fixed:** Invalid `apply` targets exit 1; exit 2 remains specific to invalid
  `plan` targets.

Ordered vertical slices. Each ends with `uv run pytest && uv run ruff check . && uv run mypy src tests`
green. Numbers in parentheses are the acceptance criteria from `specs/smallapp-unit.md`
that the slice closes. Do not start a slice before the one above it is green.

## 0. Skeleton (done)

`pyproject.toml`, package, stub test, README, `.gitignore`. Bootstrap/test/lint/typecheck
all pass. — closes (1) partially, (2), (3).

## 1-3. CLI shell, target detection, tokens (done)

`cli.py` (six commands, `plan` wired), `target.py`, `naming.py`, `tokens.py` and
their tests. — closes (4), (5), (6), part of (7), (25), (16), (17), half of (20).

## 4. Gateway (done)

`gateway.py` serves the six-row contract and reverse-proxies authorised requests to
`SMALLAPP_UPSTREAM`, which is what lets the e2e test run with no Caddy.
— closes (18), (19), (20).

## 5. Render (done)

`templates.py` + `render.py`, goldens under `tests/golden/`, `plan --out DIR` writes
them. Regenerate goldens with `UPDATE_GOLDEN=1 uv run pytest tests/test_render.py`.
— closes (8), (9), (10).

## 6-8. Registry, System seam, plan, apply, rm, doctor (done)

`registry.py`, `system.py` (incl. `preflight` for doctor), `plan.py`, `apply.py`, and
the full CLI. — closes (7), (11), (12), (13), (14), (15), (23), (24 partly).

## 9-10. End to end, doctor, hygiene (done)

`test_e2e.py` (python + static, real subprocesses, real login), `test_hardening.py`
(skips off Linux with an explicit reason), `test_hygiene.py`, README quickstart.
— closes (21), (22), (24), (26), (27), (28), (1).

## 11. QA rounds 1-3 (done)

All findings from the three QA rounds are fixed and each has a regression test.
Highlights, because later loops will trip on them:
separate `sa-NAME-gw` uid, symlink-confined writes, user-ownership tracking
(`Unit.created_user`), apply completeness (`Unit.complete`), an `flock` on
`/var/lib/smallapp`, AST-based `PORT` detection, stale-payload removal, Caddy admin
on a unix socket, `O_EXCL`-at-final-mode secret writes, and 405 on non-POST logout.

## Open items

None. Every acceptance criterion in `specs/smallapp-unit.md` is covered by a test.

## Known limits (documented, not bugs)

- **Loopback is shared.** `SocketBindDeny=any` stops a unit binding a port it was not
  given, but any unit can still *connect* to another unit's `127.0.0.1:PORT`.
  `PrivateNetwork=yes` would fix it and would also break `uv run --script`. Recorded in
  the spec's `### Threat model (v1)`. Revisit only with a design that keeps PEP 723
  working (per-unit netns + a proxied resolver, most likely).
- **`System.path()` is lstat-then-open.** A local attacker who can win the window
  between the check and the write can still redirect it. Fixing it properly means
  `openat(O_NOFOLLOW)` per component; the ceiling is marked with a `ponytail:` comment.
- **Two-uid isolation is asserted at the render level** (distinct `User=`, distinct
  `StateDirectory=`, `ProtectProc=invisible`, `ProcSubset=pid`). Proving the app really
  cannot read or signal the gateway needs real root on Linux; do it on the first Linux
  box you touch, alongside criterion 26.

## Notes for later loops

- Anything not in `§ Acceptance criteria` is out of scope. If you want it, amend the
  spec first, in the same commit.
- The `System` seam is the only place `subprocess` and `os.chown` may appear. Keeping
  it that way is what makes everything else testable on a laptop.
- The gateway proxies non-`/_smallapp/*` paths itself. In production Caddy already
  routes around it; the proxy path exists so slice 9 can test the real login flow.
- `Unit` lives in `naming.py` (lowest layer) so registry/plan/apply/render can all
  see it without an import cycle.
- Dependency-free scripts render `python3 app.py`; only a PEP 723 header renders
  `uv run --script`. That is what keeps the e2e test offline.
- The app unit gets `Environment=PORT=`, never the env file: secrets stay with the
  gateway.
- `doctor`'s checks live in `system.py` (`preflight`), not a separate module: they are
  host inspection, which is that module's job.
- Under `--root DIR` the user database is a marker file per user in
  `var/lib/smallapp/users/`; that is what makes `plan` report `unchanged` on a second
  run without root.
- Criterion 26 has never run on this machine (macOS); the first Linux CI run is the
  real check on the hardening score.
- e2e overrides only the two port numbers (a test may not assume a fixed port is
  free); everything else comes from the rendered unit and the rendered env file.
- Real-host verification (a live VPS with Caddy and systemd) is a manual step
  documented in the README, not a test. Criterion 26 is the automated stand-in.
- `NAME_MAX` is 26, not 32: `sa-NAME-gw` must fit the 32-char unix username limit.
- `plan.build()` takes a fifth argument, `known: Unit | None` — the registry entry.
  It is what makes apply refuse to adopt a pre-existing user and what makes a
  half-finished apply retry its side effects instead of reporting `unchanged`.
- The apply lock is `flock` on the `/var/lib/smallapp` *directory*, not a lock file:
  a file would survive `rm` and break criterion 14 (`rm` leaves nothing behind).
  A `rm` of an unknown unit therefore prunes the directory the lock just created.
- macOS `/etc` is itself a symlink, so `System.path()` only refuses symlinked
  components when prefixed. Unprefixed writes are root-only anyway.
