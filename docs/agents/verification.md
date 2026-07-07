# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

## Fabrication guardrails

Any time you edit a public-facing markdown file (README, USERS.md,
docs/, the Sphinx operator manual), the
[fabrication guard](fabrication-guard.md) is part of the contract.
The previous near-duplicate community surfaces (`SHOWCASE.md`,
`ECOSYSTEM.md`, `COMPARISONS.md`, `CREDIT_TEMPLATE.md`) were
deleted in the 2026-07-07 docs cleanup; USERS.md is the single
canonical community directory.
The guard runs as a pre-commit hook at Level 1 and can be re-run
explicitly with `./scripts/fabrication_guard.py --level 1 <file>`
or `--level 2 <file>` for network existence checks. Fabrication
bypassing the guard (for example with `--no-verify`) is itself
considered fabrication.

`make verify` runs (in this order, with the docs prerequisite wired in via the Makefile):
0. `make docs` — Sphinx HTML build with `-W` (warnings-as-errors). Runs **before** the python verify step as a Make prerequisite (it is NOT registered in `_VERIFY_STEPS`, so it stays outside the immutable 60-second combined test budget). Any Sphinx warning now fails `make verify`.
1. `make lint` (`ruff check`)
2. `make typecheck` (`python -m mypy ralph/` — strict mode is enabled by `ralph-workflow/mypy.ini` via `strict = true`, not by the command line)
3. `make test` (pytest, parallel, excludes `subprocess_e2e`) — the ONLY step whose wall-clock time counts against the 60-second combined test budget
4. Lint bypass audit (`ralph/testing/audit_lint_bypass.py`) — detects forbidden noqa, per-file-ignores
5. Typecheck bypass audit (`ralph/testing/audit_typecheck_bypass.py`) — detects non-compliant type:ignore, mypy config weakening (including `disable_error_code`)
6. Policy audit (`ralph/testing/audit_test_policy.py`) — detects slow test patterns, I/O in tests (including `os.system` and `os.popen` subprocess calls)
7. Bounded-subprocess audit (`ralph/testing/audit_mcp_timeout.py`) — enforces the bounded-subprocess contract (below)
8. DI seam audit (`ralph/testing/audit_di_seam.py`) — enforces the Foundations dependency-injection contract: every component below the composition root must receive collaborators through its constructor or call signature, and must not reach into ambient process state (`os.environ`, `open()`) or launder the session contract through `typing.cast()` at the session factory boundary
9. Activity-aware watchdog audit (`ralph/testing/audit_activity_aware_watchdog.py`) — enforces the subagent/tool-visibility contract on `IdleWatchdog` (constructor must accept `process_monitor=`, `set_active_sink` / `set_subagent_sink` must be wired after construction, `WorkspaceMonitor.set_on_event` must be bound to a 2-arg forwarding callable, `teardown_subtree` must run on every fire and error path, `DefaultProcessMonitor` must be constructed with injected `role_classifier=` / `discovery_strategy=` / `subagent_pid_source=`)
10. Watchdog drift audit (`ralph/testing/audit_watchdog_drift.py`) — enforces the watchdog consolidation: forbids the legacy root watchdog sentinel (the dead 1389-line module that previously sat at the ralph-workflow root) at the package root, forbids duplicate `IdleWatchdog` / `PostExitWatchdog` class definitions outside their canonical owner files, and forbids `WatchdogFireReason` construction outside the same owners. The audit's forbidden-filename sentinel is constructed at import time from two private string fragments so the literal forbidden token never appears as a contiguous substring in source. See `ralph-workflow/docs/agents/watchdog-spec.md` for the full invariant list
11. Parallelization dormant audit (`ralph/testing/audit_parallelization_dormant.py`) — enforces that Ralph-managed fan-out is dormant and the agent-driven parallel model is wired (checks 9 invariants across `planning.jinja`, `developer_iteration_continuation.jinja`, `format_docs/plan.md`, `effect_router.py`, `pipeline.toml`, `planning_analysis.jinja`, `ralph-workflow/docs/sphinx/configuration.md`, and `ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md`)
12. Artifact-submission canonical-path audit (`ralph/testing/audit_artifact_submission_canonical_path.py`) — enforces the single-writer contract for artifact submission (see `docs/agents/artifact-submission-contract.md`)
13. Agent registry sync audit (`ralph/testing/audit_agent_registry_sync.py`) — enforces that built-in agent declarations, registry seeding, docs, parser exports, and dispatch tables stay synchronized
14. Agent module state audit (`ralph/testing/audit_agent_module_state.py`) — enforces that agent catalog/registry modules stay import-safe and do not grow mutable module-level state outside the approved declarations
15. Agent internal paths audit (`ralph/testing/audit_agent_internal_paths.py`) — enforces that agent internals live at canonical paths and that cross-references stay in sync
16. Social-proof gate (`scripts/verify_social_proof.py`) — detects unverifiable public-facing adoption, credit, usage, or stats claims
17. Resource lifecycle audit (`ralph/testing/audit_resource_lifecycle.py`) — enforces the resource-lifecycle contract documented in `ralph-workflow/docs/agents/memory-lifecycle.md`: every `threading.Thread(...)` call has `daemon=True`, every `httpx.Client(...)` / `httpx.AsyncClient(...)` / `requests.Session(...)` is constructed inside a `with` statement, and raw `os.open` / `os.openpty` / `os.pipe` calls are confined to `ralph/process/` (with an inline `# resource-lifecycle-ok: <reason>` escape hatch for genuinely bounded-by-design call sites). Alias resolution is honored so `import httpx as hx; hx.Client()` cannot evade detection
18. Skill auto-commit audit (`ralph/testing/audit_skill_auto_commit.py`) — enforces the deterministic wt-025 skill-update auto-commit contract: the literal subject `chore(skills): sync baseline bundle`, the FIVE canonical project-scope skill-root prefix strings (`.opencode/skills/`, `.agents/skills/`, `.claude/skills/`, `.codex/skills/`, `.gemini/antigravity-cli/skills/`), the AST placement of the early-skip block in `ralph/git/commit_cleanup.py::untrack_engine_internal_files`, the existence of `ralph/skills/_auto_commit.py`, and the failure-path best-effort contract (try/except wrapping `commit_skill_updates` and the `Skill auto-commit failed (non-fatal)` debug log literal). The audit is registered as a normal (non-budget-tracked) verify step appended at the end of `_VERIFY_STEPS`, so the immutable 60-second combined test budget remains preserved. The runtime regression pins live in `ralph-workflow/tests/test_git_commit_cleanup.py::test_untrack_engine_internal_files_does_not_warn_on_skill_symlinks` (WARNING suppression, AC-03), `ralph-workflow/tests/test_skills_auto_commit.py::test_auto_commit_body_is_deterministic_across_shuffled_input_orderings` (deterministic message shape, AC-01), `ralph-workflow/tests/test_cli_commands_run_skill_sync.py::test_install_then_auto_commit_replaces_stale_bundled_skill` (conflict-resolution overwrite, AC-02), and `ralph-workflow/tests/test_cli_commands_run_skill_sync.py::test_skill_sync_autocommits_before_agent_sees_skill_tree_drift` (agent-clean-worktree invariant, AC-05).
19. Public docstring audit (`ralph/testing/audit_public_docstrings.py`) — enforces an AST-only, import-safe module-docstring floor (presence + non-empty first line) on every public module under `ralph/`, including leaf modules AND every package `__init__.py` (PA-004). The audit walks `ralph/` via `pathlib.Path.rglob`, skipping `__pycache__`, `.venv`, `.mypy_cache`, `tmp`, `.ruff_cache`, `.pytest_cache`, `htmlcov`, `build`, `dist`, and any module whose filename starts with `_` (private). It uses `ast.parse` on file text read via `pathlib` ONLY — it never imports the modules under inspection, so it is safe to run as part of the Sphinx autodoc build. The audit registers as a non-budget-tracked step appended LAST to `_VERIFY_STEPS`; the immutable 60-second combined test budget is preserved (the audit is <1s on the real 524-file tree). It is registered in the `_EXCLUDED` dict in `tests/test_sphinx_modules_coverage.py` (mirroring the 9 sibling `testing.audit_*` entries) and is NOT documented in `docs/sphinx/modules.rst`, since the audit is test infrastructure rather than public API. The runtime contract is pinned by `ralph-workflow/tests/test_audit_public_docstrings.py` (20 black-box tests: green on the real `ralph/` tree, red on a stripped-docstring `tmp_path` tree for both leaf modules and package `__init__.py`, and private modules ignored).

