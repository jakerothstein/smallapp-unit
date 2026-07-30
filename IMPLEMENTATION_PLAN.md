# Implementation plan

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

## 13. QA rounds 7-8 (done)

All four findings fixed, each with a regression test that fails without the fix:

1. The gateway drains the declared request body once, at the top of `_route`, and hands
   it to `_login`/`_proxy`. A body it cannot frame exactly (bad `Content-Length`,
   chunked) gets 413 *and* `close_connection = True`. Before this, a rejected 9006-byte
   login left its bytes on a keep-alive connection and the next request was parsed as
   `token=aaa...GET`. — `test_a_rejected_request_cannot_bleed_into_the_next_one`.
2. `uv sync` success is now proved by `/var/lib/smallapp/NAME/uv-cache/.synced`, written
   after the resolve and cleared before it. The cache *directory* exists from before the
   resolve starts, so it never meant success. — regression test asserts two sync attempts
   and `complete=False` in between.
3. A static→python transition emits `group ... op=remove`, `System.remove_group_member`
   runs `gpasswd --delete`, and Caddy is restarted so its supplementary groups refresh.
4. Criterion 26 actually runs now. `--json=short` reports per-setting rows with
   `exposure: null`, so the old test crashed on Linux instead of scoring anything; it now
   parses the "Overall exposure level" line, and off Linux it borrows
   `debian:bookworm-slim` via docker. Both units score **0.9 SAFE** against a 4.0 limit.

## 12. QA rounds 4-6 (done)

The same eight findings were raised three rounds running; all are now fixed, each with
a named regression test, and criteria 29-33 were added to the spec so they stay fixed.

1. `resolve_secrets(system, unit, known)` regenerates both secrets unless the registry
   says the unit is `complete`. An interrupted first apply therefore prints a working
   token on retry instead of silently locking the owner out.
2. `IPAddressDeny=any` + `IPAddressAllow=localhost` on both units. `SocketBindAllow=`
   only ever restricted the *port*, so an app binding `0.0.0.0` answered around Caddy.
   The cost is that a unit has no egress either, so PEP 723 dependencies are now
   resolved at apply (`uv sync --script` into `/var/lib/smallapp/NAME/uv-cache`) and
   the unit starts with `uv run --offline`.
3. Payload is `root:sa-NAME`, dir `0750`, files `0640`. Static units add `caddy` — and
   only `caddy` — to `sa-NAME` as a supplementary member, then restart Caddy, because
   supplementary groups are read once at process start. `userdel sa-NAME` takes the
   group and the membership with it.
4. `registry.drop()` moved after daemon-reload and `caddy reload`, so a removal that
   fails part-way is still retryable with the same command.
5. `System` records the topmost directory it had to create (`self.created`, persisted
   to `/var/lib/smallapp/created-dirs`) and `prune_created()` removes only those.
   `rm` of an unknown unit now changes nothing at all.
6. `reads_port` requires the receiver to really be `os` and accepts the `key=` keyword.
7. `plan --out` catches `OSError`: a concise message naming the path, exit 1.
8. `apply` exits 1 on every failure; exit 2 stayed with `plan`.

## Open items

None. Every acceptance criterion in `specs/smallapp-unit.md` is covered by a test,
including the two added in round 8 (34: gateway keep-alive framing —
`tests/test_gateway.py::test_a_rejected_request_cannot_bleed_into_the_next_one`;
35: sync-failure retry —
`tests/test_apply.py::test_a_failed_dependency_sync_is_retried_and_never_marked_complete`).

## Known limits (documented, not bugs)

- **Loopback is shared.** `SocketBindDeny=any` stops a unit binding a port it was not
  given, but any unit can still *connect* to another unit's `127.0.0.1:PORT`.
  `PrivateNetwork=yes` would fix it and would also break `uv run --script`. Recorded in
  the spec's `### Threat model (v1)`. Revisit only with a design that keeps PEP 723
  working (per-unit netns + a proxied resolver, most likely).
- **`System.path()` is lstat-then-open.** A local attacker who can win the window
  between the check and the write can still redirect it. Fixing it properly means
  `openat(O_NOFOLLOW)` per component; the ceiling is marked with a `ponytail:` comment.
- **No app egress.** The address filter that closes the wildcard-bind hole also stops
  a unit calling a third-party API. Documented in the spec's threat model and the
  README. If v2 wants outbound calls, the shape is an allow-list of peer addresses per
  unit (`IPAddressAllow=` takes CIDRs) rather than reopening `any`.
- **`chgrp`, `chown` and group membership are prefix no-ops**, so `--root` tests assert
  the *plan* and a marker file, not the resulting uid. Real proof needs root on Linux.
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
- Criterion 26 runs everywhere docker is available: `tests/test_hardening.py` is marked
  `slow` and shells out to `debian:bookworm-slim` when the host is not Linux. Last
  recorded scores: both units 0.9 SAFE.
- e2e overrides only the two port numbers (a test may not assume a fixed port is
  free); everything else comes from the rendered unit and the rendered env file.
- Real-host verification (a live VPS with Caddy and systemd) is a manual step
  documented in the README, not a test. Criterion 26 is the automated stand-in.
- `NAME_MAX` is 26, not 32: `sa-NAME-gw` must fit the 32-char unix username limit.
- `resolve_secrets()` takes `known: Unit | None` too, and it is not optional in spirit:
  passing `None` for a live unit rotates its secrets and invalidates every session.
- `plan.build()` takes a fifth argument, `known: Unit | None` — the registry entry.
  It is what makes apply refuse to adopt a pre-existing user and what makes a
  half-finished apply retry its side effects instead of reporting `unchanged`.
- The apply lock is `flock` on the `/var/lib/smallapp` *directory*, not a lock file:
  a file would survive `rm` and break criterion 14 (`rm` leaves nothing behind).
  A `rm` of an unknown unit therefore prunes the directory the lock just created — but
  only via `prune_created()`, which never touches a directory smallapp found already
  there.
- macOS `/etc` is itself a symlink, so `System.path()` only refuses symlinked
  components when prefixed. Unprefixed writes are root-only anyway.
