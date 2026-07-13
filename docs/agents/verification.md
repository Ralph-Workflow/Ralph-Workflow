# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

The Sphinx docs build (`make docs`) is wired in as a Make prerequisite of `make verify` with `-W --keep-going` so any Sphinx warning fails the gate; it runs before the Python verify step and stays outside the immutable 60-second combined test budget.

## No unrelated-failure exemption — you find it, you fix it

`make verify` must pass **in full**, and there is NO exemption for a failure your change did not cause. "It was already failing on `main`", "that gate is unrelated to what I touched", and "that check isn't run by `make test`" are not acceptable outcomes — a red gate is a red gate, and whoever next observes it owns fixing it.

**Do not investigate who caused it.** Stashing your changes, bisecting, or re-running against a clean tree just to prove a failure is "pre-existing" is almost never useful work — the answer does not change what you do next, which is fix it. Chase provenance only when it is genuinely diagnostic (the triggering change tells you what the bug *is*), never to decide whether the failure is yours to own. It is always yours to own.

**Preventing regressions outranks finishing the task in hand.** If a pre-existing failure blocks you, fix the failure first and finish your original task afterwards. Never report your own work as verified while any gate is red; if a repair is genuinely out of scope, stop and surface it as an active blocker rather than working around it.

A corollary for anyone adding a check: **wire it into `make verify`.** A check that lives only in an opt-in suite the default gate skips will rot silently. `audit_repo_structure` exists precisely because its rules previously lived only in `tests/integration/test_policy_file_rules.py`, which is `subprocess_e2e`-marked and therefore excluded from `make test` — the policy decayed for weeks while the gate stayed green.

## Fabrication guardrails

Any edit to a public-facing markdown file (README, USERS.md, docs/, the Sphinx operator manual) is in scope for the [fabrication guard](fabrication-guard.md). USERS.md is the single canonical community directory; the previous near-duplicate community surfaces (`SHOWCASE.md`, `ECOSYSTEM.md`, `COMPARISONS.md`, `CREDIT_TEMPLATE.md`) were removed in the 2026-07-07 docs cleanup. The guard runs as a pre-commit hook at Level 1; re-run it explicitly with `./scripts/fabrication_guard.py --level 1 <file>` or `--level 2 <file>` for network existence checks. Bypassing the guard (for example with `--no-verify`) is itself fabrication.

## Total test budget — 60 seconds, ABSOLUTE and IMMUTABLE

The 60-second combined total test budget is **absolute and immutable**. It cannot be changed, overridden, or circumvented. `make verify` runs `make test`, which executes one maintained parallel pytest invocation over `tests/` with `-m "not subprocess_e2e"`.

`ralph/verify.py` enforces these import-time invariants (using `if`/`raise RuntimeError`, NOT `assert`, so they survive `python -O`):

- `_TOTAL_TEST_BUDGET_SECONDS > 0` (must be positive)
- `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` (epsilon check on the 60.0 constant)
- `_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS`
- Every budget-tracked step has a positive timeout
- `_VERIFY_STEP_TIMEOUT_SECONDS > 0` and `>= 5.0` (non-trivial per-step cap)
- `_KNOWN_TEST_STEP_LABELS` and `_BUDGET_TRACKED_STEPS` are non-empty
- `'make test'` is present in `_KNOWN_TEST_STEP_LABELS`
- Every label in `_KNOWN_TEST_STEP_LABELS` is tracked; every tracked step has its label

All invariants are tested in `tests/test_verify_invariants.py` under both normal execution and `python -O`. A timeout failure is a test design defect — fix the production coupling, never adjust the budget. Per-suite caps (`PYTEST_SUITE_TIMEOUT_SECONDS`, `DEFAULT_SUITE_TIMEOUT_SECONDS`) are SECONDARY; raising them does NOT increase the combined budget. The cumulative tracker sums time across ALL budget-tracked steps via `time.monotonic()`. See [AGENTS.md §'═══ ABSOLUTE TEST BUDGET — 60s, IMMUTABLE ═══'](../../AGENTS.md) for the canonical non-circumvention table.

## Non-circumvention table