All 19 steps run sequentially under `make verify`. Steps 1, 2, 4-19 each use a per-step timeout (`_VERIFY_STEP_TIMEOUT_SECONDS`); step 3 (`make test`) uses the 60-second combined budget. The full ordered list of step labels is the canonical `_VERIFY_STEPS` tuple in `ralph-workflow/ralph/verify.py`; this doc lists the user-facing name for each step and is the single source of truth for what `make verify` actually runs. Step 0 (the Sphinx docs build) is the Make prerequisite listed in `verify: verify-drift docs`; it is run with `-W --keep-going` so any warning fails the gate, but it does not count against the 60-second combined test budget because it is wired in via the Makefile, not via `_VERIFY_STEPS`.

### Bounded-subprocess contract — every MCP and git operation is bounded

No operation under `ralph/mcp/`, `ralph/git/`, **or `ralph/process/manager/`** may perform blocking I/O without a bounded, fail-closed timeout. An unbounded blocking call hangs the server/worker thread, starves the agent of stdout, and trips the idle watchdog — a real, diagnosed agent-hang vector (`git status` over large `vendor/` submodules; a network/credential git prompt with no timeout). With no explicit root argument, `ralph/testing/audit_mcp_timeout.py` (part of `make verify`) audits all three roots and statically flags, as a contract violation:

