# Required Verification (before PR/completion)

## Canonical command

```bash
cd ralph-workflow
make verify
```

The Sphinx docs build (`make docs`) is wired in as a Make prerequisite of `make verify` with `-W --keep-going` so any Sphinx warning fails the gate; it runs before the Python verify step and stays outside the immutable 60-second combined test budget.

## Verify steps and gates

`make verify` runs the ordered step registry `ralph/verify_steps.py::_VERIFY_STEPS` (re-exported as `ralph.verify._VERIFY_STEPS`); `ralph/verify.py` owns the budget policy that charges those steps. The tuple is the source of truth — the position of an entry IS its execution order — and it currently holds **47 steps**. The four gates that protect the supply chain and the test surface are:

| Gate | Verify step label | Enforcing command |
|---|---|---|
| Instruction / MCP-config integrity | `instruction integrity audit (audit_instruction_integrity)` | `uv run python -m ralph.testing.audit_instruction_integrity` |
| Dependency integrity | `dependency integrity audit (audit_dependency_integrity)` | `uv run python -m ralph.testing.audit_dependency_integrity` |
| Secret scan | `secret scan audit (audit_secret_scan)` | `uv run python -m ralph.testing.audit_secret_scan` |
| Test integrity | `test integrity audit (audit_test_integrity)` | `uv run python -m ralph.testing.audit_test_integrity` |

Each of the four is an audit, not a test suite: none is listed in `_KNOWN_TEST_STEP_LABELS` or `_BUDGET_TRACKED_STEPS`, so each runs on every `make verify` without being charged to the combined 60-second test budget. The instruction-integrity gate checks the whole tree; the other three report only what the working-tree diff against the committed `HEAD` baseline changes. A gate that is not registered in `_VERIFY_STEPS` does not run at all — see §'No unrelated-failure exemption'.

## Project-policy portfolio

