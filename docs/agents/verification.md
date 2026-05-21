# Required Verification (before PR/completion)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


## Canonical command

```bash
cd ralph-workflow
make verify
```

`make verify` runs:
1. `make lint` (`ruff check`)
2. `make typecheck` (`mypy --strict`)
3. pytest (parallel, excludes `subprocess_e2e`)

Verification passes only when all checks complete with **no ERROR/WARNING diagnostics**. If any step fails, fix the issue immediately and rerun. `make verify` emits a high-visibility failure banner that cites `AGENTS.md` and `CLAUDE.md`.

If the change touches README, docs, START_HERE, the manual, or any public-doc route, read `docs/code-style/documentation-rubric.md` first and check the edited surface against it before calling the docs work done.

For full verification (including docs build and subprocess E2E tests):

```bash
cd ralph-workflow
make docs
make test-subprocess-e2e
```

---

## Individual commands

```bash
cd ralph-workflow
make lint
make typecheck
make test
make test-unit
make test-integration
make test-cov
make docs
uv run ruff check ralph/ tests/
uv run ruff format --check ralph/ tests/
uv run python -m mypy ralph/
python -m ralph --help
python -m ralph --version
```

`make test` runs the full non-`subprocess_e2e` suite in timeout-guarded shards. `make test-unit` excludes `tests/integration/`. `make test-cov` enforces an 80% coverage gate. `make docs` builds Sphinx HTML with warnings as errors.

---

## Dead-code audit

```bash
cd ralph-workflow
make dead-code
```

Runs Vulture with `pyproject.toml` config. Intentionally separate from `make verify` while known dead code remains. Non-zero exit until the backlog is cleared.

---

## Policy loader smoke check

```bash
cd ralph-workflow
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"
```

Run after changing policy defaults or loader code.

---

## Parallel-mode tests

```bash
cd ralph-workflow
uv run pytest -q tests/integration/test_parallel_resume.py
uv run pytest -q tests/integration/test_runner_fanout_wiring.py
uv run pytest -q tests/integration/test_old_checkpoint_loads.py
```

## Interactive Claude PTY tests

```bash
cd ralph-workflow
uv run pytest -q tests/test_process_manager_pty.py
uv run pytest -q tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py
uv run pytest -q tests/integration/test_claude_interactive_pty_e2e.py tests/integration/test_claude_interactive_interrupt_realtime.py
```

## Manual interactive Claude smoke test

**Not part of `make verify`** — consumes live agent tokens. Validates the interactive TUI interpreter surfaces expected semantic signals.

```bash
cd ralph-workflow
python -m ralph smoke-interactive-claude
```

What it does:
- writes a smoke prompt under `tmp/interactive-claude-smoke/`
- asks `claude/haiku` to create a JavaScript todo list
- prints a detailed report of what worked and what broke
- uses the headless Claude contract as a guide for expected semantics (session capture, tool activity, completion signal, parser events, tmp/ artifact creation)

## Recovery tests

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

All recovery tests run in under 10 seconds each with injectable fake clocks, fake sleep, and fake probes — no real network I/O.
