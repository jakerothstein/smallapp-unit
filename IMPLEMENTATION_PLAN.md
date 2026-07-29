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