The following do **NOT** circumvent the 60-second combined budget or the lint/typecheck enforcement (detected by the corresponding audit module under `ralph/testing/audit_*.py`):

| Attempted circumvention | Why it fails |
|---|---|
| Splitting tests into N suites | Cumulative tracker sums time across ALL tracked steps — not per-suite |
| Moving slow tests to a different suite/target | All budget-tracked steps count toward the same combined budget |
| Renaming test targets or adding new test-related `_VERIFY_STEPS` without updating `_KNOWN_TEST_STEP_LABELS` | `_BUDGET_TRACKED_STEPS` stays frozen — new targets do not get budget tracking |
| Raising `PYTEST_SUITE_TIMEOUT_SECONDS` / `DEFAULT_SUITE_TIMEOUT_SECONDS` | Per-suite cap only; combined budget enforced separately by `_TOTAL_TEST_BUDGET_SECONDS` |
| Setting `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` / `RALPH_PYTEST_TEST_TIMEOUT_SECONDS` | Env vars only adjust per-suite/per-test caps, not the combined budget |
| Modifying `_BUDGET_TRACKED_STEPS` / raising `_TOTAL_TEST_BUDGET_SECONDS` | Blocked by import-time `if`/`raise RuntimeError` checks — immune to `python -O` |
| Emptying `_KNOWN_TEST_STEP_LABELS` / `_BUDGET_TRACKED_STEPS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time containment RuntimeError check — immune to `python -O` |
| Adding `per-file-ignores` / `extend-per-file-ignores` / blanket `# noqa` | Detected by `ralph/testing/audit_lint_bypass.py` |
| Adding `ignore_missing_imports`, `disable_error_code`, blanket `# type: ignore` | Detected by `ralph/testing/audit_typecheck_bypass.py` |
| Using `time.sleep()`, real subprocess, real file I/O in non-`subprocess_e2e` tests | Detected by `ralph/testing/audit_test_policy.py` |
| Unbounded `subprocess.run` / `httpx.*` / `urlopen` / `socket.create_connection` (no `timeout=`) in `ralph/mcp/`, `ralph/git/`, `ralph/process/manager/` | Detected by `ralph/testing/audit_mcp_timeout.py` |
| Mutable collection literals (`list` / `dict` / `set` / `deque`) assigned to module-level names or `self.X` in `__init__` without `maxlen=` or a justified `# bounded-accumulator-ok:` marker | Detected by `ralph/testing/audit_resource_lifecycle.py` |

Every circumvention above is detected by `make verify`. Any bypass requires a documented justification and an entry in the audit allowlist — there is no other path. See [AGENTS.md §'Non-negotiables'](../../AGENTS.md) for the full policy text.

## Smoke-check subsections

Use these focused commands when a smoke check is required for the area you are touching. Each command lives outside the budget-tracked combined budget (per-suite caps only) so they do not inflate the 60-second gate.

```bash
# Policy loader smoke check (after changing policy defaults)
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

# Parallel-mode regression tests (work-units / namespaced payloads)
uv run pytest -q tests/test_parallel_mode_docs_banned_phrases_across_all_docs.py tests/test_parallel_mode_docs_namespaced_payload_docs.py

# Parallel worker bootstrap tests
uv run pytest -q tests/test_parallel_worker_runtime.py tests/integration/test_parallel_worker_bootstrap.py

# Interactive Claude PTY tests
uv run pytest -q tests/test_process_manager_pty.py tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py

# Recovery tests
uv run pytest -x tests/recovery/ tests/test_recovery_first_invariant.py tests/test_reducer.py tests/test_pipeline_runner.py
```

For full verification including docs and subprocess E2E:

```bash
cd ralph-workflow
make docs
make test-subprocess-e2e
```

Verification passes only when all checks complete with **no ERROR/WARNING diagnostics**. If any step fails, fix the issue immediately and rerun. `make verify` emits a high-visibility failure banner that cites `AGENTS.md`.

## Terminal escape containment

The terminal-escape containment contract keeps interactive agents (Claude Code, Cursor, etc.) from blanking Ralph's screen or overwriting live log lines. It is enforced by `ralph/testing/audit_terminal_escape_containment.py` (a `make verify` step) and consists of three pinning rules plus one exception:

