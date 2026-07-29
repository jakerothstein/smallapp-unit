# Implementation plan

Ordered vertical slices. Each ends with `uv run pytest && uv run ruff check . && uv run mypy src tests`
green. Numbers in parentheses are the acceptance criteria from `specs/smallapp-unit.md`
that the slice closes. Do not start a slice before the one above it is green.

## 0. Skeleton (done)

`pyproject.toml`, package, stub test, README, `.gitignore`. Bootstrap/test/lint/typecheck
all pass. — closes (1) partially, (2), (3).

## 1. `smallapp --help` runs end to end

`cli.py` with argparse and all six subcommands wired to functions that print a
one-line "not yet" and exit 3; `__main__.py`; console script entry point.
Test: `--help` exit 0, names all six commands; each subcommand is reachable.
— closes (4).

## 2. Target detection

`target.py` + `naming.py`: classify python vs static, validate names, deterministic
port allocation (registry-free path first). Wire `plan` to print the detected kind
and port. Test each accept and each rejection message.
— closes (5), (6), part of (7), (25).

## 3. Tokens

`tokens.py`: `generate_secret`, `generate_token`, `hash_token`/`verify_token`
(salted scrypt), `sign_cookie`/`verify_cookie` (hmac-sha256, `v1.` prefix, exp).
Pure module, no I/O. Test all six negative verify cases.
— closes (16), (17), and the `compare_digest` half of (20).

## 4. Gateway

`gateway.py`: stdlib `ThreadingHTTPServer`, the six-row HTTP contract, env config
with loud failure on missing `SMALLAPP_SECRET`/`SMALLAPP_TOKEN_HASH`. `smallapp
gateway` now really runs it. Test drives a live server on an ephemeral port.
This is the first slice a human can *see* work: run it, log in, get a cookie.
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
- Real-host verification (a live VPS with Caddy and systemd) is a manual step
  documented in the README, not a test. Criterion 26 is the automated stand-in.