Policy schema v6 (current, see `markers.SCHEMA_VERSION`; currently v6) requires
`docs/ralph-workflow-policy/policy-portfolio.toml`. Its schema, composition
order, limits, control fields, exception rules, and evidence contract are
defined once in
[Verification Policy: Bounded policy portfolio](../ralph-workflow-policy/verification-policy.md#bounded-policy-portfolio).
Use that section rather than duplicating the contract here.

For project-policy changes, the focused runnable proof is:

```bash
python -m pytest ralph-workflow/tests/project_policy/test_policy_docs_schema_consistency.py ralph-workflow/tests/project_policy/test_starters.py -q
```

Then run the canonical completion command above. The focused tests prove the
v6 markers and seeded manifest; `make verify` proves repository-wide
integration. A process exit by itself does not prove a material behavior claim:
retain the public-surface artifact and its immutable subject/run IDs, validated
oracle, and independent verdict.

## No unrelated-failure exemption — you find it, you fix it

`make verify` must pass **in full**, and there is NO exemption for a failure your change did not cause. "It was already failing on `main`", "that gate is unrelated to what I touched", and "that check isn't run by `make test`" are not acceptable outcomes — a red gate is a red gate, and whoever next observes it owns fixing it.

**Do not investigate who caused it.** Stashing your changes, bisecting, or re-running against a clean tree just to prove a failure is "pre-existing" is almost never useful work — the answer does not change what you do next, which is fix it. Chase provenance only when it is genuinely diagnostic (the triggering change tells you what the bug *is*), never to decide whether the failure is yours to own. It is always yours to own.

**Preventing regressions outranks finishing the task in hand.** If a pre-existing failure blocks you, fix the failure first and finish your original task afterwards. Never report your own work as verified while any gate is red; if a repair is genuinely out of scope, stop and surface it as an active blocker rather than working around it.

A corollary for anyone adding a check: **wire it into `make verify`.** A check that lives only in an opt-in suite the default gate skips will rot silently. `audit_repo_structure` runs in the default gate as the verify step "repo structure audit (audit_repo_structure)".

## Fabrication guardrails

Any edit to a public-facing markdown file (README, USERS.md, docs/, the Sphinx operator manual) is in scope for the [fabrication guard](fabrication-guard.md). USERS.md is the single canonical community directory. The guard runs as a pre-commit hook at Level 1; re-run it explicitly with `./scripts/fabrication_guard.py --level 1 <file>` or `--level 2 <file>` for network existence checks. Bypassing the guard (for example with `--no-verify`) is itself fabrication.

## Total test budget

The combined total test budget is **pinned**: 60 seconds of wall clock net of host contention, and 700 CPU core-seconds. It cannot be changed, overridden, or circumvented. `make verify` runs `make test`, which selects `(not subprocess_e2e and not smoke) or required_auto_integrate_e2e` across the maintained pytest shards. All selected files join the same LPT partition, distributed across the parallel shards (`ralph/test_suites.py:1958-1969`). The critical path is the slowest shard.

Wall clock on a shared host also measures every other process, so `ralph/verify.py` judges the tracked steps once they have all passed (`ralph/verify_budget.py` documents the rule):

- **CPU** — the core-seconds the suite's own process trees consumed (`getrusage(RUSAGE_CHILDREN)`, every descendant reaped inside the tree included, plus the CPU of descendants orphaned to init during a step, which rusage never sees and `orphaned_cpu_tracker` samples twice a second) may not exceed `_TOTAL_TEST_CPU_BUDGET_SECONDS = 700.0`. The suite controls this number; host load barely moves it, so exceeding it always fails. Calibrated 2026-10-30 on the maintained 6P+6E Apple Silicon host, before the required real-git lane moved to ten plain shards: `make test` 396–422 core-s plus the aggregate smoke 47 core-s.
- **Wall clock** — over 60 s fails unless it fits once the CPU other processes used meanwhile is credited (their core-seconds ÷ the host's logical CPUs: the most wall time foreign load can have taken). Fitting only with that credit passes with a host-contention WARNING that prints every number used. On an idle host the credit is ~0 and 60 s applies exactly. The credit is an upper bound, which is why the CPU budget is the hard backstop.
- **Host capacity** — a host can be too small for 60 s with nothing else running: ~470 core-s of suite work on a 4-core CI runner needs at least 117 s even perfectly parallel. When the suite's own CPU kept the cores foreign load left free at least 90% busy (`suite CPU / (wall × cores − foreign CPU) ≥ 0.9`), the wall clock is capacity-bound, the CPU budget is the binding limit, and the overrun is a host-capacity WARNING. A wall overrun that left capacity idle — the suite waited instead of working — still fails. This cannot shelter a slow suite on a large host: keeping 13 or more cores 90% busy for over 60 s costs more than 700 core-s, which fails first.

The tracked steps are killed only at the cumulative `_TOTAL_TEST_HANG_CAP_SECONDS = 180.0`, and each pytest invocation only at `DEFAULT_SUITE_TIMEOUT_SECONDS = 180.0`: those are hang detection, not speed budgets. Measured on the maintained host before the required real-git lane moved to ten plain shards, `make test` needs ~400 CPU core-seconds and saturates all 12 cores, so its wall clock tracks host capacity — a wall-clock-only gate failed whenever anything else ran.

`ralph/verify.py` enforces these import-time invariants (using `if`/`raise RuntimeError`, NOT `assert`, so they survive `python -O`):

| Invariant | Error message | Purpose |
|---|---|---|
| `_TOTAL_TEST_BUDGET_SECONDS > 0` | `must be positive` | Budget can't be zero or negative |
| `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` | `must be 60.0` | Budget constant can't be silently altered |
| `abs(_TOTAL_TEST_CPU_BUDGET_SECONDS - 700.0) < 1e-9` | `must be 700.0` | CPU budget can't be silently altered |
| `_TOTAL_TEST_HANG_CAP_SECONDS == 3 * _TOTAL_TEST_BUDGET_SECONDS` | `must be 3 x _TOTAL_TEST_BUDGET_SECONDS` | Hang cap can't drift into a budget |
| `_BUDGET_TRACKED_STEPS` indices are valid indices into `_VERIFY_STEPS` | `indices must be valid` | Tracked steps must exist in `_VERIFY_STEPS` |
| Every budget-tracked step has a positive timeout | `must have a positive timeout` | Budget-tracked steps need timeout |
| `_VERIFY_STEP_TIMEOUT_SECONDS > 0` | `must be positive` | Per-step timeout can't be zero or negative |
| `_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0` | `must be at least 5.0` | Per-step timeout must be non-trivial (>= 5s) |
| `_KNOWN_TEST_STEP_LABELS` non-empty | `must not be empty` | Can't empty label set to hide test steps |
| `_BUDGET_TRACKED_STEPS` non-empty | `must not be empty` | Can't empty tracked set to disable budget |
| `'make test'` present in `_KNOWN_TEST_STEP_LABELS` | `must contain 'make test'` | Primary test step label always present |
| Labels/steps sync enforced | `must be` / `must NOT be` | Every label in `_KNOWN_TEST_STEP_LABELS` is tracked; no untracked test steps |

All invariants are tested in `tests/test_verify_invariants.py` under both normal execution and `python -O`. A budget failure is a test design defect — fix the production coupling, never adjust the budget. A host-contention WARNING is not a licence to grow the suite: the CPU budget still binds. Per-suite caps (`PYTEST_SUITE_TIMEOUT_SECONDS`, `DEFAULT_SUITE_TIMEOUT_SECONDS`) are hang caps; raising them does NOT increase the combined budget. The cumulative tracker sums time across ALL budget-tracked steps via `time.monotonic()`. The canonical non-circumvention table is [§'Non-circumvention table'](#non-circumvention-table) below; `AGENTS.md` §'TEST BUDGET' names this file as its owner.

Each phase of each test — fixture setup, the test call and fixture teardown, each with its own budget (the `timeout_seconds` marker, else `RALPH_PYTEST_TEST_TIMEOUT_SECONDS`, else 1 s) — is charged the CPU seconds its *main thread* spends, so a thread an earlier test leaked cannot bill the next one. A wall-clock hang cap of 5x the budget (at most +60 s) catches phases that wait instead of work. Both raise `TestExecutionTimeoutError`, a `BaseException` so code under test cannot swallow it; an overrun that is swallowed anyway is re-raised and fails the phase when it ends. The mechanism lives in `tests/_per_test_budget.py`: a watchdog thread signals the main thread (`SIGPROF`) only when the budget could first have run out, and the handler measures `time.thread_time()`. Budgets of zero, below zero or infinite are rejected, a test that replaces the `SIGPROF` handler or leaves `SIGPROF` blocked fails, and `make verify` forces both `RALPH_PYTEST_*_TIMEOUT_SECONDS` variables to their defaults for every step. A test must not assert on wall clock it does not own. A deadline that only bounds how long a real child process takes to start is sized for a saturated host (with a comment saying so); synchronization uses an observable condition (a marker file, an event), never a fixed sleep; a latency gate re-times an over-limit sample before counting it, so only a slowdown that reproduces fails.

## Non-circumvention table

The following do **NOT** circumvent the combined test budget or the lint/typecheck enforcement (detected by the corresponding audit module under `ralph/testing/audit_*.py`):

| Attempted circumvention | Why it fails |
|---|---|
| Splitting tests into N suites | Cumulative tracker sums time across ALL tracked steps — not per-suite |
| Moving slow tests to a different suite/target | All budget-tracked steps count toward the same combined budget |
| Renaming test targets or adding new test-related `_VERIFY_STEPS` without updating `_KNOWN_TEST_STEP_LABELS` **and** `_BUDGET_TRACKED_STEPS` | `_BUDGET_TRACKED_STEPS` stays frozen — new targets do not get budget tracking; EVERY `_VERIFY_STEPS` entry that runs tests belongs in both sets |
| Raising `PYTEST_SUITE_TIMEOUT_SECONDS` / `DEFAULT_SUITE_TIMEOUT_SECONDS` | Per-suite hang cap only; combined budget enforced separately by `_TOTAL_TEST_BUDGET_SECONDS` / `_TOTAL_TEST_CPU_BUDGET_SECONDS` |
| Setting `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` / `RALPH_PYTEST_TEST_TIMEOUT_SECONDS` | `make verify` forces both to their defaults for every step; elsewhere they only adjust per-suite/per-test caps, never the combined budget, and zero or negative values are rejected |
| `@pytest.mark.timeout_seconds(0)`, or arming and cancelling an own `ITIMER_REAL` / replacing the `SIGPROF` handler | Non-positive budgets are rejected; the per-test budget uses no interval timer a test can cancel, and a replaced or blocked `SIGPROF` fails the phase. `tests/_per_test_budget.py` charges setup, call and teardown separately (main-thread CPU plus a 5x wall hang cap) and rejects non-positive budgets |
| Modifying `_BUDGET_TRACKED_STEPS` / raising `_TOTAL_TEST_BUDGET_SECONDS`, `_TOTAL_TEST_CPU_BUDGET_SECONDS` or `_TOTAL_TEST_HANG_CAP_SECONDS` | Blocked by import-time `if`/`raise RuntimeError` checks — immune to `python -O` |
| Emptying `_KNOWN_TEST_STEP_LABELS` / `_BUDGET_TRACKED_STEPS` | Blocked by import-time non-empty RuntimeError check — immune to `python -O` |
| Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` | Blocked by import-time containment RuntimeError check — immune to `python -O` |
| Adding `per-file-ignores` / `extend-per-file-ignores` / blanket `# noqa` | Detected by `ralph/testing/audit_lint_bypass.py` |
| Using any `# noqa` — bare, or `# noqa: CODE` | Detected by `ralph/testing/audit_lint_bypass.py` — inline lint suppression is not permitted; remove the comment and fix the underlying lint error |
| Adding `ignore_missing_imports`, `follow_imports = silent`, `exclude` patterns, `ignore_errors`, `disable_error_code`, `warn_unused_ignores = false`, or `disallow_untyped_defs = false` to mypy config | Detected by `ralph/testing/audit_typecheck_bypass.py` |
| Using `# type: ignore` to silence a mypy error, bare or with an error code | Detected by `ralph/testing/audit_comments.py` — every source comment is a finding, so no suppression comment survives the gate; the remedy is a fix in the code (see `docs/agents/type-ignore-policy.md`, mandatory reading) |
| Using `# type: ignore` in test files | Tests must be fully typed — no exceptions |
| Using `time.sleep()`, real subprocess, or real file I/O in non-`subprocess_e2e` tests | Detected by `ralph/testing/audit_test_policy.py` |
| Unbounded `subprocess.run` / `httpx.*` / `urlopen` / `socket.create_connection` (no `timeout=`) in `ralph/mcp/`, `ralph/git/`, `ralph/process/manager/` | Detected by `ralph/testing/audit_mcp_timeout.py` — including `subprocess.run`/`.communicate`/`.wait` without `timeout=`; the remedy is an explicit bounded timeout on the call |
| Mutable collection literals (`list` / `dict` / `set` / `deque`) assigned to module-level names or `self.X` in `__init__` without `maxlen=` | Detected by `ralph/testing/audit_resource_lifecycle.py` — a `deque()` call without `maxlen=` counts as unbounded; the remedy is a bounded structure such as `deque(maxlen=...)` or an explicit count cap in the code |
| Any weakening of any check | There is no bypass — the check is repaired in the code it guards, and never weakened, disabled, or excluded to make a finding disappear |

Every circumvention above is detected by `make verify`. There is no bypass: a finding is repaired in the code that raised it, or the reporting tool's own configuration already accepts that case — a gate is never weakened, disabled, or excluded to make a finding disappear. See [AGENTS.md §'Non-negotiables'](../../AGENTS.md) for the absolute rules this contract implements; the table above is the canonical one.

## How to fix a slow test

Never work around the budget. Repair the test design:

- Replace real I/O with fakes (`MemoryWorkspace`, `FsWorkspace`, `tmp_path`, `MockProcessExecutor`).
- Eliminate `sleep()` and real wall-clock waits — inject a clock abstraction instead.
- Refactor production code for testability — extract I/O behind an interface.
- Assert on observable behavior, not implementation internals.
- For cumulative volume bottlenecks (many fast tests, not individual slow tests): consolidate parameterized tests with overlapping coverage, optimize shared fixtures, or reduce redundant test coverage — see `docs/agents/testing-guide.md` and `docs/ralph-workflow-policy/testing-policy.md` §"Test optimization".

## Focused suite-routing feedback

For a change confined to `ralph-workflow/Makefile`, `ralph-workflow/ralph/test_suites.py`, or the suite-routing contract files `tests/test_makefile_verification_workflow.py`, `tests/test_test_suites.py`, and `tests/test_test_suites_orchestration.py`, run:

```bash
make -C ralph-workflow test-fast
```

This is a fail-closed static three-file profile with `not subprocess_e2e and not smoke`; it is not full verification and never replaces the required `make -C ralph-workflow verify` completion gate.

## Smoke-check subsections

Use these focused commands when a smoke check is required for the area you are touching. Most commands below live outside the budget-tracked combined budget (per-suite caps only) so they do not inflate the 60-second gate. `make verify` runs every deterministic install, multimodal, and visual smoke case together through `make test-verification-smoke`, charged to the same immutable budget as `make test`. Sharing one pytest startup reduces overhead without dropping cases. The separate smoke targets remain available for focused checks.

```bash
# Policy loader smoke check (after changing policy defaults)
python -c "from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

# Project policy v6 starter smoke check (ralph --init / preflight bootstrap):
# verifies all bundled starters carry the v6 schema marker and 7-column guard table.
python -c "from ralph.project_policy import starters, markers; assert all(starters.read_starter(f).splitlines()[0] == markers.POLICY_SCHEMA_MARKER for f in starters.iter_starter_names())"

# Parallel-mode regression tests (work-units / namespaced payloads)
uv run pytest -q tests/test_parallel_mode_docs_banned_phrases_across_all_docs.py tests/test_parallel_mode_docs_namespaced_payload_docs.py

# Parallel worker bootstrap tests
uv run pytest -q tests/test_parallel_worker_runtime.py tests/integration/test_parallel_worker_bootstrap.py

# Interactive Claude PTY tests
uv run pytest -q tests/test_process_manager_pty.py tests/test_claude_interactive_pty.py tests/test_claude_interactive_session_resume.py tests/test_claude_interactive_parser.py

# Recovery tests
uv run pytest -x tests/recovery/ tests/test_recovery_first_invariant.py tests/test_reducer.py tests/test_pipeline_runner.py

# Offline real-process install smoke: drives the public `make install`
# transaction with isolated HOME/PATH and a fail-closed fake `uv`.
make test-install-make-smoke

# This smoke verifies the missing-uv diagnostic, immutable-generation `rdev`
# installation, source-checkout independence, lock contention, and failed
# candidate preservation. It does not contact a network and is budget-tracked by
# `make verify` through the combined `test-verification-smoke` target.

# Multimodal smoke (criterion 5): drives the deterministic multimodal
# stub agent across every harness identity. Runs under ``make verify``
# through the combined budget-tracked smoke entry -- unlike the commands
# above, this one IS counted toward the immutable 60-second combined
# budget so a future endpoint-delivery regression like the OpenCode
# multimodal break fails CI rather than passing silently.
make test-multimodal-smoke

# Per-harness multimodal smoke (manual, paid-backend dialling where
# the harness is live). These do NOT run under ``make verify`` -- they
# consume live tokens. Each command drives one harness identity
# through the multimodal scenario and grades the multimodal fact at
# WIRE on success (exit 0).
python -m ralph smoke-interactive-claude --multimodal
python -m ralph smoke-headless-claude --multimodal
python -m ralph smoke-interactive-agy --multimodal
python -m ralph smoke-interactive-nanocoder --multimodal
python -m ralph smoke-interactive-cursor --multimodal
python -m ralph smoke-interactive-opencode --multimodal
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

- **Single-stripper rule.** `ralph/display/line_sanitizer.py::strip_terminal_control` is the canonical terminal-control remover. The full CSI parameter-byte range `[0-?]` matches every valid sequence (alternate screen `ESC[?1049h`, erase display `ESC[2J`, private-parameter CSI `ESC[>0c` and `ESC[<35;1;2M`, OSC titles, DCS/SOS/PM/APC payloads, dangling string introducers, and two-character ESC). No other module may define a second, narrower regex — the audit rejects any file that reintroduces the SGR-only `[0-9;]*m` form or the digit-only `[0-9;?]` class. Defence-in-depth: `ralph/agents/invoke/_pty_line_reader.py` must keep yielding raw VT text (its `yield queued_line` line is an audit invariant) because interactive permission auto-approval parses that raw stream — sanitizing at the source would silently break it.

- **Single-painter rule.** Every loguru terminal log record goes through `ralph.display.log_sink` (either `make_sanitizing_log_sink` for the DisplayContext Console, or `make_stderr_log_sink` for the library/worker fallback), never through a raw `logger.add(sys.stderr, ...)`. The CLI (`ralph/cli/main.py::_configure_logging`) takes an injected `console_sink` keyword; `ralph/cli/main.py::main` wires the Console-backed sink at the call site so the rich `Live` status bar is the only painter of Ralph's terminal. Sanitization happens through `strip_terminal_control`; the sink prints via `ctx.console.print(text, markup=False, highlight=False)` so bracketed paths and `[bold]` tokens survive verbatim. Module construction of `rich.console.Console` outside `ralph/display/theme.py` is forbidden by `tests/display/test_di_invariants.py`; the audit enforces the same rule on `ralph.display.log_sink` itself.

- **Background-spawn rule.** `SpawnOptions.stdin` defaults to `subprocess.DEVNULL` (the dataclass field in `ralph/process/manager/_spawn_options.py`) so no child inherits Ralph's controlling-terminal stdin by construction. Callers that genuinely need stdio (`ralph/mcp/protocol/transport.py:270` for the stdio MCP transport and `ralph/mcp/upstream/_stdio_upstream_client.py:122` for the stdio upstream client) already pass `subprocess.PIPE` explicitly. The audit additionally enforces that NO `SpawnOptions(...)` call site anywhere under `ralph/` may pass `stdin=None` (the package-wide `PackageWideCallSiteInvariant` walks every `*.py` file and rejects the INHERIT literal). PTY children get their own session: `ralph/process/pty.py::spawn_pty_process` calls `os.setsid()` plus `fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)` inside the child branch — removing either fails the audit.

- **Exception: keep the source reader raw.** `ralph/agents/invoke/_pty_line_reader.py` and `ralph/agents/invoke/_process_reader.py` keep yielding raw bytes / queued lines so the stream that auto-approves Claude Code's permission prompts is unchanged. Sanitization must happen ONLY at the display boundary (`display/parallel_display.py`, `display/activity_model.py`, the new `display/log_sink.py`) — never at the source.

Enforcing command:

```bash
uv run python -m ralph.testing.audit_terminal_escape_containment
```

This audit is one of the 47 steps in the `ralph/verify_steps.py::_VERIFY_STEPS` registry (re-exported as `ralph.verify._VERIFY_STEPS`) — it is not the last entry; the last one is `audit_mcp_agent_boundary`. It is not budget-tracked (exempt from the 60-second combined test budget) and it runs on every `make verify`. The audit's invariant count is pinned by `tests/test_audit_terminal_escape_containment.py::test_audit_invariant_count_matches_table` — adding an invariant requires bumping that count in the same change.

## Terminal state restoration contract

The terminal state restoration contract keeps Ralph ownership-safe on its own parent terminal: every controlled exit restores the saved termios line-discipline state, makes the cursor visible, and resets SGR attributes. It does NOT switch alternate-screen buffers, clear or reposition the prior transcript, or flush queued operator input that Ralph does not own. The contract is owned by `ralph/display/terminal_restore.py` and wired across Ralph's controlled exit paths:

- **Normal and exception exit paths.** Snapshot TTY modes at CLI entry (`ralph/cli/main.py::ensure_cli_terminal_restore`) and register `restore_terminal` via `atexit`. `terminal_restore_sequence()` emits exactly the cursor-show escape (`ESC[?25h`) followed by the SGR-reset escape (`ESC[0m`); no alternate-screen exits, mouse/focus/bracketed-paste/cursor/keypad/scroll-region/charset/autowrap resets, or display-clearing controls appear on this path. The parent stream is flushed after the sequence, but Ralph never discards operator input.
- **Interrupt and termination paths.** `InterruptController.force_exit` (`ralph/interrupt/controller.py`) invokes `restore_terminal` before `hard_exit` (`os._exit`); CLI handlers write the same two-escape sequence and call `restore_terminal_modes` (which uses the saved snapshot, never fresh `tcgetattr`) before delegating `SIGTERM` and `SIGHUP`. These paths inherit the same ownership-safe envelope as the normal-exit path.
- **Controlling-terminal selection.** The restore target falls back from stdout to stderr and then to the controlling terminal. `terminal_understands_vt` in `ralph/display/terminal_restore.py` suppresses escape writes for unset, empty, or `TERM=dumb` terminals, but termios restoration from the saved snapshot remains unconditional on every path. Input flushing on the parent stream is intentionally absent — only the OSC 11 probe below touches it.
- **OSC 11 probe timeout flush (owned exception).** The OSC 11 background probe (`ralph/display/_terminal_bg_query.py::_probe`) is the single place that may `termios.tcflush(..., TCIFLUSH)` and it does so only on its owned timeout path (when `parsed_color is None`, inside the `finally` that restores the pre-`setraw` snapshot). It never flushes the parent stream from the restore utility, and the probe publishes its pre-raw termios snapshot, calls `setraw`, sends its OSC query only when `terminal_understands_vt` permits it, and restores the snapshot that existed before the probe.
- **Signal handler cleanup.** `install_width_refresher` (`ralph/display/context.py`) captures the previous `SIGWINCH` handler and returns a `stop()` callback that restores it on shutdown. Its refresh handler is total, drops re-entrant delivery, and restores `SIG_DFL` when no prior handler was captured.

*Unrecoverable states:* Terminal state cannot be restored from inside the process if the process receives an uncatchable `SIGKILL` (signal 9) or if the terminal emulator window is closed externally.

## MCP timeout contract

Every blocking I/O operation under `ralph/mcp/` (as well as `ralph/git/` and `ralph/process/manager/`) must perform blocking calls with a bounded, fail-closed timeout. An unbounded blocking call hangs the worker or server thread and starves the agent of output. This contract is enforced by `ralph/testing/audit_mcp_timeout.py` as part of `make verify`.

### Diagnostic-vs-deadline split in MCP startup

The MCP server startup architecture enforces a strict distinction between non-fatal stage diagnostics and the fatal process deadline:

- **Diagnostic stage budgets:** Individual startup stages are tracked with stage budgets (e.g. `_PRE_SERVE_BUDGET_S`, `_CONFIG_LOAD_BUDGET_S`, `_REGISTRY_BUILD_BUDGET_S`, `_READY_TO_SERVE_BUDGET_S`). When a stage overruns its individual budget, `record_stage_elapsed` emits a structured WARNING log and appends a diagnostic entry to the sibling JSONL `.overruns` file (`<stage_file_path>.overruns`) including calculated `headroom_s` to the real deadline. Stage overruns are diagnostic warnings only; they do NOT raise exceptions or abort startup.
- **Fatal startup deadline:** Fatal checks during startup evaluate cumulative elapsed time against the real startup deadline: `mcp_real_startup_deadline_from_env()` = `mcp_preflight_timeout_from_env() - _PROBE_SAFETY_S` (default: 28.0s = 30.0s preflight - 2.0s probe-safety margin). When cumulative elapsed time exceeds this deadline, `_check_real_startup_deadline` raises `McpServerError` (and pre-serve execution triggers `fail_closed_pre_serve_overrun`) so the child fails loudly before supervisor `SIGKILL`.
- **Bounded pre-serve and exit paths:** Pre-serve operations (`run_pre_serve_bounded` in `ralph/mcp/server/_runtime_polling.py`) execute within the remaining real startup deadline (`mcp_real_startup_deadline_from_env() - elapsed`), allowing slow-but-successful pre-serve operations (such as session discovery and configuration parsing) to use available headroom rather than being prematurely truncated by an arbitrary sub-second constant. Overrun diagnostic flushes execute in a daemon thread bounded by `mcp_overrun_diagnostic_timeout_from_env()` (default 0.2s) so a stalled filesystem or blocked FIFO cannot delay process exit or hold inherited sockets past the preflight ceiling. The legacy `mcp_pre_serve_budget_from_env()` helper and `RALPH_MCP_PRE_SERVE_TIMEOUT_MS` environment variable remain available for diagnostic stage budgeting and compatibility, but do not truncate runtime execution.
- **Stale git lock recovery and persistent reducer retry:** Git operations guard mutating calls with `_run_git_operation_with_stale_lock_recovery` using a conservative 10s age threshold and negative liveness proof (OR-predicate across proc fd, cwd, environ, lsof, fuser) inside a bounded 0.5s `ThreadPoolExecutor` probe. Contention retries with bounded exponential backoff (total ≤ 1.7s) before escalating to the reducer. When policy defines no automated recovery phase (`commit_recovery` or `commit_review`), the reducer clamps `recovery_epoch` at `_MAX_COMMIT_RECOVERY_ATTEMPTS` and retries in-place, preserving the validated commit message at `effect.message_file` across passes rather than aborting to `failed_terminal`.

## Cross-links

- `ralph/verify_steps.py` — the `_VERIFY_STEPS` registry (the step definitions and their execution order)
- `ralph/verify.py` — budget tracker, the re-exported `_VERIFY_STEPS`, invariant checks
- `ralph/testing/audit_*.py` — per-audit machinery (see each docstring for the per-audit invariant list)
- [AGENTS.md §'Non-negotiables'](../../AGENTS.md) — canonical policy and circumvention table
- [Testing Guide](testing-guide.md) — test design rules and required doubles
- [Deterministic Auto-Commit](deterministic-auto-commit.md) — the producer-level discipline for engine-owned writes
- [Remote sync](../../ralph-workflow/docs/agents/remote-sync.md) — the contract that, with remote sync enabled, every auto-integration entry point (post-commit, startup recovery, periodic catch-up ticker) fast-forwards the local target to the remote, and its regression tests
- [Documentation Rubric](../code-style/documentation-rubric.md) — for any docs/README/manual change

If the change touches README, docs, START_HERE, the manual, or any public-doc route, read [Documentation Rubric](../code-style/documentation-rubric.md) first and check the edited surface against it before calling the docs work done.