- `subprocess.run/call/check_call/check_output(...)` without `timeout=` (resolved through `import x as y` / `from x import y` aliases, so an aliased call cannot evade the check);
- `subprocess.getoutput`/`getstatusoutput` and `os.system` (these take no timeout — always flagged);
- any `.communicate(...)` / `.communicate_and_cleanup(...)` without `timeout=` (their first positional is `input`, not a timeout) and any `.wait()` without a timeout;
- network calls (`httpx.*`/`requests.*` request methods and clients, `urllib.request.urlopen`, `socket.create_connection`) without `timeout=`.

It does NOT flag `subprocess.Popen(...)` or `socket.socket(...)` (they take no `timeout=`), and is best-effort against deliberate obfuscation (assignment rebind, `getattr`, `importlib`). The only escape hatch is an inline `# mcp-timeout-ok: <reason>` marker on the call line, for a genuinely unbounded-by-design call (keep it rare and justified). The presence of the audit step in `make verify` is itself protected by an import-time invariant in `ralph/verify.py` (immune to `python -O`), so the contract cannot be silently dropped. Subprocesses must additionally fail closed: kill the process (group) on expiry and raise a clear tool error. `ralph/git/subprocess_runner.run_git` applies a default timeout (`GIT_SUBPROCESS_TIMEOUT_SECONDS`) when a caller gives none and runs git non-interactively (`GIT_TERMINAL_PROMPT=0`) so a credential/editor/pager prompt fails fast instead of hanging.

### Total test budget — 60 seconds, ABSOLUTE and IMMUTABLE

**THE 60-SECOND COMBINED TOTAL TEST BUDGET IS ABSOLUTE AND IMMUTABLE.**
It cannot be changed, overridden, or circumvented.

`make verify` runs `make test`, which executes one maintained parallel pytest invocation over `tests/` with `-m "not subprocess_e2e"`.

Budget enforcement:
- **Per-suite cap** (applies to `make test`, `make test-unit`, and `make test-integration`): each `python -m ralph.verify_timeout --suite-timeout 60` call is killed after 60 s. This cap is defined in `ralph/verify_timeout.py:DEFAULT_SUITE_TIMEOUT_SECONDS`. Per-suite caps are SECONDARY only — raising them does not increase the combined budget.
- **Combined-total cap** (enforced by `make verify` only): `ralph.verify` tracks cumulative wall-clock time via `time.monotonic()` across ALL test steps in `_BUDGET_TRACKED_STEPS`. The combined total must not exceed `_TOTAL_TEST_BUDGET_SECONDS` (60 s), defined at `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`. Adding suites, splitting tests, or creating new test targets does NOT reset or extend the budget — the cumulative tracker sums time across ALL tracked steps. If cumulative test time exceeds 60 s, `make verify` fails with `(budget exhausted — cumulative test time exceeded)`.

This combined limit is **IMMUTABLE** — the following do **NOT** circumvent it:
- Adding more test suites or shards
- Renaming test targets
- Moving tests between targets or suites
- Setting environment variables (RALPH_PYTEST_SUITE_TIMEOUT_SECONDS, etc.)
- Changing Makefile variables (PYTEST_SUITE_TIMEOUT_SECONDS)
- Modifying DEFAULT_SUITE_TIMEOUT_SECONDS or DEFAULT_TEST_TIMEOUT_SECONDS

