## QA FINDINGS (round 11, 2026-07-29)

1. **Critical — interrupted applies still permanently skip payload/state ownership.**
   `src/smallapp/plan.py:148-155` and `src/smallapp/plan.py:170-179` mark ownership
   unchanged from directory existence alone; `src/smallapp/apply.py:35-37` then skips
   it. Reproduce by interrupting a real-host apply after `mkdir` but before `chgrp` or
   `chown`, then retry: the existing directories make all three ownership actions
   unchanged and the unit can reach `complete=true` without executing them. Fixed means
   retries compare or safely reapply the required uid/gid, with a regression test that
   interrupts before ownership and proves the retry performs every ownership action.

2. **Critical — duplicate Content-Length headers desynchronise gateway connections.**
   `src/smallapp/gateway.py:252-268` reads only the first value returned by
   `self.headers.get("Content-Length")`. Reproduce over one raw socket with a login POST
   containing `Content-Length: 5`, `Content-Length: 100`, 100 body bytes, then a valid
   GET: the gateway returns 401, reads five bytes, and parses the remaining 95 bytes plus
   `GET` as an unsupported method. Fixed means every duplicate Content-Length is rejected
   with 413 and a closed connection, with a raw-socket regression test.

3. **High — dependency resolution executes untrusted package build code as root.**
   `src/smallapp/system.py:340-360` runs `uv sync --script` through
   `src/smallapp/system.py:386-405`, reached from root-only apply in
   `src/smallapp/cli.py:118-152`. Reproduce with a PEP 723 target depending on an
   attacker-controlled sdist whose PEP 517 backend records `geteuid()`: its build hook
   runs as uid 0 before systemd confinement exists. Fixed means resolution cannot execute
   third-party build code as root and a test proves the resolver child is unprivileged.

4. **High — pruning the lock directory can create two simultaneous exclusive locks.**
   `src/smallapp/system.py:177-202` removes `/var/lib/smallapp`, while
   `src/smallapp/system.py:204-222` locks that directory inode. Reproduce with process A
   holding the lock, B opening the same inode and waiting, A pruning the directory and
   releasing, then C recreating and locking the path: B and C lock different inodes
   concurrently. Fixed means the lock inode is stable and never pruned, with a concurrent
   rm/apply test proving registry writers cannot overlap.

5. **Medium — PORT detection accepts unrelated attribute chains as the os module.**
   `src/smallapp/target.py:53-57` accepts any attribute named `os`. Reproduce with
   `reads_port('fake.os.getenv("PORT")')`, which returns `True` although the process
   environment is never read. Fixed means only supported imports of the real `os` module
   qualify, with `.os` impostors rejected by regression tests.

6. **Medium — acceptance criterion 1 contradicts the default clean-checkout suite.**
   `specs/smallapp-unit.md:297-298` permits no network beyond the Python package index,
   but `tests/test_hardening.py:46-56` pulls `debian:bookworm-slim` and runs
   `apt-get update`; `README.md:224-226` admits those extra requirements. Reproduce on
   macOS with Docker available and Docker Hub or Debian mirrors blocked: default
   `uv run pytest` collects the slow hardening test and fails. Fixed means the default
   suite obeys criterion 1, or the criterion explicitly names every required service.

## QA FINDINGS (round 10, 2026-07-29)

1. **Critical — interrupted applies still permanently skip payload/state ownership.**
   `src/smallapp/plan.py:148-155` and `src/smallapp/plan.py:170-179` infer ownership
   from directory existence, and `src/smallapp/apply.py:35-37` skips every action marked
   unchanged. Reproduce by creating `/opt/smallapp/NAME` after the first plan but before
   `chgrp` runs, then rebuild the plan with an incomplete registry entry: `chgrp` changes
   from `create` to `unchanged` without ever executing. The same holds for both `chown`
   actions. Fixed means retries compare or safely reapply the actual uid/gid, with a
   regression test that interrupts before ownership and proves the retry performs it.

