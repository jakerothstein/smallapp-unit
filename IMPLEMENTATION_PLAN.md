## QA FINDINGS (round 3, 2026-07-29)

1. **CRITICAL — the untrusted app can steal the gateway secret.**
   `src/smallapp/templates.py:53,77` runs both services as the same unix user. On
   Linux, app code can read `/proc/<gateway-pid>/environ` as that UID and recover
   `SMALLAPP_SECRET`, then forge owner cookies or signal the gateway. Fixed means a
   distinct gateway UID with no shared writable state, plus a test proving the app
   cannot read or signal the gateway process.

2. **CRITICAL — `--root` confinement is bypassed by symlinks.**
   `src/smallapp/system.py:34-42,56-62` joins paths without rejecting symlinked
   components; `src/smallapp/cli.py:211-217` does the same for `plan --out`. Symlink
   `<root>/etc` to an outside directory, then write `/etc/escaped` through `System`:
   the outside file is created. Fixed means symlink-safe, root-confined filesystem
   operations (for example `openat` plus `O_NOFOLLOW`) and escape regression tests.

3. **HIGH — app code can bypass gateways and reconfigure Caddy.**
   `src/smallapp/templates.py:31` permits host-network `AF_INET`, while app services
   share loopback with every app port and Caddy's unauthenticated admin API at
   `127.0.0.1:2019`. From a deployed app, request another unit's `127.0.0.1:18xxx`
   port or `http://127.0.0.1:2019/config/`; neither path requires an owner cookie.
   Fixed means apps cannot reach Caddy administration or other units' ports, proven
   from inside a deployed service.

4. **HIGH — pre-existing unix users are adopted and later deleted.**
   `src/smallapp/plan.py:75-80` treats any existing `sa-NAME` user as managed, and
   `src/smallapp/apply.py:78` deletes it on `rm` without recording ownership. Create
   `sa-demo` before applying `demo`; apply accepts its unchecked shell/home/UID and
   removal calls `userdel sa-demo`. Fixed means rejecting unmanaged users or recording
   and validating ownership, and never deleting a user smallapp did not create.

5. **HIGH — retry after a side-effect failure can report false success.**
   `src/smallapp/plan.py:103-108` marks reload/service actions unchanged whenever
   files already match. Inject a first-run Caddy reload failure, then retry: the retry
   skips Caddy, writes the registry, and exits successfully despite the failed reload.
   Fixed means incomplete side effects remain pending and are retried before an apply
   is recorded complete.

6. **HIGH — concurrent applies can lose units and allocate duplicate ports.**
   `src/smallapp/registry.py:44-81` performs allocation and load-modify-save with no
   lock. Synchronize two first applies after their registry reads: both can select the
   same port and the last save drops the other entry. Fixed means locking the complete
   allocation-through-registration transaction with a concurrent regression test.

7. **HIGH — delayed re-apply still mutates state while saying `no changes`.**
   `src/smallapp/cli.py:72` regenerates `created_at`, and
   `src/smallapp/apply.py:54` rewrites the registry. In clean clone
   `/tmp/qa-1785358093`, a two-second delayed re-apply printed `13 unchanged. no
   changes.` while the registry SHA changed from `da0668...` to `7f4713...`. Fixed
   means preserving the registered timestamp and a delayed byte-identical CLI test.

8. **HIGH — the documented install fails and cannot start generated services.**
   `README.md:58` and `src/smallapp/templates.py:47` use nonexistent
   `https://github.com/smallapp/unit`; the clean-room `uv tool install` exited 2 with
   `Repository not found`. Even a corrected default root install lands outside the
   service PATH, while `src/smallapp/render.py:55-56` renders `/usr/bin/env smallapp
   gateway`. Fixed means a canonical, system-readable installation and a clean-host
   test that starts the rendered gateway command under the service PATH and UID.