**Example:** splitting your test suite into 3 separate test steps does NOT give you 3 × 60 s = 180 s of total budget. The cumulative tracker sums the elapsed time of every budget-tracked step, so all 3 suites together must still finish within the single 60 s combined cap.

| Attempted Circumvention | Why It Fails |
|---|---|
| Splitting tests into N suites | Cumulative tracker sums time across ALL tracked steps — NOT per-suite |
| Moving slow tests to a different suite/target | All test-budget-tracked steps count toward the same combined budget |
| Adding new test-related Makefile targets | `_BUDGET_TRACKED_STEPS` frozen set stays at `{2}` — new targets don't get budget tracking |
| Raising `PYTEST_SUITE_TIMEOUT_SECONDS` | Per-suite cap only; combined budget is enforced separately by `_TOTAL_TEST_BUDGET_SECONDS` |
| Modifying `_BUDGET_TRACKED_STEPS` frozenset | Blocked by `ralph/verify.py` import-time RuntimeError checks — immune to `python -O` |
| Raising `_TOTAL_TEST_BUDGET_SECONDS` | Blocked by import-time RuntimeError epsilon check (`must be 60.0`) — immune to `python -O` |
| Emptying `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Emptying `_BUDGET_TRACKED_STEPS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time containment RuntimeError check — immune to `python -O` |
| Adding `per-file-ignores` to pyproject.toml | Detected by `ralph/testing/audit_lint_bypass.py` (part of `make verify`) |
| Adding `ignore_missing_imports = true` to mypy config | Detected by `ralph/testing/audit_typecheck_bypass.py` (part of `make verify`) |
| Using bare `# noqa` or blanket `# type: ignore` | Detected by bypass audit modules with file:line reporting |

#### Verification Invariants

`ralph/verify.py` enforces import-time invariants to prevent accidental
or malicious weakening of budget enforcement:

- `_TOTAL_TEST_BUDGET_SECONDS > 0` — the budget must be positive.
- `_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS` —
  all tracked steps must exist.
- Every budget-tracked step has a positive timeout — budget enforcement
  cannot be silently nullified by a `None` or zero timeout.
- An epsilon check (`abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9`)
  confirms the constant has not been altered from its ABSOLUTE and
  IMMUTABLE value of 60.0 seconds.
- `_VERIFY_STEP_TIMEOUT_SECONDS > 0` — the per-step timeout must be positive.
- `_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0` — the per-step timeout must be non-trivial
  (at least 5 seconds, enforced via the `_MIN_VERIFY_STEP_TIMEOUT_SECONDS`
  constant in `ralph/verify.py`).
- `_KNOWN_TEST_STEP_LABELS` must not be empty — prevents silently
  hiding all test steps from budget tracking.
- `_BUDGET_TRACKED_STEPS` must not be empty — prevents disabling
  budget enforcement entirely.
- `'make test'` must be present in `_KNOWN_TEST_STEP_LABELS` — the
  canonical test step label always exists and is tracked.
- Every label in `_KNOWN_TEST_STEP_LABELS` must correspond to a
  tracked step, and every tracked step must have its label in
  `_KNOWN_TEST_STEP_LABELS` — prevents label/steps drift.

These invariants are checked at module import time. Any violation causes
a `RuntimeError` at startup (enforced by `if`/`raise` — immune to `python -O`
stripping), preventing `make verify` from running with
a weakened budget configuration.

All invariants are tested in `tests/test_verify_invariants.py` under
both normal execution and `python -O` to confirm survivability.

A timeout failure is a test design defect. Fix the production coupling — never adjust the budget. Remove I/O, use `MemoryWorkspace`, inject fake clocks. Do **not** raise `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS` to mask a slow test.

Verification passes only when all checks complete with **no ERROR/WARNING diagnostics**. If any step fails, fix the issue immediately and rerun. `make verify` emits a high-visibility failure banner that cites `AGENTS.md`.

### Bypass Audit Policy

The `make verify` pipeline includes automated bypass audits that scan the entire codebase for lint and typecheck circumvention. These audits are **non-optional** — they run as mandatory steps and cannot be skipped.

**Lint bypass audit** (`ralph/testing/audit_lint_bypass.py`):
- Detects bare `# noqa` without a specific error code (must use `# noqa: CODE`)
- Detects `# noqa: CODE` where CODE is not in the allowlist
- Detects `per-file-ignores` and `extend-per-file-ignores` in `pyproject.toml`
- Violations produce output: `file:line: [LINT-BYPASS] category: detail`

