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
