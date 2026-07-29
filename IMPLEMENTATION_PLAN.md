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

## 5. Render

`templates.py` + `render.py`: pure `(Target, Unit) -> dict[path, RenderedFile]`.
App unit, gateway unit, vhost, env file, payload. Golden files under `tests/golden/`.
`smallapp plan --out DIR` writes them. Directive-by-directive assertions.
— closes (8), (9), (10).

## 6. Registry

`registry.py`: JSON read/write with atomic replace, unit records, port collision
probing. Wire into `plan`/`status`.
— closes rest of (7), (23).

## 7. Plan + System seam

`system.py` (root-prefix-aware write/chmod/chown/user/systemctl/caddy_reload) and
`plan.py` (rendered files + current fs → ordered actions with create/change/unchanged
state). `smallapp plan` prints the real action list.
— closes (11).

## 8. Apply and rm

`apply.py`: execute actions, print `+ ~ =`, print the one-time token, fail loudly
naming the failing step. `smallapp rm` reverses everything.
— closes (12), (13), (14), (15).

## 9. End to end

`test_e2e.py`: apply into a temp root, launch the rendered app and gateway as real
subprocesses from the rendered env file, walk the full login path, then the static
variant. Fix whatever this finds — it will find something.
— closes (21), (22).

## 10. Doctor + hygiene

`doctor` checks; `test_hardening.py` with `systemd-analyze security` (skip with a
reason off Linux); no-TODO test; README command-parse test; README quickstart from
bare VPS to private URL.
— closes (24), (26), (27), (28), and (1) fully.

## Notes for later loops

- Anything not in `§ Acceptance criteria` is out of scope. If you want it, amend the
  spec first, in the same commit.
- The `System` seam is the only place `subprocess` and `os.chown` may appear. Keeping
  it that way is what makes everything else testable on a laptop.
- The gateway proxies non-`/_smallapp/*` paths itself. In production Caddy already
  routes around it; the proxy path exists so slice 9 can test the real login flow.
- Real-host verification (a live VPS with Caddy and systemd) is a manual step
  documented in the README, not a test. Criterion 26 is the automated stand-in.