**Typecheck bypass audit** (`ralph/testing/audit_typecheck_bypass.py`):
- Detects blanket `# type: ignore` without a specific mypy error code
- Detects `# type: ignore[CODE]` without a policy-compliant reason marker (see `type-ignore-policy.md` for exact format requirements)
- Detects `# type: ignore` in test files (tests must be fully typed — no exceptions)
- Detects ALL mypy config that weakens type checking: `ignore_missing_imports = true`, `follow_imports = silent`, `exclude` patterns, `ignore_errors = true`, `disable_error_code` (globally suppresses error codes), `warn_unused_ignores = false` (silences unused ignore warnings), `disallow_untyped_defs = false` (allows untyped function definitions)
- Violations produce output: `file:line: [TYPECHECK-BYPASS] category: detail`

**Both audits** scan both `ralph/` and `tests/` directories. Each uses an allowlist of known-legitimate suppressions. Any new suppression that does not match the allowlist IS a violation. To add a legitimate suppression, the code must be added to the allowlist with a documented justification.

Reference: `AGENTS.md` §'Non-negotiables' for the full non-circumvention rules.

### Resource-accumulator non-circumvention rules

`make verify` step 17 (`ralph/testing/audit_resource_lifecycle.py`) enforces the
resource-accumulator contract that complements the bounded-subprocess audit.
A long-lived mutable collection assigned module-level OR to `self.X` inside
`__init__` MUST carry a FIFO / size cap or a justified escape-hatch marker:

- Use `collections.deque(maxlen=N)` for FIFO-bounded logs / record buffers
  (e.g. `RalphAuditSinkAdapter._records` uses `deque(maxlen=4096)`).
- Use `OrderedDict` / `defaultdict` paired with an explicit count cap and
  manual `popitem(last=False)` / `len(...) > cap` eviction (these have NO
  `maxlen=` kwarg).
- For genuinely unbounded-by-design collections, attach the inline marker
  `# bounded-accumulator-ok: <reason>` on the same line, naming the cap or
  the drain that keeps the collection bounded in practice.

A `deque()` / `collections.deque()` constructor call WITHOUT `maxlen=` is
treated as unbounded and flagged by the audit. Mutable collection literals
(`[]`, `{}`, `set()`) assigned to a module-level name or `self.X` in
`__init__` are flagged for the same reason — they have no cap and grow
monotonically across a long unattended run, exactly the leak class that
produced `BudgetState.failures` (unbounded `tuple[ClassifiedFailure, ...]`
accumulator never read for any decision) and `RalphAuditSinkAdapter._records`
(unbounded `list` flushed only by `drain_records()`, never by `flush()`).
Both leak classes are now closed by dropping the field / capping the deque,
not by weakening the audit.

Exclusions (intentional, documented to avoid false positives):
- `__all__` (Python re-export convention).
- Single-element list literals `[X]` (mutable-closure idiom).
- Dict / set literals whose keys / elements are all static (dispatch tables).
- Dataclass field defaults (`field(default_factory=...)`).
- Local variables inside non-`__init__` functions.

The ONLY bypass for the accumulator contract is the inline
`# bounded-accumulator-ok: <reason>` marker naming the cap constant or drain.
Like the lint/typecheck bypass audits above, this audit is part of `make
verify` and any new unbounded accumulator fails the gate.

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

`make test` runs the maintained verification suite as one parallel invocation over `tests/` with `-m "not subprocess_e2e"`, wrapped in the 60-second suite timeout. `make test-unit` excludes `tests/integration/`. `make test-integration` runs only `tests/integration/`. `make test-cov` enforces an 80% coverage gate. `make docs` builds Sphinx HTML with warnings as errors.

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

## Parallel worker bootstrap tests

Dedicated worker bootstrap isolates each fan-out worker's prompt, checkpoint, and runtime state under `.agent/workers/<unit_id>/`. These tests verify the isolation contract:

```bash
cd ralph-workflow
uv run pytest -q tests/test_parallel_worker_runtime.py
uv run pytest -q tests/integration/test_parallel_worker_bootstrap.py
```

## Interactive Claude PTY tests

```bash
cd ralph-workflow
uv run pytest -q tests/test_process_manager_pty.py
uv run pytest -q tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py
uv run pytest -q tests/integration/test_claude_interactive_pty_e2e.py tests/integration/test_claude_interactive_interrupt_realtime.py
```

## Manual interactive Claude smoke test