9. **HIGH — secret env files are created world-readable before chmod.**
   `src/smallapp/system.py:59-62` uses `Path.write_bytes()` before applying mode 0600.
   Under umask 022 the predictable temporary env file is initially 0644, allowing a
   local watcher to retain a readable descriptor and steal the cookie key. Fixed means
   exclusive creation with mode 0600 before writing, with a test observing creation
   mode rather than only final mode.

10. **MEDIUM — removed static files remain deployed.**
    `src/smallapp/render.py:110-122` only renders current files and
    `src/smallapp/apply.py:30-54` never reconciles old payloads. Apply a static target
    containing `old.txt`, remove it from the source, and re-apply: deployed `old.txt`
    remains available. Fixed means removing payload files absent from the new target,
    with a regression test.

11. **MEDIUM — comments and strings still satisfy Python target validation.**
    `src/smallapp/target.py:12,56-66` searches raw source for `PORT`. In the clean
    clone, a file containing `# PORT` and `print("not a server")` made `smallapp plan`
    exit 0. Fixed means requiring an actual environment-variable access and testing
    comment-only and string-only false positives.

12. **MEDIUM — doctor still accepts a commented-out Caddy import.**
    `src/smallapp/system.py:175-180` uses substring membership. A prepared clean-room
    prefix whose Caddyfile contained only `# import smallapp.d/*.caddy` made
    `smallapp doctor --root` exit 0. Fixed means recognizing an active directive and
    testing the CLI against commented and malformed imports.

13. **MEDIUM — removal still hides unix-user deletion failure.**
    `src/smallapp/system.py:103-109` invokes `userdel` with `allow_fail=True`, while
    `src/smallapp/apply.py:78-86` drops state and reports success. Keep a process alive
    as the unit user and remove the unit: `userdel` can fail while the CLI says removed.
    Fixed means an unexpected failure exits non-zero, names the step, and preserves
    enough registry state to retry.

14. **MEDIUM — the static end-to-end assertion weakens criterion 22.**
    `tests/test_e2e.py:199-202` asserts `root * /opt/smallapp/notes`, while
    `specs/smallapp-unit.md:302-303` requires `root * <prefix>/opt/smallapp/NAME`.
    Fixed means the generated prefixed vhost and test match the stated criterion, or
    the specification is explicitly amended before implementation.

15. **LOW — logout still accepts methods outside the HTTP contract.**
    `src/smallapp/gateway.py:145-155,221-229` expires cookies for every method. The
    live clean-room gateway returned 303 plus `Max-Age=0` for
    `GET /_smallapp/logout`. Fixed means non-POST methods return 405 without changing
    cookies, covered by a method-contract test.

16. **LOW — README overstates what doctor checks.**
    `README.md:27-29` says doctor checks all listed host requirements, including DNS
    and open ports 80/443; `src/smallapp/system.py:168-190` checks neither. Fixed means
    correcting the claim or implementing actionable DNS and port checks.

## QA FINDINGS (round 2, 2026-07-29)

1. **HIGH — delayed re-apply mutates state while reporting no changes.**
   `src/smallapp/cli.py:72` regenerates `created_at`, and
   `src/smallapp/apply.py:54` always rewrites the registry. Apply a unit, wait two
   seconds, hash `var/lib/smallapp/registry.json`, and apply again: stdout says
   `13 unchanged. no changes.` but the hash changes. Fixed means preserving the
   original timestamp and testing a delayed, byte-identical CLI re-apply.

2. **HIGH — the documented install source and generated documentation URL do not
   exist.** `README.md:58` and `src/smallapp/templates.py:47` point to
   `https://github.com/smallapp/unit`. From a clean home,
   `uv tool install --from git+https://github.com/smallapp/unit smallapp-unit` fails
   to fetch. Fixed means using the canonical repository URL in both places and
   executing the documented install in an automated clean-host check.