- **Single-stripper rule.** `ralph/display/line_sanitizer.py::strip_terminal_control` is the canonical terminal-control remover. The full CSI parameter-byte range `[0-?]` matches every valid sequence (alternate screen `ESC[?1049h`, erase display `ESC[2J`, private-parameter CSI `ESC[>0c` and `ESC[<35;1;2M`, OSC titles, two-character ESC). No other module may define a second, narrower regex — the audit rejects any file that reintroduces the SGR-only `[0-9;]*m` form or the digit-only `[0-9;?]` class. Defence-in-depth: `ralph/agents/invoke/_pty_line_reader.py` must keep yielding raw VT text (its `yield queued_line` line is an audit invariant) because interactive permission auto-approval parses that raw stream — sanitizing at the source would silently break it.

- **Single-painter rule.** Every loguru terminal log record goes through `ralph.display.log_sink` (either `make_sanitizing_log_sink` for the DisplayContext Console, or `make_stderr_log_sink` for the library/worker fallback), never through a raw `logger.add(sys.stderr, ...)`. The CLI (`ralph/cli/main.py::_configure_logging`) takes an injected `console_sink` keyword; `ralph/cli/main.py::main` wires the Console-backed sink at the call site so the rich `Live` status bar is the only painter of Ralph's terminal. Sanitization happens through `strip_terminal_control`; the sink prints via `ctx.console.print(text, markup=False, highlight=False)` so bracketed paths and `[bold]` tokens survive verbatim. Module construction of `rich.console.Console` outside `ralph/display/theme.py` is forbidden by `tests/display/test_di_invariants.py`; the audit enforces the same rule on `ralph.display.log_sink` itself.

- **Background-spawn rule.** `SpawnOptions.stdin` defaults to `subprocess.DEVNULL` (the dataclass field in `ralph/process/manager/_spawn_options.py`) so no child inherits Ralph's controlling-terminal stdin by construction. Callers that genuinely need stdio (`ralph/mcp/protocol/transport.py:270` for the stdio MCP transport and `ralph/mcp/upstream/_stdio_upstream_client.py:122` for the stdio upstream client) already pass `subprocess.PIPE` explicitly. The audit additionally enforces that NO `SpawnOptions(...)` call site anywhere under `ralph/` may pass `stdin=None` (the package-wide `PackageWideCallSiteInvariant` walks every `*.py` file and rejects the INHERIT literal). PTY children get their own session: `ralph/process/pty.py::spawn_pty_process` calls `os.setsid()` plus `fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)` inside the child branch — removing either fails the audit.

- **Exception: keep the source reader raw.** `ralph/agents/invoke/_pty_line_reader.py` and `ralph/agents/invoke/_process_reader.py` keep yielding raw bytes / queued lines so the stream that auto-approves Claude Code's permission prompts is unchanged. Sanitization must happen ONLY at the display boundary (`display/parallel_display.py`, `display/activity_model.py`, the new `display/log_sink.py`) — never at the source.

Enforcing command:

```bash
uv run python -m ralph.testing.audit_terminal_escape_containment
```

This audit is the LAST entry in `ralph/verify.py::_VERIFY_STEPS`; it is not budget-tracked (exempt from the 60-second combined test budget) and it runs on every `make verify`. The audit's invariant count is pinned by `tests/test_audit_terminal_escape_containment.py::test_audit_invariant_count_matches_table` — adding an invariant requires bumping that count in the same change.

## Cross-links

- `ralph/verify.py` — budget tracker, `_VERIFY_STEPS`, invariant checks
- `ralph/testing/audit_*.py` — per-audit machinery (see each docstring for the per-audit invariant list)
- [AGENTS.md §'Non-negotiables'](../../AGENTS.md) — canonical policy and circumvention table
- [Testing Guide](testing-guide.md) — test design rules and required doubles
- [Documentation Rubric](../code-style/documentation-rubric.md) — for any docs/README/manual change

If the change touches README, docs, START_HERE, the manual, or any public-doc route, read [Documentation Rubric](../code-style/documentation-rubric.md) first and check the edited surface against it before calling the docs work done.