2. **High — duplicate Content-Length headers still desynchronise gateway connections.**
   `src/smallapp/gateway.py:252-268` reads only
   `self.headers.get("Content-Length")`. Reproduce over one socket with a login POST
   carrying `Content-Length: 5`, `Content-Length: 100`, 100 body bytes, then a valid
   GET: the POST returns 401 and the server parses the remaining 95 bytes plus `GET` as
   an unsupported method. Fixed means every duplicate Content-Length is rejected with
   413 and a closed connection, with a raw-socket regression test.

3. **High — dependency resolution executes untrusted package build code as root.**
   `src/smallapp/system.py:340-360` runs `uv sync --script` through
   `src/smallapp/system.py:386-405` without dropping privileges or adding confinement;
   `src/smallapp/cli.py:118-152` requires the invoking apply process to be root. A PEP
   723 target naming a malicious or compromised sdist therefore executes its PEP 517
   build backend as root before the hardened unit starts. Fixed means dependency
   resolution runs as the dedicated app uid inside a confinement boundary with only
   its cache writable, and a test proves the child uid is not root.

4. **High — pruning the lock directory splits one exclusive lock into two.**
   `src/smallapp/system.py:177-202` can remove `/var/lib/smallapp` while
   `src/smallapp/system.py:204-222` uses that directory inode as the flock target.
   Reproduce with process A holding the directory lock, process B opening the same inode
   and waiting, A removing the directory and releasing, then process C recreating and
   locking the path: B and C hold exclusive locks concurrently on different inodes.
   Fixed means the lock inode is stable and never pruned, with a concurrent rm/apply
   regression test proving only one registry writer enters at a time.

5. **Medium — PORT detection accepts unrelated attribute chains as the os module.**
   `src/smallapp/target.py:53-57` returns true for any attribute named `os`, so
   `reads_port('fake.os.getenv("PORT")')` is true even though the process environment
   is never read. The CLI accepts such an app and deploys one that need not bind the
   assigned port. Fixed means only supported imports of the real `os` module qualify,
   with `fake.os.getenv("PORT")` and equivalent aliases rejected by regression tests.

6. **Medium — acceptance criterion 1 contradicts the default clean-checkout suite.**
   `specs/smallapp-unit.md:297-298` permits no network beyond the Python package index,
   but `tests/test_hardening.py:46-56` pulls `debian:bookworm-slim` and runs
   `apt-get update`; `README.md:224-226` confirms the default suite needs Docker and
   those external networks off Linux. Reproduce on a clean macOS host without the image
   cached while blocking Docker Hub or Debian mirrors. Fixed means the default suite
   obeys criterion 1, or the criterion and bootstrap documentation explicitly name all
   required external services.

## QA FINDINGS (round 9, 2026-07-29)

1. **Critical — interrupted applies permanently skip payload/state ownership.**
   `src/smallapp/plan.py:148-155` and `src/smallapp/plan.py:170-179` mark `chgrp`
   and `chown` unchanged whenever the directory exists; `src/smallapp/apply.py:35-37`
   then skips them. Reproduce with a PEP 723 target by failing `uv sync` on the first
   apply: the directories exist before ownership runs, and the retry reports every
   ownership action unchanged, marks the unit complete, and never calls `chgrp` or
   `chown`. On a real host the `0750` payload can remain inaccessible to `sa-NAME`.
   Fixed means a retry detects or safely reapplies incorrect/unknown UID and GID, with
   a regression test that fails after directory creation but before ownership and
   proves the retry executes both ownership operations before marking the unit complete.

2. **High — duplicate Content-Length headers desynchronise gateway keep-alive
   connections.** `src/smallapp/gateway.py:252-268` reads only
   `self.headers.get("Content-Length")`, which returns the first value when duplicates
   are present. Reproduce over one raw TCP connection by sending a POST with
   `Content-Length: 5`, `Content-Length: 100`, and 100 body bytes, then a valid
   `GET /_smallapp/login`: the gateway reads five bytes and parses the remaining 95 as
   the next request method, returning 501. This violates acceptance criterion 34 and is
   reachable directly from another untrusted app over shared loopback. Fixed means the
   gateway rejects every duplicate Content-Length as ambiguous, returns 413, closes the
   connection, and has a raw-socket regression test proving no bytes reach a subsequent
   request.

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