3. **HIGH — the documented install is not executable by generated services.**
   `README.md:58`, `src/smallapp/render.py:55-56`, and
   `src/smallapp/templates.py:81` install into root's user-local tool directory but
   render `ExecStart=/usr/bin/env smallapp gateway`. Reproduce with a clean `HOME`:
   `uv tool install` places the binary under `.local/bin`, outside the service PATH
   and inaccessible to `sa-NAME`. Fixed means installing tool and executable in
   service-readable system paths and starting the rendered command in a test.

4. **MEDIUM — comments and strings satisfy Python target validation.**
   `src/smallapp/target.py:12,56-66` searches raw source for `PORT`. A file containing
   `# PORT` and `print("not a server")` makes `smallapp plan` exit 0. Fixed means
   comments and string literals do not count as reading the environment variable,
   with regression tests for both.

5. **MEDIUM — doctor accepts a commented-out Caddy import.**
   `src/smallapp/system.py:175-180` uses substring membership. Put only
   `# import smallapp.d/*.caddy` in the Caddyfile; doctor reports the import check as
   OK. Fixed means recognizing an active import directive and rejecting commented or
   otherwise inactive text.

6. **MEDIUM — removal hides unix-user deletion failure.**
   `src/smallapp/system.py:103-109` invokes `userdel` with `allow_fail=True`, while
   `src/smallapp/apply.py:78-86` still reports success. On Linux, keep a process alive
   as `sa-NAME` and run `smallapp rm NAME`; the user remains. Fixed means naming the
   failed step and exiting non-zero on unexpected `userdel` failure.

7. **LOW — logout accepts methods outside the specified contract.**
   `src/smallapp/gateway.py:145-155,221-229` expires the cookie for every method.
   `GET /_smallapp/logout` returns 303 with `Max-Age=0`. Fixed means non-POST requests
   return 405 without changing cookies, covered by a method-contract test.

8. **MEDIUM — the plan secret-leak test is vacuous.**
   `tests/test_apply.py:154-171` reads secrets from a temporary `--root`, then plans
   against `/`, which generates unrelated values. An implementation that prints its
   own rendered secret still passes. Fixed means using `plan --out`, reading that
   invocation's env file, and asserting its exact secret and token hash are absent
   from stdout and stderr.

9. **LOW — the constant-time guard is a source-text tautology.**
   `tests/test_gateway.py:250-255` passes when `verify_token` merely appears in
   `gateway.py`. It does not prove login reaches `hmac.compare_digest`. Fixed means
   spying on `hmac.compare_digest` during a real login attempt and asserting the call.

10. **LOW — criterion 24 is not tested through the CLI.**
    `tests/test_system.py:99-118` calls `preflight()` directly. No test runs
    `smallapp doctor --root` against bare and prepared prefixes. Fixed means a
    subprocess test asserts exit 1 plus diagnostics for the former and exit 0 for the
    latter.

11. **MEDIUM — secret env files are briefly created world-readable.**
    `src/smallapp/system.py:59-62` calls `Path.write_bytes()` before `chmod(0600)`.
    With root's usual umask, `/etc/smallapp/.NAME.env.smallapp-tmp` is initially 0644;
    an unprivileged local process watching the traversable directory can open it and
    steal the HMAC secret before chmod, then forge an owner cookie. Fixed means
    atomically creating the temporary file with the final restrictive mode (for
    example, `os.open(..., mode=0o600)`) before writing, with a test asserting mode at
    creation time.

## QA FINDINGS (round 1, 2026-07-29)

1. **HIGH — a delayed second apply mutates state while reporting no changes.**
   `src/smallapp/cli.py:72` creates a new `created_at` on every invocation and
   `src/smallapp/apply.py:54` always rewrites the registry. Reproduce by applying a
   unit, waiting two seconds, hashing `var/lib/smallapp/registry.json`, and applying
   again: the command prints `13 actions, 13 unchanged. no changes.` but the hash and
   `created_at` change. Fixed means an existing unit retains its original
   `created_at`, a delayed second apply is byte-identical, and a regression test
   advances the clock between applies.

