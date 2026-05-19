# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

Verification passes only when all checks complete successfully with **no ERROR/WARNING diagnostics**.

`make verify` runs:
1. `make lint` (`ruff check`)
2. `make typecheck` (`mypy --strict`)
3. pytest (direct invocation, parallel, excludes `subprocess_e2e`)

For full verification (including docs build and subprocess E2E tests), run:
```bash
cd ralph-workflow
make docs
make test-subprocess-e2e
```

If any step fails, fix the issue immediately and rerun verification.
`make verify` now emits a high-visibility failure banner that explicitly cross-references `AGENTS.md` and `CLAUDE.md` so AI agents cannot plausibly treat a failed verification run as optional.

---

## Dead-code audit

```bash
cd ralph-workflow
make dead-code
```

`make dead-code` runs Vulture with the repository's `pyproject.toml` configuration and is intentionally separate from `make verify` while the repo still contains known dead code. Today it is a validation and cleanup command: the expected behavior is a non-zero exit until the backlog is removed.

---

## Individual commands (when narrowing failures)

```bash
cd ralph-workflow
uv run ruff check ralph/ tests/
uv run ruff format --check ralph/ tests/
uv run python -m mypy ralph/
make test
make test-unit
make test-integration
make test-cov
make docs
python -m ralph --help
python -m ralph --version
```

`make test` runs the full non-`subprocess_e2e` suite in multiple timeout-guarded slices so every pytest invocation fails fast after 30 seconds: grouped package directories first, then alphabetical root-level test shards, then `tests/integration/`. `make test-unit` uses the same sharded root/unit slices and excludes `tests/integration/`. `make test-integration` runs only `tests/integration/`. `make test-cov` is the authoritative covered suite; it runs all pytest shards with coverage and enforces an 80% coverage gate. It is not part of the fast `make verify` chain due to the 30-second hard budget, but is required before any PR submission. `make test-subprocess-e2e` also runs under the same 30-second suite timeout wrapper. In addition, repo-local raw `pytest` runs now auto-load `ralph.testing.pytest_timeout_plugin` from `tests/conftest.py`, which pins the local checkout to the front of `sys.path` before registering the plugin so direct invocations inherit the same hard 30-second suite cap even when another Ralph checkout is present on the interpreter path. Use `uv run ruff ...` for direct Ruff repro commands so manual lint/format checks match the same uv-managed Ruff toolchain that `make verify` uses. `make docs` builds the Sphinx HTML docs into `docs/sphinx/_build/html` with warnings treated as errors.

---

## Policy loader smoke check

```bash
cd ralph-workflow
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"
```

Run this smoke check whenever policy defaults or loader code changes.

---

## Parallel-mode tests

When working on or verifying the parallel development feature:

```bash
cd ralph-workflow
uv run pytest -q tests/integration/test_parallel_resume.py
uv run pytest -q tests/integration/test_runner_fanout_wiring.py
uv run pytest -q tests/integration/test_old_checkpoint_loads.py
```

## Interactive Claude PTY tests

When working on or verifying the PTY-backed interactive Claude runtime:

```bash
cd ralph-workflow
uv run pytest -q tests/test_process_manager_pty.py
uv run pytest -q tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py
uv run pytest -q tests/integration/test_claude_interactive_pty_e2e.py tests/integration/test_claude_interactive_interrupt_realtime.py
```

## Manual interactive Claude smoke test

This smoke test is **not** part of `make verify` because it consumes live agent tokens. Use it when you want a real `claude/haiku` PTY run to validate that the interactive TUI interpreter still surfaces the same semantic signals Ralph expects from the headless contract.

```bash
cd ralph-workflow
python -m ralph smoke-interactive-claude
```

What it does:
- writes a tiny smoke prompt under `tmp/interactive-claude-smoke/`
- asks `claude/haiku` to create a small JavaScript todo list in `tmp/interactive-claude-smoke/todo-list.js`
- prints a detailed report of what worked and what broke
- uses the headless Claude contract as a **guide** for expected semantics (session capture, tool activity, completion signal, parser events, tmp/ artifact creation) without running headless Claude itself

## Recovery tests

When working on or verifying the failure recovery feature:

```bash
cd ralph-workflow
uv run pytest -x tests/recovery/
uv run pytest -x tests/test_recovery_first_invariant.py tests/test_reducer.py tests/test_pipeline_runner.py tests/test_asyncio_bridge.py tests/test_checkpoint.py
```

Recovery tests cover:
- Network loss / connectivity monitoring (environmental failures do not count against budget)
- Agent empty output / idle timeout (agent failures count against budget, trigger fallover)
- Agent chain exhaustion (full chain → PHASE_FAILED, fallover history populated)
- Recovery cycle cap (global cap prevents infinite loops, emits ExitFailureEffect)
- Worker failures and merge conflicts (do not terminate pipeline, route through recovery)
- Pre-flight validation (invalid chain / missing agent caught at startup with exit code 2)
- User interrupt (first SIGINT → ordered shutdown; second → os._exit(130))

All recovery tests run in under 10 seconds each and use injectable fake clocks, fake sleep, and fake probes — no real network I/O in tests.