**Ad hoc manual test only. Not part of `make verify`.** This command consumes live agent tokens and exists to validate real interactive-Claude behavior without polluting the normal verification flow.

```bash
cd ralph-workflow
python -m ralph smoke-interactive-claude
```

What it does:
- writes a smoke prompt under `tmp/interactive-claude-smoke/`
- asks `claude/haiku` to create a JavaScript todo list
- prints a detailed report of what worked and what broke
- uses the headless Claude contract as a guide for expected semantics (session capture, tool activity, completion signal, parser events, tmp/ artifact creation)

Current failure signature to watch for:
- the file and `smoke_test_result` artifact are created
- but the report still says `Agent produced no output for 30s`
- and the report still shows missing session ID, missing `declare_complete`, missing parser events, missing tool activity, or too few meaningful output lines

Do not add this smoke command to `make verify` or any always-on verification path. Keep it as manual ad hoc testing for live interactive-Claude regressions only.

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

### Cycle cap vs unavailable-cooldown waiting

The recovery cycle cap prevents infinite loops from **budget exhaustion** (consumed retries, chain exhaustion, repeated agent failures). It is enforced by `RecoveryController` via `recovery_cycle_count` and terminates the run with an `ExitFailureEffect` when the cap is reached.

Unavailable-cooldown waiting is **different** and does **not** count toward the cycle cap. When every agent in a phase chain is temporarily unavailable, the controller preserves the session and returns a state with `last_retry_delay_ms > 0` and reason `all agents unavailable; waiting for cooldown expiry`. The run loop sleeps on `last_retry_delay_ms` and retries the same phase. This path does **not** increment `recovery_cycle_count`, so agents can cool down indefinitely without hitting the cap. The "WE NEVER CRASH" requirement applies here: the system waits for the earliest cooldown to expire and then resumes, rather than terminating.

In short:
- **Cycle cap** = limit on retry budget consumption / repeated failure loops.
- **Unavailable-cooldown wait** = pause until an agent becomes available again; unbounded by the cycle cap.

## Idle watchdog — per-channel activity evidence model

The idle watchdog in `ralph-workflow/ralph/agents/idle_watchdog/` decides whether a session is stuck by looking at four independent evidence channels: the agent's `stdout` output (the original baseline), MCP `tools/call` activity against the Ralph Workflow MCP server, subagent progress / heartbeat / tool-call signals routed through `OpenCodeExecutionStrategy.observe_line`, and workspace file-change events captured by `WorkspaceMonitor`. Workspace evidence collection runs whenever a run has a `workspace_path`, regardless of whether the progress UI (`show_progress`) is enabled. While ANY non-stdout channel is fresher than `agent_idle_activity_evidence_ttl_seconds` (default 30.0 s, tunable in `ralph-workflow.toml`; set to `0.0` to disable and restore the legacy stdout-only behavior), the watchdog defers a `NO_OUTPUT_DEADLINE` fire and returns `CONTINUE` instead. The `SESSION_CEILING_EXCEEDED` and `CHILDREN_PERSIST_TOO_LONG` absolute ceilings are checked BEFORE the deferral, so they remain absolute — activity evidence cannot reset or defer either ceiling.

Activity means **demonstrated work**, not mere existence: an OpenCode subagent process that is alive but has produced no output, no tool calls, and no file changes for the configured idle window is **not** evidence of progress. Once scoped Ralph child evidence goes stale, the run falls back to the normal idle timeout instead of lingering under the larger cumulative waiting-on-child ceiling. Raw OS descendants alone defer the verdict only when Ralph never had scoped visibility into the child in the first place.

Every watchdog fire embeds an `evidence_summary` (channel name, last_at, age_seconds, counter) in its diagnostic so an on-call operator or post-mortem can see exactly which channels were fresh and which were stale at the moment the watchdog fired. The feature is covered by the black-box suites in `ralph-workflow/tests/agents/test_idle_watchdog_3.py`, `ralph-workflow/tests/mcp/test_mcp_activity_sink.py`, `ralph-workflow/tests/agents/test_subagent_activity_wiring.py`, `ralph-workflow/tests/agents/test_invoke_timeout_integration.py`, and `ralph-workflow/tests/test_opencode_session_execution_stale_scoped_child_evidence_timeout.py`.

**Upstream coverage:** the `mcp_tool` channel also covers upstream (third-party) MCP tool calls proxied through `UpstreamProxyHandler`; the `mcp_tool` evidence is now end-to-end across both the in-process Ralph Workflow MCP server and configured upstream servers.