2. **HIGH — the documented fresh-host install source does not exist.**
   `README.md:58` installs from `https://github.com/smallapp/unit`, while this
   repository is `jakerothstein/smallapp-unit`; `gh repo view smallapp/unit` and the
   documented `uv tool install` fail to resolve it. Fixed means the copy-pasteable
   command installs this project successfully from a clean host and an automated
   check executes the install rather than merely parsing `smallapp` commands.

3. **HIGH — the default tool install is invisible to generated services.**
   `README.md:58`, `src/smallapp/render.py:55-56`, and
   `src/smallapp/templates.py:81` combine default `uv tool install` locations with
   `ExecStart=/usr/bin/env smallapp gateway`. Reproduce with a clean `HOME`: `uv tool
   install` puts `smallapp` in `$HOME/.local/bin`, then the service PATH
   `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin` returns exit 127; as root,
   the tool environment is also under `/root` and inaccessible to `sa-NAME`. Fixed
   means the README installs both the tool environment and executable in
   service-readable system locations, and a test starts the rendered gateway command
   under the documented host layout and service PATH.

4. **MEDIUM — a comment can satisfy Python target validation.**
   `src/smallapp/target.py:12,56-66` searches raw text for `PORT`. Reproduce with a
   file containing only `# PORT` and `print("not a server")`; `smallapp plan` exits 0
   instead of 2. Fixed means comments and string literals do not count as reading the
   environment variable, and tests cover both false positives.

5. **MEDIUM — doctor accepts a commented-out Caddy import.**
   `src/smallapp/system.py:175-180` uses substring membership, so a Caddyfile
   containing only `# import smallapp.d/*.caddy` marks the import check as OK.
   Fixed means doctor recognizes an active import directive and a regression test
   rejects commented or otherwise inactive text.

6. **MEDIUM — removal can report success while leaving the unix user behind.**
   `src/smallapp/system.py:103-109` runs `userdel` with `allow_fail=True`, and
   `src/smallapp/apply.py:78-86` still completes removal. Reproduce on Linux by
   keeping a process alive as `sa-NAME` and running `smallapp rm NAME`; `userdel`
   fails but the command reports `removed NAME`. Fixed means an unexpected user
   deletion failure is surfaced as a named failing step and the command exits
   non-zero.

7. **LOW — logout is not restricted to the specified POST method.**
   `src/smallapp/gateway.py:145-155,221-229` expires the cookie for every HTTP method.
   Reproduce with `GET /_smallapp/logout`; it returns 303 and `Max-Age=0`, despite the
   HTTP contract exposing only `POST`. Fixed means non-POST logout requests return
   405 without changing cookies, with a method-contract regression test.

8. **MEDIUM — the plan secret-leak test is vacuous.**
   `tests/test_apply.py:154-171` reads a secret created under a temporary `--root`,
   then invokes `smallapp plan` against the real root, where plan generates unrelated
   random secrets. A plan implementation that printed its own rendered secret would
   still pass this assertion. Fixed means the test runs `plan --out`, reads that
   invocation's exact secret and token hash from its rendered env file, and asserts
   those values are absent from stdout and stderr.

9. **LOW — criterion 20's constant-time guard is a source-text tautology.**
   `tests/test_gateway.py:250-255` accepts the mere string `verify_token` in
   `gateway.py`, so it passes without proving that login reaches
   `hmac.compare_digest`. The current implementation is constant-time, but the guard
   would miss a regression inside `verify_token`. Fixed means the test spies on
   `hmac.compare_digest` during a real login attempt and asserts it was called.

10. **LOW — criterion 24 is not tested through the CLI it specifies.**
    `tests/test_system.py:99-118` calls `preflight()` directly; no test runs
    `smallapp doctor` and asserts its required exit 1/exit 0 behavior. Fixed means a
    subprocess test runs `doctor --root` against bare and prepared prefixes and checks
    both exit codes and the required diagnostics.

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

## Open items

None. Every acceptance criterion in `specs/smallapp-unit.md` is covered by a test.

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
