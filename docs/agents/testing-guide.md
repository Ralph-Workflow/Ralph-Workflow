# Testing Guide (Canonical)

**Single source of truth for all test strategy, rules, and patterns.**
Read before writing or modifying any test.

---

## Test Performance Policy — MANDATORY

> **This policy is non-negotiable. AI agents must comply before marking any test task complete.**

### The Rule

**Every test must finish within 1 second.**

**Combined budget (enforced by `make verify`):** When `make verify` runs `make test`, the combined wall-clock time of all test suites must stay within 60 seconds total, enforced by `ralph.verify._TOTAL_TEST_BUDGET_SECONDS = 60.0`. You cannot circumvent this by adding suites, renaming targets, or redistributing tests. `conftest.py` installs a `SIGALRM`-based watchdog on every test. If a test exceeds the limit it is killed with `TestExecutionTimeoutError`. Do not work around this; fix the design.

Budget enforcement invariants in `ralph/verify.py` use `if`/`raise RuntimeError` (NOT `assert`) — this prevents `python -O` from stripping the checks. All import-time invariants survive `-O`.

For cumulative volume bottlenecks (many fast tests where per-item overhead dominates), valid fix strategies include:

- Consolidating parameterized tests with overlapping coverage
- Optimizing shared fixtures (function-scoped → session-scoped where safe)
- Reducing redundant test coverage (never remove the sole coverage for a code path)

Tests in `_IO_ALLOWLIST` (e.g., memory regression tests) that require real I/O for valid assertions must not be refactored to test doubles — optimize their fixtures instead.

For the test-optimization strategy — move equivalent coverage down the test pyramid, remove duplicates, and refactor seams for testability — see [Testing Policy: Test optimization strategy](../ralph-workflow-policy/testing-policy.md#test-optimization-strategy). Consult a target repository's own strategy first when it has one.

Override only when genuinely required:

```python
@pytest.mark.timeout_seconds(30)
def test_large_artifact_round_trip() -> None: ...
```

### What "fix it" means

1. **Refactor code to be testable.** If a test is slow because production code does real I/O, sleeps, or spawns real processes, that is a design defect. Extract the I/O behind a `Workspace` or `AgentExecutor` boundary and test the pure logic with fakes.
2. **Replace real I/O with fakes.** Use `MemoryWorkspace` instead of the real filesystem, `FakeAgentExecutor` + `FakeRun` instead of real subprocesses. Never let real filesystem writes or network calls into unit or integration tests.
3. **Eliminate real `time.sleep` / wall-clock waits.** If a module sleeps, inject a clock abstraction or use `asyncio.sleep(0)` for cooperative yields in tests. Never call `time.sleep(...)` inside test code with a non-zero delay.
4. **Never use `@pytest.mark.skip` to hide a slow test.** A timeout problem is a design problem. Fix the design.

---

## Portfolio admission before adding a test

Search the nearest existing policy, public scenario, and assertion before
creating a test. For the affected control family, record one explicit
`REMOVE`, `MERGE`, `REPLACE`, or `KEEP` decision. Admit new recurring
coverage only when it has distinct fault sensitivity, uses the cheapest
sufficient layer, has an owner and trigger, and declares marginal cost,
inspectable evidence, and a lifecycle review condition.

An addition to the default lane must be budget-neutral through removal,
consolidation, or replacement. Do not raise the 60-second aggregate ceiling,
move cost to an unowned lane, delete a unique defense merely to obtain green,
or use repository size, coverage percentage, or a fixed test count as a
portfolio-selection proxy. External/platform checks belong in an owned
triggered profile; perceptual/editorial decisions belong to human review;
non-repeatable migration or credential probes belong to preserved one-off
evidence. See
[Verification Policy: Bounded policy portfolio](../ralph-workflow-policy/verification-policy.md#bounded-policy-portfolio)
for the canonical composition and evidence contract.

## Tests must test behavior, not implementation

| Good | Avoid |
|------|-------|
| Phase transitions, `PipelineEvent` variants emitted | Private attribute mutations, internal buffer sizes |
| Checkpoint round-trips through the public seam | Which internal method was called |
| Observable output via captured stdout / Rich console | Cache hit counters, task handle lifetimes |
| Effect variants returned by the orchestrator | Which branch of an `if` was taken |

If changing the implementation (without changing behavior) would break a test, **the test is wrong — rewrite it.**

### Agent checklist

- [ ] Every test in the affected area finishes in < 1 s individually
- [ ] Combined run of ALL test suites, as executed by `make verify`, completes in < 60 s total
- [ ] No test calls `time.sleep(N)` with `N > 0` or polls real wall-clock time
- [ ] No test reaches through a boundary into real I/O (filesystem, subprocess, network)
- [ ] Every test asserts on observable behavior, not internal state
- [ ] Any refactor needed to make code testable within time is done — not deferred
- [ ] No bypass audit violation detected in lint, typecheck, or test policy

---

## Test pyramid by page family

| Family | Location | Real I/O? | Run |
|--------|----------|-----------|-----|
| Suite-routing fast path | Static `tests/test_makefile_verification_workflow.py`, `tests/test_test_suites.py`, `tests/test_test_suites_orchestration.py` | No | `make -C ralph-workflow test-fast` |
| Unit | `tests/` root, `tests/unit/` | No | `make test-unit` |
| Integration | `tests/integration/` | No | `make test-integration` |
| Full suite | all tests | Mixed | `make test` |
| Verification | lint + typecheck + `make test` (60 s combined budget) | Mixed | `make verify` |

Use `make -C ralph-workflow test-fast` only for a change confined to the listed suite-routing surfaces. Its static three-file selection and `not subprocess_e2e and not smoke` marker make it deterministic and fail closed, but it is developer feedback, not completion proof. `make -C ralph-workflow verify` remains the only completion gate.

Unit and integration tests must be parallel-safe (`pytest-xdist`). Use `monkeypatch.setenv()` for env mutation — never assign to `os.environ` directly. Use injectable clocks, seed injection, `MemoryWorkspace`, and `asyncio.sleep(0)` for cooperative yields. Never `asyncio.sleep(N)` with N > 0 in tests.

---

## Env-Injection Pattern

The most common parallelism hazard: production code calling `os.environ` directly.

**BEFORE — requires test isolation workaround:**
```python
def test_feature_disabled_by_default() -> None:
    os.environ.pop("RALPH_FEATURE_ENABLED", None)   # mutates process-global state ❌
    cfg = ServiceConfig.from_env()
    assert not cfg.enabled
```

**AFTER — parallel-safe, no env mutation:**
```python
def test_feature_disabled_by_default() -> None:
    cfg = ServiceConfig.from_env_fn(lambda _key: None)
    assert not cfg.enabled

def test_feature_enabled_with_env() -> None:
    env = {"RALPH_FEATURE_ENABLED": "true", "RALPH_FEATURE_URL": "https://x"}
    cfg = ServiceConfig.from_env_fn(env.get)
    assert cfg.enabled
```

**Production pattern:**
```python
@classmethod
def from_env_fn(cls, get: Callable[[str], str | None]) -> "ServiceConfig":
    """Build config from an injected env-lookup function."""
    ...

@classmethod
def from_env(cls) -> "ServiceConfig":
    """Build config from the real process environment."""
    return cls.from_env_fn(os.environ.get)
```

---

## Required doubles and their usage

Use the right double. Never mock domain logic — only mock at architectural boundaries. **Fake** = working in-memory implementation. **Stub** = canned pipeline events. **Spy** = records calls. **Mock** = pre-programmed expectations. **Dummy** = placeholder, never used.

| Operation | Use | Never use |
|-----------|-----|-----------|
| File I/O | `MemoryWorkspace` | `tmp_path`, `open()`, `Path.read_text()` |
| Subprocess execution | `FakeAgentExecutor` + `FakeRun` | `subprocess.run`, `asyncio.create_subprocess_exec` |
| Agent pipeline invocations | `MockAgentInvoker` | Real agent processes |
| Process manager (sync timeout) | `FakeTimeoutPopen` via injected `_pm` | Real subprocess with `time.sleep` |
| Process manager (async liveness) | `FakeControllableAsyncProcess` via injected `_pm` | Real subprocess with `asyncio.sleep` |

### FakeTimeoutPopen — synchronous timeout simulation

```python
from ralph.testing.fake_process import FakeTimeoutPopen
from ralph.process.manager import ProcessManager, ProcessManagerPolicy

def _make_timeout_pm(partial_stdout: bytes = b"") -> ProcessManager:
    pid_iter = itertools.count(1)
    def factory(command, *, cwd, env, stdin, stdout, stderr, start_new_session, text):
        return FakeTimeoutPopen(next(pid_iter), partial_stdout=partial_stdout)
    return ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0, kill_followup_timeout_s=0.0, log_events=False
        ),
        sync_process_factory=factory,
    )

def test_run_process_timeout_includes_context(tmp_path: Path) -> None:
    pm = _make_timeout_pm(partial_stdout=b"before-timeout")
    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process("cmd", cwd=tmp_path, timeout=0.5, _pm=pm)
    assert excinfo.value.timed_out is True
    assert excinfo.value.stdout.strip() == "before-timeout"
```

`FakeTimeoutPopen` raises `TimeoutExpired` on the first `communicate(timeout=T)` call and returns partial stdout on the second. No real clock involved.

### FakeControllableAsyncProcess — async liveness simulation

```python
from ralph.testing.fake_process import FakeControllableAsyncProcess
import asyncio

async def test_async_process_stays_alive_until_event() -> None:
    completion = asyncio.Event()  # not set → process stays running
    proc = FakeControllableAsyncProcess(
        pid=42,
        stdout_data=b"ready\n",
        completion_event=completion,
    )
    # proc.wait() and proc.communicate() block until completion.set()
    # proc.terminate() / proc.kill() both set the event and unblock waiters
    completion.set()
    rc = await proc.wait()
    assert rc == 0
```

Inject into `SubprocessAgentExecutor` or `run_process_async()` via a custom `ProcessManager`:

```python
async def fake_factory(command, *, cwd, env, stdin, stdout, stderr, start_new_session):
    return proc

pm = ProcessManager(
    policy=ProcessManagerPolicy(
        default_grace_period_s=0.0, kill_followup_timeout_s=0.1, log_events=False
    ),
    async_process_factory=fake_factory,
)
executor = SubprocessAgentExecutor(["cmd"], _pm=pm)
```

> **Note:** Use `kill_followup_timeout_s > 0` (e.g., `0.1`) when testing async termination. A value of `0.0` causes `asyncio.wait_for(timeout=0)` to always raise `TimeoutError`.

### FakeAgentExecutor usage

```python
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

def test_scheduler_marks_unit_succeeded() -> None:
    executor = FakeAgentExecutor(
        runs={
            "api-endpoints": FakeRun(
                outputs=["line 1", "line 2"],
                exit_code=0,
                duration_ms=50,
            )
        }
    )
    result = run_scheduler(unit, executor=executor)
    assert result.exit_code == 0
    assert executor.calls[0].unit_id == "api-endpoints"
```

### MemoryWorkspace usage

```python
from ralph.workspace.memory import MemoryWorkspace

def test_checkpoint_round_trip() -> None:
    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Test\n\nPrompt content.")
    save_checkpoint(ws, state)
    restored = load_checkpoint(ws)
    assert restored is not None
    assert restored.phase == state.phase
```

---

## Architecture boundaries

| Layer | What lives here | Test with |
|-------|----------------|-----------|
| Pure orchestration (`pipeline/orchestrator.py`, reducers) | Phase routing, effect selection, transition logic | Unit tests — value-type inputs/outputs, no mocks needed |
| Effect handlers (`phases/`, `agents/`) | Workspace reads/writes, subprocess invocation | Integration tests with `MemoryWorkspace` + `FakeAgentExecutor` |
| Real OS / git | Actual git operations, git system hooks | Git system tests only (`tmp_git_repo`) |

Test phase transitions and effect/event contracts at the public seam — assert on the `Effect` returned by `determine_next_effect(...)`, never on internal call counts or buffer state.

---

## Test structure and naming

Every test follows Arrange-Act-Assert with one behavior per test. Keep Arrange short; extract a named builder helper or pytest fixture if setup exceeds ~10 lines. Use `async def test_...` directly — pytest-asyncio detects and runs them; never use `asyncio.run()` inside test functions.

Name tests by observable behavior, not implementation:

| Good | Avoid |
|------|-------|
| `test_planning_routes_to_development_on_success` | `test_internal_counter_updates` |
| `test_pipeline_transitions_to_failed_when_budget_exhausted` | `test_buffer_management` |
| `test_checkpoint_preserves_phase_after_round_trip` | `test_cache_size_tracking` |

Length assertions are acceptable **only when combined with content checks**: pair `len(calls) == 2` with `calls[0]["phase"] == "planning"` and `calls[1]["phase"] == "development"`. Serialization contracts are persistence contracts — round-trip the JSON through `model_dump_json` and `model_validate_json`.

---

## Common anti-patterns

| Anti-pattern | Fix |
|--------------|-----|
| `os.environ[key] = value` / `del os.environ[key]` in tests | Use `monkeypatch.setenv()` / `monkeypatch.delenv()` |
| `open()` / `Path.read_text()` in unit/integration tests | Use `MemoryWorkspace` |
| `subprocess.run()` / `asyncio.create_subprocess_exec()` in unit/integration tests | Use `FakeAgentExecutor` + `FakeRun` |
| `time.sleep(N)` with N > 0 in tests | Inject clock abstraction; use `asyncio.sleep(0)` for yields |
| `if TYPE_CHECKING` or test-mode boolean parameters in production code | Use dependency injection |
| Testing private attributes / internal state | Test through public APIs |
| Asserting `.len()` without content | Add content assertions |
| `# type: ignore` suppressions in tests | Fix the underlying type issue |
| `@pytest.mark.skip` without a GitHub issue URL | Add URL; fix root cause within one sprint |
| `unittest.mock.patch` on production internals | Inject the dependency instead |

---

## Flaky test policy

A flaky test fails non-deterministically. Flaky tests must not remain in gating paths.

### Protocol

1. **Fix** the root cause (inject clock/random seed, use `tmp_path` isolation, apply env-injection via `monkeypatch`).
2. **Quarantine** if the fix is non-trivial — open a GitHub issue first, then annotate:

```python
@pytest.mark.skip(reason="flaky: https://github.com/org/repo/issues/N — timing-sensitive signal delivery")
def test_something_timing_sensitive() -> None: ...
```

3. **Resolve** the quarantine issue within one sprint.

**Rules:**

- Every `@pytest.mark.skip` attribute must include a `https://` URL.
- A skip without a URL will fail the `make verify` audit.

---

## TDD discipline

**Write the failing test first.** No production code without a red test.

1. **Red** — write a test that describes the new behavior and watch it fail.
2. **Green** — implement the minimal production change to make the test pass.
3. **Refactor** — clean up duplication and structure, keeping tests green.

```python
# Red: write the failing test first — it will fail
def test_pipeline_rejects_empty_agent_chain() -> None:
    state = PipelineState(phase="planning", dev_chain=AgentChainState(agents=[]))
    agents_policy, pipeline_policy, _ = load_default_policy()
    effect = determine_next_effect(state, pipeline_policy, agents_policy)
    assert isinstance(effect, ExitFailureEffect)

# Green: add minimal production logic
# Refactor: simplify / extract helpers
```

---

## Definition of done for testing

- [ ] New behavior is covered by a test that was **red before** the production change.
- [ ] Existing behavior regressions are prevented by focused, targeted tests.
- [ ] All tests pass in **parallel-safe mode**.
- [ ] No new flaky tests introduced; quarantined tests include issue URLs.
- [ ] Test names describe observable behavior, not implementation details.
- [ ] AAA structure is clear; setup does not exceed ~10 lines without a named fixture.
- [ ] Required refactors for testability are done; no test-mode booleans, skip flags, or `open()` calls in unit/integration tests remain.
- [ ] `make verify` completes with no ERROR/WARNING diagnostics.

---

## Documentation quality

- **Single source of truth:** all test strategy lives in `docs/agents/testing-guide.md`.
- **Update docs in the same commit** as the behavior or architecture change.
- **Keep examples runnable.** Remove stale patterns immediately when the production API changes.
- **Every `@pytest.mark.skip` requires an issue URL** (enforced by `make verify`).

<!-- whitebox-ledger:start -->

## White-box coupling: the three detectors and the frozen-ceiling ratchet

The test-policy audit (`ralph-workflow/ralph/testing/audit_test_policy.py`,
detectors in `ralph-workflow/ralph/testing/whitebox_detectors.py`) enforces
that tests assert observable behaviour. It reports three white-box classes on
**every** test file, including files that carry the `subprocess_e2e` marker:

| detector | what it counts |
| --- | --- |
| `setattr_private` | `monkeypatch.setattr` calls whose target is a private production name — both the const-first form (`monkeypatch.setattr("ralph.x._name", v)`) and the name-first form (`monkeypatch.setattr(obj, "_name", v)`). Dunder targets are not private. |
| `private_import` | imports that reach private production internals: a private symbol imported from a `ralph.*` module, a public symbol imported through a private `ralph.*` module path, or a private `ralph.*` module imported wholesale. |
| `private_attr` | private attribute reads in high-confidence contexts: inside an `assert`, a `monkeypatch.setattr` target, or a direct read of a module attribute imported from `ralph.*`. |

**The ratchet.** The frozen inventory lives in
`ralph-workflow/ralph/testing/test_whitebox_baseline.py` as
`WHITEBOX_BASELINE: dict[str, dict[str, int]]` (keys are test paths relative
to the `ralph-workflow/` project root). The counts are a **ceiling, never a
target**:

- a file whose counts stay at or below its ceiling passes;
- a file whose count **rises** above its ceiling fails with `whitebox-regression`;
- a file with a non-zero count that is **absent** from the baseline fails with
  `whitebox-baseline-missing` — a new test file must start at zero white-box
  usage;
- a ceiling for a file that no longer exists retires with the file.

**The whole-tree reconciliation.** The per-file ratchet above only ever sees
ONE file at a time, so it is structurally blind to the drift that matters most
for the ledger below: a baseline row that no longer corresponds to any
coupling. The policy-audit verify step (`policy audit (audit_test_policy)`,
`python -m ralph.testing.audit_test_policy`) therefore also reconciles the
frozen baseline against the whole tree
(`ralph-workflow/ralph/testing/whitebox_reconciliation.py`) and reports three
drift shapes:

| drift | what it means | the fix |
| --- | --- | --- |
| `whitebox-baseline-removed` | the baseline names a test file that no longer exists — a leftover of a deleted file | delete the baseline row |
| `whitebox-baseline-stale` | the file still exists but its coupling has been fully converted away, so the row claims an exception the file no longer needs | delete the baseline row and its ledger entry |
| `whitebox-baseline-unlowered` | the frozen ceiling for a category sits above the file's current count | lower the baseline row to the current count |

That reconciliation runs OUTSIDE the 60-second pytest budget: a whole-tree AST
scan of every test file costs tens of seconds, so it belongs to the
policy-audit verify step and not to a pytest suite. The pytest suite covers
the reconciliation logic itself against fixtures, so the rule is enforced in
both places without spending the immutable budget twice.

**Lowering a ceiling** is a deliberate, reviewed step, never an automatic one.
After a conversion batch, re-emit the inventory with
`uv run python -m ralph.testing.audit_test_policy --emit-baseline` and lower
only the entries whose counts actually fell; an entry may never be raised. A
new file inherits the ceiling of the files it replaced. Lowering the baseline
and updating the ledger are ONE change: re-emit with `--emit-baseline` and
update the matching ledger row in the same edit, because a lowered ceiling
without a matching ledger row — or a ledger row without a lowered ceiling —
leaves the ledger claiming coupling the tree no longer has.

**Allowlist semantics.** Each allowlist skips only its own category:
`_IO_ALLOWLIST` skips the real-I/O check, `_WALL_CLOCK_ALLOWLIST` skips the
wall-clock check, `_SLEEP_ALLOWLIST` skips the sleep check. A file marked
`subprocess_e2e` owns the real-process categories (subprocess/network, real
sleep, real wall-clock, real blocking wait) because real children and real
elapsed time are the subject under test there — the canonical testing policy
states that real `time.sleep()`, real filesystem I/O and real network I/O are
forbidden *"in tests not marked `subprocess_e2e` or `live_agy`"*. The three
white-box detectors are **never** skipped by an allowlist: a marked file is
still white-box checked.

There is no per-line marker grammar. A finding is repaired by injecting a
clock (`Clock` / `FakeClock`), a workspace (`MemoryWorkspace` / `FsWorkspace` /
`tmp_path`), or a public seam — not by annotating the line.

---

## White-box Backlog Ledger

Every test file that still holds a non-zero white-box count is listed below
with its frozen count, the observable-surface gap that keeps it white-box, the
seam that would remove it, and the follow-up owner. The ledger is the
authoritative record of retained white-box coupling: a retained entry without
a ledger row is a defect, and so is a ledger row without a retained entry.
The ratchet and the whole-tree reconciliation are what keep the table honest.

| cluster | files | setattr_private | private_import | private_attr | total | owner |
| --- | --- | --- | --- | --- | --- | --- |
| root | 459 | 395 | 857 | 866 | 2118 | follow-up: backlog white-box conversion unit |
| mcp-bridge | 82 | 23 | 267 | 142 | 432 | mcp-bridge |
| agents | 97 | 34 | 281 | 100 | 415 | agents |
| auto-integrate | 40 | 160 | 37 | 54 | 251 | auto-integrate |
| project-policy | 28 | 4 | 53 | 161 | 218 | project-policy |
| run-loop | 17 | 100 | 27 | 84 | 211 | run-loop |
| display | 21 | 22 | 85 | 75 | 182 | display |
| pipeline | 31 | 70 | 32 | 74 | 176 | pipeline |
| integration | 22 | 21 | 39 | 98 | 158 | integration |
| property | 11 | 0 | 60 | 35 | 95 | property |
| recovery | 28 | 7 | 42 | 32 | 81 | recovery |
| tool-exec | 9 | 0 | 36 | 29 | 65 | tool-exec |
| process | 8 | 5 | 10 | 12 | 27 | process |
| dir:unit | 5 | 4 | 7 | 8 | 19 | follow-up: backlog white-box conversion unit |
| dir:config | 6 | 0 | 7 | 2 | 9 | follow-up: backlog white-box conversion unit |
| dir:git | 1 | 0 | 1 | 0 | 1 | follow-up: backlog white-box conversion unit |
| dir:update_check | 1 | 0 | 0 | 1 | 1 | follow-up: backlog white-box conversion unit |
| dir:workspace | 1 | 0 | 1 | 0 | 1 | follow-up: backlog white-box conversion unit |

### Cluster narratives — observable-surface gap and seam sketch


**run-loop** — *gap:* run-loop collaborators are reachable only as private module functions. *Seam sketch:* publish the remaining run-loop collaborators as public entry points on `ralph.pipeline.run_loop` (or a sibling module) and route the tests through them; the retry-diagnostic cluster already has the `LoopContext.hooks` injection seam to copy. *Follow-up owner:* run-loop public-seam unit.

**pipeline** — *gap:* the pipeline runner's effect/commit collaborators are private module functions, so the tests reach them directly. *Seam sketch:* expose the runner's effect resolution through a public seam (a public runner result object or an injected collaborator) and assert on the public result. *Follow-up owner:* pipeline public-seam unit.

**process** — *gap:* process-manager termination and teardown state is only observable through private attributes. *Seam sketch:* return the termination outcome through a public result object on the manager's public API instead of reading `_`-prefixed fields. *Follow-up owner:* process public-seam unit.

**mcp-bridge** — *gap:* the MCP bridge exposes restart/health transitions only through private handlers and private state. *Seam sketch:* route the continuity assertions through the bridge's public `check_health_and_restart_if_needed()` / `handle_request()` entry points (the E17 conversion already does this) and extend the same treatment to the remaining bridge files. *Follow-up owner:* mcp public-seam unit.

**project-policy** — *gap:* project-policy tests assert on private authority/state fields. *Seam sketch:* return the authority decision through the public readiness/decision surface the policy module already publishes. *Follow-up owner:* policy public-seam unit.

**agents** — *gap:* agent registry and invoke wiring are asserted through private module state. *Seam sketch:* assert through the public registry/catalog API and the public invoke result object. *Follow-up owner:* agent public-seam unit.

**display** — *gap:* display probes read private renderer/criteria helpers. *Seam sketch:* expose the criteria probes as a public probe API or convert the assertions to the public rendered-scene surface. *Follow-up owner:* display public-seam unit.

**integration** — *gap:* integration acceptance tests read private release/cleanup state. *Seam sketch:* assert through the public acceptance/lifecycle result objects. *Follow-up owner:* integration public-seam unit.

**auto-integrate** — *gap:* auto-integrate seams are reached through private gate/resolver functions. *Seam sketch:* publish the gate and resolver decisions through the public auto-integrate seam module and assert on its public decision objects. *Follow-up owner:* auto-integrate public-seam unit.

**tool-exec** — *gap:* tool-exec tests assert private handle/process state. *Seam sketch:* return the execution outcome through the public handle result surface. *Follow-up owner:* tool public-seam unit.

**recovery** — *gap:* recovery tests assert private classifier/controller state. *Seam sketch:* return the classification through the public failure-classifier result. *Follow-up owner:* recovery public-seam unit.

**property** — *gap:* property tests read private runtime counters. *Seam sketch:* expose the counters through the public metrics surface the property contract already defines. *Follow-up owner:* property public-seam unit.

### Per-entry ledger

One row per retained inventory entry. Counts are the frozen ceiling for that
file; `sa` = `setattr_private`, `pi` = `private_import`, `pa` = `private_attr`.

| test file | setattr_private | private_import | private_attr | cluster | observable-surface gap | seam sketch | follow-up owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `tests/_fake_git_ops.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/agents/execution_state/test_generic_child_signal.py` | 0 | 5 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/execution_state/test_opencode_sink_registry_parity.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/execution_state/test_opencode_subagent_visibility.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/execution_state/test_strategy_classes_consolidated.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py` | 0 | 6 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_claude_interactive_real_capture_replay.py` | 0 | 2 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_codex_completion_liveness.py` | 0 | 2 | 4 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_codex_session_id_extraction.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_command_builder_spec.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_completion_bounded_stderr.py` | 1 | 7 | 0 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_completion_quota_source.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_conflict_resolution_activity_profile.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_conflict_resolution_completion_liveness.py` | 0 | 8 | 3 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_cpu_baseline_pruning.py` | 0 | 3 | 14 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_cursor_session_id_extraction.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_dispatch_table_covers_every_transport.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_dropped_lines_reach_the_capture.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_interactive_startup_errors.py` | 0 | 5 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_invoke_dispatch_recipe.py` | 0 | 5 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_invoke_monitor_wiring.py` | 12 | 2 | 5 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_invoke_options.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_kimi_command_builder_and_runtime_resolver.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_kimi_command_for_log.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_kimi_session_id_extraction.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_line_reader_queue_bound.py` | 0 | 4 | 8 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_missing_credentials_preflight.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_native_mcp_private_root_concurrency.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_opencode_prompt_delivered_on_stdin.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pi_command_builder_and_runtime_resolver.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_prompt_echo_line_reader.py` | 0 | 2 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_check_fire_resume_metadata.py` | 0 | 3 | 3 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_drain_after_handle_close.py` | 0 | 1 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_graceful_teardown.py` | 0 | 3 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_graceful_teardown_timing.py` | 2 | 3 | 0 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_line_reader_interrupt_teardown.py` | 0 | 2 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_line_reader_transcript_handle.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_subagent_pid_source_wiring.py` | 0 | 1 | 4 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_terminal_reason_before_signal.py` | 0 | 1 | 3 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_transcript_tailer_wiring.py` | 1 | 3 | 4 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_pty_visible_tui_session_id_promotion.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_reader_stall_lifetime.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py` | 0 | 6 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_runtime_resolver_endpoint_delivery.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_subagent_transcript_tail.py` | 0 | 3 | 8 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_transcript_discovery_concurrent_session.py` | 0 | 4 | 3 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/invoke/test_transcript_thread_single_feed.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_claude_headless_wire_capture.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_claude_interactive_transcript_parser.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_codex_uses_ndjson_base.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_ndjson_base.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_pi_dev_wire_format_spec.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/parsers/test_template_base.py` | 0 | 1 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_add_a_new_agent_blackbox_recipe.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_add_a_new_agent_recipe.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_agent_spawn_detaches_tty.py` | 0 | 5 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_agy_model_resolution.py` | 2 | 0 | 0 | agents | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: agent public-seam unit |
| `tests/agents/test_builtin_spec_consolidation.py` | 0 | 6 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_catalog.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_consolidated_add_invoke_remove.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_display_capabilities.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_dynamic_alias_help_lookup.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_empty_output_diagnostic_lookup.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_generic_command_builder_seam.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_idle_watchdog.py` | 1 | 22 | 20 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_idle_watchdog_3.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_idle_watchdog_4.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_idle_watchdog_workspace_smart_filter.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_invocation_context.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_agent_uses_spec_pty.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_agent_with_recovery_mcp_error.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_agent_with_recovery_typed_failure.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_mcp_merge.py` | 10 | 0 | 0 | agents | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_timeout_integration.py` | 0 | 1 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_invoke_workspace_wiring.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_kimi_dynamic_alias.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_legacy_module_dicts_are_views.py` | 0 | 6 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_no_progress_ceiling_opencode.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_pi_dev_blackbox.py` | 0 | 6 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_pty_child_never_paints_parent_terminal.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_quickstart_recipe_blackbox.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_quota_exhaustion_stops_wait.py` | 5 | 4 | 0 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_register_agent_support.py` | 0 | 8 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_register_cursor.py` | 0 | 5 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_register_kimi.py` | 0 | 5 | 2 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_register_my_agent.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_registration_shim.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_registry_register_unregister.py` | 0 | 6 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_registry_seeds_catalog.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_stream_parsed_agent_activity_invokes_parser_emit_subagent.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_subagent_activity_emission.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_subprocess_executor_quota.py` | 0 | 1 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_support.py` | 0 | 1 | 1 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_terminal_state_restored_on_exit.py` | 0 | 2 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_workspace_change_classifier.py` | 0 | 4 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_workspace_cross_process_coordination.py` | 0 | 8 | 3 | agents | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: agent public-seam unit |
| `tests/agents/test_workspace_event_backpressure.py` | 0 | 3 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/agents/test_workspace_watch_scoping.py` | 0 | 5 | 0 | agents | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: agent public-seam unit |
| `tests/config/test_agents_config_file.py` | 0 | 0 | 1 | dir:config | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/config/test_general_config_same_shape_resumes.py` | 0 | 1 | 0 | dir:config | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/config/test_heartbeat_ceiling_config.py` | 0 | 2 | 0 | dir:config | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/config/test_no_output_at_start_config.py` | 0 | 2 | 0 | dir:config | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/config/test_retention_max_age_config.py` | 0 | 0 | 1 | dir:config | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/config/test_workspace_change_weights_config.py` | 0 | 2 | 0 | dir:config | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/conftest.py` | 1 | 2 | 16 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/display/test_generated_scene_renderer.py` | 0 | 1 | 5 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/display/test_generated_scene_salience.py` | 0 | 1 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/display/test_operator_display_labels.py` | 0 | 2 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/git/test_git_output_limit_bytes.py` | 0 | 1 | 0 | dir:git | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/integration/_multimodal_e2e_fixtures.py` | 0 | 4 | 1 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_cli_plumbing_uses_factory.py` | 0 | 1 | 4 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_codex_home_cap_eviction.py` | 0 | 0 | 11 | integration | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: integration public-seam unit |
| `tests/integration/test_codex_home_cleanup.py` | 0 | 3 | 23 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_codex_home_live_sessions.py` | 0 | 1 | 9 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_codex_home_release_path.py` | 0 | 5 | 24 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_commit_cleanup_rock_solid.py` | 0 | 2 | 0 | integration | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: integration public-seam unit |
| `tests/integration/test_lifecycle_restart_acceptance.py` | 0 | 6 | 14 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_mcp_exec_result_contract.py` | 0 | 3 | 2 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_multimodal_default_client_surface.py` | 0 | 3 | 0 | integration | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: integration public-seam unit |
| `tests/integration/test_parallel_multimodal_runtime_e2e.py` | 0 | 0 | 1 | integration | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: integration public-seam unit |
| `tests/integration/test_parallel_resume.py` | 1 | 0 | 0 | integration | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: integration public-seam unit |
| `tests/integration/test_parallel_worker_bootstrap.py` | 3 | 0 | 1 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_pipeline_iterations.py` | 1 | 0 | 0 | integration | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: integration public-seam unit |
| `tests/integration/test_plumbing_pipeline_core.py` | 2 | 1 | 0 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_plumbing_shared_deps.py` | 6 | 1 | 4 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/integration/test_process_zombie_cleanup.py` | 0 | 5 | 0 | integration | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: integration public-seam unit |
| `tests/integration/test_retained_integration_acceptance.py` | 0 | 3 | 0 | integration | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: integration public-seam unit |
| `tests/integration/test_runner_default_display.py` | 3 | 0 | 0 | integration | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: integration public-seam unit |
| `tests/integration/test_single_agent_e2e.py` | 4 | 0 | 0 | integration | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: integration public-seam unit |
| `tests/integration/test_transcript_end_to_end.py` | 1 | 0 | 0 | integration | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: integration public-seam unit |
| `tests/integration/test_upstream_media_memory_regression.py` | 0 | 1 | 4 | integration | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: integration public-seam unit |
| `tests/mcp/_e72_footprint_helper.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/_lifecycle_tool_hash_seams.py` | 0 | 3 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/e72_descendant_helper.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_agent_transport_probe.py` | 1 | 0 | 0 | mcp-bridge | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: mcp public-seam unit |
| `tests/mcp/test_boot_allowance_lifecycle.py` | 0 | 3 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_bounded_catalog_publish.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_attempt_vs_budget.py` | 0 | 3 | 3 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_e1_e2_edge_cases.py` | 0 | 11 | 11 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_e3_e4_edge_cases.py` | 0 | 2 | 5 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py` | 8 | 0 | 0 | mcp-bridge | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_handover_step_errors.py` | 0 | 4 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_bridge_make_before_break.py` | 0 | 6 | 6 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_codex_config_synthesis.py` | 1 | 2 | 0 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_concurrent_upstream_discovery_within_aggregate_budget.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_discovery_aggregate_budget.py` | 0 | 5 | 4 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e14_duplicate_submission_once.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_e16_store_interleave_real_children.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_e17_state_continuity.py` | 0 | 14 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e18_old_session_id_accepted.py` | 0 | 3 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_e24_fresh_stage_id.py` | 0 | 3 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e24_parent_owned_env.py` | 0 | 2 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py` | 0 | 6 | 3 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e26_broken_workspace_package.py` | 0 | 2 | 6 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e33_listening_fd_real_child.py` | 0 | 0 | 1 | mcp-bridge | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: mcp public-seam unit |
| `tests/mcp/test_e3x_transport_gap_typed_retry.py` | 0 | 7 | 14 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e42_two_supervisors_one_replacement.py` | 1 | 4 | 4 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e54_bridge_zombie.py` | 0 | 3 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_e64_bound_dominance_proof.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_e71_enospc_diagnostics_fail_open.py` | 0 | 1 | 6 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_e72_footprint_real_child.py` | 0 | 3 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_effective_config_fingerprint.py` | 1 | 3 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_fallback_standalone_health_route.py` | 0 | 1 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_fallback_standalone_server_close.py` | 1 | 3 | 0 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_handover_inflight_real_child.py` | 0 | 8 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_host_contention_current_signal.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_host_contention_signal_producers.py` | 0 | 8 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_host_contention_signal_round_trip.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_inherited_fd_invalid_fails_closed.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_lifecycle_e16_store_claims.py` | 0 | 12 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_lifecycle_tool_hash_and_classification.py` | 0 | 5 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_activity_relay.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_activity_relay_sequence_anomaly.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_activity_sink.py` | 0 | 7 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_bridge_avoids_killing_under_contention.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_endpoint_never_refuses.py` | 0 | 2 | 3 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_health_event_bridge_driven.py` | 1 | 3 | 0 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_health_event_recorded.py` | 0 | 2 | 5 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_relay_fault_health.py` | 0 | 6 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_server_last_stage_when_stage_file_missing.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_server_slow_pre_dispatch.py` | 0 | 4 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_server_startup_stages_observable.py` | 0 | 3 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_slow_startup_served_from_backlog.py` | 0 | 4 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_mcp_stage_attribution_real_child.py` | 0 | 3 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_md_references.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_md_spec.py` | 0 | 2 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_parent_owned_spawn_env.py` | 0 | 5 | 8 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_preflight_error_carries_stage_and_elapsed.py` | 0 | 6 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_probe_overrun_self_measure.py` | 0 | 5 | 7 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_replacement_public_paths.py` | 0 | 4 | 5 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_replacement_shutdown_signals_real_child.py` | 0 | 4 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_reserved_socket_held_across_respawn.py` | 0 | 2 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_respawn_no_discovery.py` | 0 | 1 | 7 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_root_cause_real_child_bridge.py` | 0 | 9 | 13 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py` | 0 | 3 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_runtime_blocked_workspace_read_surfaces_typed_error.py` | 1 | 0 | 0 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_runtime_respawn_precheck.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_s1_independent_counters_contract.py` | 0 | 2 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_session_id_persisted_across_respawn.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_single_acceptor_instrumented.py` | 6 | 3 | 1 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_spawn_early_failure_typed.py` | 0 | 2 | 3 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_start_stage_tracker_fresh_spawn.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_startup_stage_recorder.py` | 2 | 2 | 0 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_startup_stalled_stage_io.py` | 0 | 6 | 2 | mcp-bridge | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: mcp public-seam unit |
| `tests/mcp/test_startup_watch.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_supervisor_tick_lateness.py` | 0 | 3 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_tool_webvisit_summary.py` | 0 | 0 | 1 | mcp-bridge | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: mcp public-seam unit |
| `tests/mcp/test_whole_startup_budget_production.py` | 0 | 6 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_wire_ledger_fail_closed.py` | 0 | 1 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_wire_ledger_incremental.py` | 0 | 4 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/test_wire_ledger_lock_contention.py` | 0 | 5 | 0 | mcp-bridge | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: mcp public-seam unit |
| `tests/mcp/websearch/test_brave_backend.py` | 0 | 0 | 1 | mcp-bridge | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: mcp public-seam unit |
| `tests/pipeline/_terminal_mcp_error_seams.py` | 0 | 2 | 1 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_development_phase_missing_plan_handoff_status_bar_visible.py` | 1 | 3 | 2 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_integration_resolution_dispatch_inventory.py` | 0 | 0 | 9 | pipeline | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_interrupt_reports_the_state_reached.py` | 2 | 0 | 3 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_kimi_smoke_harness.py` | 0 | 3 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_next_attempt_gets_fresh_bridge.py` | 0 | 1 | 1 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_phase_close_failed_no_artifact.py` | 2 | 1 | 3 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_planning_commit_recovery.py` | 4 | 0 | 5 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_planning_requires_target_sync.py` | 3 | 0 | 10 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_reducer_lock_retry.py` | 0 | 1 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_run_loop_cleanup_shutdown.py` | 0 | 2 | 0 | run-loop | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_cycle_timebox_wiring.py` | 2 | 0 | 1 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_interrupt.py` | 2 | 2 | 0 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_phase_entry_prefers_available_agent.py` | 0 | 0 | 11 | run-loop | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_recovery_surface.py` | 0 | 3 | 0 | run-loop | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_resume_prefers_available_agent.py` | 34 | 0 | 33 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_status_bar_wiring.py` | 4 | 7 | 0 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_unavailable_surface.py` | 0 | 1 | 0 | run-loop | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_waiting_state_logs.py` | 0 | 2 | 0 | run-loop | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_loop_waiting_state_real_controller.py` | 0 | 4 | 0 | run-loop | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: run-loop public-seam unit |
| `tests/pipeline/test_run_time_report_shutdown.py` | 2 | 0 | 1 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_runner_missing_plan_handoff_recovery.py` | 0 | 3 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_terminal_mcp_error_per_entry_path.py` | 0 | 4 | 4 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/pipeline/test_terminal_mcp_error_supervisor_ordering.py` | 0 | 3 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/process/monitor/test_dispatch_all_transports.py` | 0 | 2 | 0 | process | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: process public-seam unit |
| `tests/process/test_spawn_env_sanitizer.py` | 4 | 0 | 0 | process | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: process public-seam unit |
| `tests/project_policy/test_analysis.py` | 0 | 0 | 1 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_authentic_v4_consent_flow.py` | 0 | 2 | 7 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_citation_workspace_restore.py` | 0 | 0 | 2 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_claim_scope_column_sweep.py` | 0 | 5 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_claim_scope_guard_rows.py` | 0 | 3 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_cli_integration_helpers.py` | 0 | 8 | 7 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_guard_inventory_contract.py` | 0 | 2 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_pending_marker.py` | 0 | 1 | 9 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_auto_commit.py` | 0 | 1 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_handback_channel_ledger.py` | 0 | 2 | 2 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_handback_ready_route.py` | 0 | 1 | 7 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_handback_to_pipeline.py` | 1 | 3 | 26 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_handback_working_tree.py` | 0 | 2 | 31 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_policy_never_blocks_the_run.py` | 0 | 1 | 4 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_prompt_ui.py` | 3 | 1 | 0 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_readiness_assessment.py` | 0 | 5 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_ready_cache_freshness.py` | 0 | 0 | 7 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_remediation.py` | 0 | 0 | 9 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_run_integration.py` | 0 | 1 | 15 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_skip_inline_policy_prompt.py` | 0 | 4 | 9 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_starter_enforcement_prose.py` | 0 | 0 | 2 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_starter_living_document_contract.py` | 0 | 0 | 1 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_three_state_readiness_emission.py` | 0 | 2 | 6 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_typechecking_ratchet_inventory.py` | 0 | 3 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/project_policy/test_v4_corpus_authentic.py` | 0 | 0 | 1 | project-policy | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: policy public-seam unit |
| `tests/project_policy/test_v5_kernel.py` | 0 | 1 | 9 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_validator.py` | 0 | 1 | 6 | project-policy | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: policy public-seam unit |
| `tests/project_policy/test_verification_ratchet_inventory.py` | 0 | 4 | 0 | project-policy | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: policy public-seam unit |
| `tests/recovery/test_agent_cooldown_enforcement.py` | 0 | 0 | 1 | recovery | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: recovery public-seam unit |
| `tests/recovery/test_agent_empty_output.py` | 0 | 1 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_agent_unavailable_tracker.py` | 0 | 4 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_agy_incomplete_exit_recovery.py` | 0 | 1 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_all_agents_unavailable_never_crashes.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_broken_agent_no_output_loop_bounds.py` | 1 | 1 | 1 | recovery | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: recovery public-seam unit |
| `tests/recovery/test_broken_agent_single_agent_chain_loop_exit.py` | 0 | 0 | 1 | recovery | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: recovery public-seam unit |
| `tests/recovery/test_classifier_typed_cause_chain_child_alive.py` | 0 | 4 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_controller_all_agents_unavailable_never_exits.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_controller_source_parse_cache.py` | 1 | 0 | 16 | recovery | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: recovery public-seam unit |
| `tests/recovery/test_failure_classifier.py` | 1 | 0 | 0 | recovery | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: recovery public-seam unit |
| `tests/recovery/test_harness_echo_does_not_advance_meaningful_output.py` | 0 | 0 | 2 | recovery | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: recovery public-seam unit |
| `tests/recovery/test_no_progress_quiet.py` | 0 | 5 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_opencode_resumable_exit_classification.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_opencode_resumable_exit_producer_path.py` | 0 | 1 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_out_of_credits_fast_fallover.py` | 0 | 0 | 1 | recovery | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: recovery public-seam unit |
| `tests/recovery/test_preflight_validation.py` | 1 | 0 | 0 | recovery | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: recovery public-seam unit |
| `tests/recovery/test_priority_agent_selection.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_prompt_scenario_unavailability.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py` | 0 | 6 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_retry_backoff.py` | 0 | 0 | 7 | recovery | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: recovery public-seam unit |
| `tests/recovery/test_runner_offline_pause_resume.py` | 1 | 0 | 0 | recovery | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: recovery public-seam unit |
| `tests/recovery/test_same_shape_retry_bounds.py` | 0 | 1 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_two_main_retry_rules.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_two_state_invariant.py` | 0 | 2 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_unavailability_reason.py` | 0 | 3 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/recovery/test_user_interrupt.py` | 2 | 0 | 3 | recovery | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: recovery public-seam unit |
| `tests/recovery/test_watchdog_fire_record.py` | 0 | 1 | 0 | recovery | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: recovery public-seam unit |
| `tests/test_activity_feed_paths_converge.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_activity_router_subagent_progress_dispatch.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agent_internal_paths.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agent_registry.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agent_registry_agy_model_alias.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agents_invoke.py` | 0 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_agy_completion_adversarial.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_empty_output_failure.py` | 0 | 1 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_agy_harness_with_mock.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_live_regression.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_parser.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_parser_to_harness_pipeline.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_plumbing_mock.py` | 0 | 8 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_agy_pty_invocation.py` | 8 | 2 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_agy_registry_alias_resolution.py` | 7 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_agy_runtime_home_auth.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_api_opencode.py` | 2 | 0 | 11 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_artifact_format_docs.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_artifact_write_durability.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_asyncio_bridge_install_signal_handlers.py` | 0 | 0 | 15 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_asyncio_bridge_signal_bridge.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_atomic_write_if_changed.py` | 0 | 0 | 12 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_attempt_fault.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_agent_module_state.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_artifact_submission_canonical_path.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_artifact_submission_canonical_types_sync.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_canonical_session_text.py` | 2 | 0 | 7 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_cast_policy.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_di_seam_enforcement.py` | 0 | 0 | 7 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_audit_filesystem_write_consolidation.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_fsevents_watch_consolidation.py` | 1 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_lint_bypass.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_mcp_timeout.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_opts_key_drift.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_audit_parallelization_dormant.py` | 2 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_resource_lifecycle.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_skill_auto_commit.py` | 5 | 0 | 10 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_skill_mirror_freshness.py` | 3 | 3 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_template_render_integrity.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_terminal_escape_containment.py` | 11 | 0 | 5 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_audit_typecheck_bypass.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_audit_watchdog_drift.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_audit_workspace_resource_inventory.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_authority_audit_invariants_3_4_10.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_authority_audit_scenario_1_2_worktree_target.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_authority_audit_scenario_3_4_9_restart_retry.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_authority_audit_scenario_5_6_lifecycle_publication.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_authority_audit_scenario_7_8_10_entrypoints_display.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_auto_integrate_acceptance_scenarios_e2e.py` | 0 | 1 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_boundary_cleanliness.py` | 1 | 0 | 2 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_boundary_freshness.py` | 4 | 4 | 0 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_boundary_refresh.py` | 11 | 0 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_catchup.py` | 7 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_catchup_e2e.py` | 2 | 0 | 2 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_catchup_regressions.py` | 3 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_catchup_remote_coordination.py` | 10 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_conflict_budget.py` | 1 | 1 | 0 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_containment_release.py` | 10 | 1 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_default_policy_seam.py` | 0 | 0 | 2 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_drift_recovery.py` | 1 | 1 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_fail_closed_e2e.py` | 4 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_gates.py` | 3 | 1 | 4 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_log_routing.py` | 0 | 0 | 3 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_non_main_target.py` | 8 | 1 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_parallel_wiring.py` | 0 | 0 | 7 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_parallel_worker_seam.py` | 1 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_race.py` | 4 | 0 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_record_idempotent.py` | 0 | 1 | 0 | auto-integrate | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_record_loader_lease.py` | 0 | 13 | 0 | auto-integrate | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_recovery.py` | 2 | 4 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_recovery_git_state_reflog.py` | 0 | 1 | 0 | auto-integrate | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_recovery_git_state_reflog_unit.py` | 0 | 1 | 0 | auto-integrate | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_refresh_contract.py` | 5 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_remote_push.py` | 0 | 5 | 0 | auto-integrate | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_remote_refresh.py` | 15 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_remote_sync_pull.py` | 4 | 0 | 4 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_remote_sync_push.py` | 3 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_remote_sync_reconcile.py` | 20 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_resolution.py` | 2 | 1 | 1 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_resolving_rebase_record.py` | 1 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_retained_recovery_seam.py` | 13 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_runner_seam.py` | 7 | 0 | 3 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_seams_e2e.py` | 0 | 0 | 4 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_skip_reasons.py` | 8 | 1 | 0 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_stateless_seam.py` | 8 | 0 | 13 | auto-integrate | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_toml_discoverability.py` | 0 | 0 | 1 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_workspace_context_end_to_end.py` | 2 | 0 | 0 | auto-integrate | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: auto-integrate public-seam unit |
| `tests/test_auto_integrate_worktree_sync.py` | 0 | 0 | 1 | auto-integrate | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: auto-integrate public-seam unit |
| `tests/test_bootstrap_auto_seed_git_exclude.py` | 1 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_bootstrap_auto_seed_gitignore.py` | 1 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_bounded_text_buffer.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_bridge_lifetime_primitive.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_busy_refusal_state_factory.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_canonical_artifact_submit.py` | 2 | 1 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_capability_state_idempotent.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_checkpoint_idempotent.py` | 0 | 0 | 6 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_claude_interactive_parser.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_claude_interactive_pty.py` | 1 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_claude_interactive_session_resume.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_claude_interactive_timeout_reason.py` | 0 | 5 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commands_2.py` | 2 | 1 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commands_init.py` | 4 | 4 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commands_run_capability_warning.py` | 0 | 3 | 5 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commands_run_mcp_preflight.py` | 2 | 2 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commands_run_skill_sync.py` | 22 | 6 | 20 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_commit_command.py` | 0 | 1 | 10 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_config_error_envelope.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_cli_diagnose_path.py` | 2 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_force_init_skills.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_cli_init_auto_skill_install.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_cli_init_skill_root_coverage.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_cli_run_skill_sync_warnings.py` | 3 | 0 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_smoke.py` | 1 | 2 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cli_workspace_health.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_codex_inline_image_roundtrip.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_codex_transport_reaches_the_session.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_attempt_clears_receipt.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_cleanup_artifact.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_cleanup_diff.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_cleanup_invariants.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_cleanup_verify_gate.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_completion_authority.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_detached_head_recovery.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_executor.py` | 2 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_commit_message_pipeline.py` | 3 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_commit_plumbing_transport_tools.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_commit_plumbing_uses_canonical_submit.py` | 0 | 1 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_commit_retry_cooldown.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_completion_invocation_exit_logging.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_completion_receipts.py` | 14 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_completion_run_id_from_env.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_completion_sentinel.py` | 0 | 0 | 19 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_config_auto_commit.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_config_bootstrap.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_budget_durability.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_budget_typed_setter.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_attempt_attribution.py` | 5 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_cross_path_budget.py` | 6 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_futile_loop.py` | 9 | 1 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_lifecycle.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_liveness_matrix.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_outcome_judging.py` | 5 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_phase_parity.py` | 11 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_pipeline.py` | 11 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_rebase_loop.py` | 16 | 0 | 9 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_relay_failure.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_resume.py` | 8 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_shared_ledger.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_conflict_resolution_worktree_rescan.py` | 2 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_conftest_process_manager_teardown.py` | 4 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cursor_concurrent_invocations.py` | 1 | 3 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cursor_runtime_auth_preservation.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_cycle_baseline_diff_cycle_baseline_diff.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_cycle_survives_recovery_hops.py` | 0 | 2 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_deterministic_writers_clean_tree.py` | 1 | 3 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_developer_prompt_subagent_guidance.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_diagnostic_evidence_gate_reason.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_diagnostic_transition_helpers.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_direct_mcp_recovery.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_display_context.py` | 0 | 1 | 2 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/test_display_docs_salience_model.py` | 0 | 0 | 2 | display | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: display public-seam unit |
| `tests/test_display_lifecycle_label_taxonomy.py` | 0 | 1 | 1 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/test_display_no_module_globals_injected_display_context_is_honored.py` | 0 | 2 | 1 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/test_display_phase_lifecycle_run_completion_model.py` | 0 | 1 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/test_display_visual_floor.py` | 0 | 5 | 22 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/test_driver_events.py` | 0 | 3 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_evidence_provenance_lattice.py` | 1 | 9 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_exec_job_integration.py` | 0 | 8 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_exec_liveness_evidence_window.py` | 0 | 2 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_exec_liveness_scenarios.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_exec_progress_notifications.py` | 0 | 13 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_exec_smoke_subprocess_e2e.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_exec_stringified_argv_tools_call_subprocess_e2e.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_executor_process.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_executor_process_subtree_reaping.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_exhaustion_does_not_invoke_recovery_preamble.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_expired_cycle_blocks_recovery_retries.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_explore_audit_register.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_bench.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_bench_gates.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_bench_query_warmup.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_bench_r6_cli.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_bench_rss_isolation.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_cold_start_lifecycle.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_dirty_paths.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_edit_indexed.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_fault_matrix.py` | 1 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_explore_fault_matrix_full.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_fault_matrix_full_late.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_freshness_guard.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_graph.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_grep_indexed.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_handlers.py` | 0 | 0 | 22 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_explore_legacy_shape_regressions.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_localized_change_invariant.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_perf_regression_gate.py` | 0 | 14 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_pipeline.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_explore_real_codebase.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_rebuild_equivalence.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_reindex_bench.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_resource_lifecycle.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_search_read_indexed.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_serving_metadata.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_store.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_explore_tool_descriptions.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_failure_marker_single_source_of_truth.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_failure_origin_causal_chain.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_fan_out_authority.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_filesystem_activity_baseline.py` | 0 | 3 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_filesystem_criterion_catalog.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_filesystem_scenario_baseline.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_format_docs_idempotent.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_fs_health_process_manager.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_fs_workspace_idempotent_write.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_fsevents_write_conversions.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_generate_commit_canonical_path.py` | 0 | 2 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_git_call_policy.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_git_command_queue.py` | 2 | 0 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_git_command_queue_lease_clock.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_git_commit_cleanup.py` | 3 | 0 | 6 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_git_lock_liveness_probe.py` | 2 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_git_lock_real_processes.py` | 2 | 0 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_git_lock_recovery.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_git_lock_recovery_regressions.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_git_operation_authority.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_git_operations.py` | 2 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_git_read_timeout.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_git_subprocess_runner_unreported_returncode.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_git_termination_floor.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_guidelines.py` | 1 | 0 | 6 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_harness_run_diagnosis.py` | 7 | 1 | 55 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_idempotent_write.py` | 0 | 0 | 5 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_in_memory_transport_round_trip.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_inline_image_needs_an_actual_image.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_install.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_install_cli.py` | 5 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_install_conflicts.py` | 0 | 2 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_install_snapshot.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_install_spawn_env.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_install_transaction.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_action.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_actions.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_clock_and_reasons.py` | 7 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic.py` | 3 | 1 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_evidence.py` | 0 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_exhaustion.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_intervention_cap.py` | 0 | 2 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_livelock.py` | 1 | 10 | 5 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_proof.py` | 0 | 1 | 10 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_resolver_health.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_diagnostic_worktree_isolation.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_fault_catalogue.py` | 7 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_integration_fault_model.py` | 7 | 2 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_gate_parity.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_integration_obstruction_real_git.py` | 1 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_owner.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_owner_config.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_phase_proof.py` | 0 | 1 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_public_entry_trace.py` | 6 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_integration_record_legacy_migration.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_remote_ops.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_integration_resolution.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_integration_success_state.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_internal_skills_mcp_prompts.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_interrupt.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_interrupt_cli_helper.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_interrupt_dispatcher.py` | 2 | 1 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_kimi_wire_provenance.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_launch_payload_ceiling.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_lifecycle_acceptance.py` | 0 | 5 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_lifecycle_bounded_response.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_list_directory_recursive_bounded.py` | 2 | 1 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_live_post_tool_result_wedge.py` | 0 | 5 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_live_tool_unavailable_recovery.py` | 1 | 3 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_liveness_helpers.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_liveness_pa002_properties.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_liveness_properties.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_liveness_sampler.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_liveness_termination.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_makefile_verification_workflow.py` | 0 | 0 | 7 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_managed_agent_sessions.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_master_prompt.py` | 3 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_master_prompt_file_idempotent.py` | 0 | 0 | 11 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_agent_boundary_causal_chain.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_contract_single_source_of_truth.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_endpoint_functional_sweep.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_factory_impl.py` | 0 | 1 | 12 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_lifecycle.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_lifecycle_pythonpath_isolation.py` | 0 | 0 | 7 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_lifecycle_session_file_cleanup.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_restart_budget_rolling.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_restart_policy_default_cooldown.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_saturated_dispatch_bounded_result.py` | 0 | 1 | 7 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_error_enriched.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_exec_result_contract.py` | 0 | 5 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_exec_streaming_concurrency.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_exec_streaming_post_final_frame.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_fallback_handler_hardening.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_file_backed_session_capability_profile.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_handler_error_safety.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_http_serialization_safety.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_lifecycle_server_output_persisted.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_load_runtime_upstream_servers.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_owner_exit.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_schema_flavor.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_tool_registry_reset_cap.py` | 0 | 2 | 6 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_tools_list_aliases.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_server_unreachable_upstream_strict_mode.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_startup.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_supervisor.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_tool_artifact.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_tool_json_repair_boundary.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_transport.py` | 0 | 2 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_transport_close_failure.py` | 0 | 1 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_mcp_wire_ledger.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_md_artifact_editing.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_md_artifact_staging.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_media_degradation_warning.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_media_io_periodic_prune.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_media_manifest_retention.py` | 0 | 2 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_media_tools_read_the_corrected_profile.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_model_flag_parser.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_multimodal_evidence.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_multimodal_prompt.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_no_anti_drift_recovery_invariants.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_no_anti_drift_regression.py` | 1 | 2 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_no_prose_driven_reducer_control_flow.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_no_worker_role_in_prompts.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_operation_evidence.py` | 1 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_operation_evidence_fingerprint.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_parallel_display.py` | 0 | 0 | 7 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_parallel_display_preserves_log_tags.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_parallel_display_subscriber_forwarding.py` | 0 | 0 | 5 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_payload_identity_is_read_once.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pending_merge_commit_recovery.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pending_merge_repair.py` | 4 | 4 | 8 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_pending_merge_repair_real_git.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pending_merge_repair_resolver_health.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_persisted_registry_row_cannot_pick_the_block.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_phase_and_commit_sessions_are_wired.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_phase_process_cleanup.py` | 0 | 0 | 15 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_phase_raw_log_break_reporting.py` | 3 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_phase_transport_failure_reporting.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_phases_commit_cleanup.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_phases_commit_planning_fix.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_phases_retry_on_stale_session.py` | 1 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_pipeline_core.py` | 0 | 3 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_development_timebox.py` | 0 | 0 | 5 | pipeline | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_factory_default.py` | 2 | 1 | 0 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_orchestration_flow.py` | 1 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner.py` | 26 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_cycle_baseline_lifecycle.py` | 1 | 0 | 5 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_determine_effect.py` | 1 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_empty_commit_effect_execution.py` | 1 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_execute_agent_effect_2_a.py` | 0 | 1 | 4 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_execute_commit_effect.py` | 15 | 0 | 12 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_invoke_agent_drain_clearing.py` | 0 | 1 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_opencode_resume_build_agent_recovery_plan_resumable_session.py` | 1 | 1 | 0 | pipeline | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_phase_cleanup.py` | 0 | 0 | 9 | pipeline | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_pipeline_runner_loop_1.py` | 5 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_runner_timebox_boundaries.py` | 3 | 0 | 0 | pipeline | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_session_bridge.py` | 0 | 1 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/test_pipeline_state_machine.py` | 0 | 3 | 0 | pipeline | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: pipeline public-seam unit |
| `tests/test_plain_renderer_ansi.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_kind_tags.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_phase_close.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_role_driven_levels.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_run_end.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_run_start.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_plain_renderer_streaming_dedup.py` | 0 | 0 | 5 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_plan_handoff_requirements.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_plan_mcp_tool_descriptions.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_planning_obstruction_typed_seam.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_planning_phase_2026_08_06_regression.py` | 1 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_policy_cycle_timebox_validation.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_policy_loader.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_policy_remediation_completion_evidence.py` | 0 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_contract.py` | 9 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_end_to_end.py` | 3 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_heartbeat.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_hooks.py` | 15 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_prompt.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_state_query.py` | 2 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_pro_support_watcher.py` | 0 | 0 | 2 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_process_cross_platform_hardening.py` | 0 | 1 | 3 | process | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: process public-seam unit |
| `tests/test_process_hardening.py` | 0 | 1 | 4 | process | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: process public-seam unit |
| `tests/test_process_manager_failure_state.py` | 0 | 1 | 0 | process | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: process public-seam unit |
| `tests/test_process_manager_pty.py` | 0 | 0 | 4 | process | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: process public-seam unit |
| `tests/test_process_manager_wait_procs_oserror.py` | 1 | 4 | 1 | process | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: process public-seam unit |
| `tests/test_process_termination_outcomes_bound.py` | 0 | 1 | 0 | process | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: process public-seam unit |
| `tests/test_product_criteria_idempotent.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_change_detection.py` | 0 | 1 | 7 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_endpoint_advertising.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_materialize_2.py` | 3 | 0 | 4 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_materialize_3.py` | 6 | 0 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_materialize_partial_development_result.py` | 2 | 1 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_prompt_payload_idempotent.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_prompts_cycle_deadline_publication.py` | 1 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_property_a_one_transport_one_behavior.py` | 0 | 2 | 1 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_c_liveness_contract.py` | 0 | 10 | 13 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_d_failure_observability.py` | 0 | 10 | 4 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_e_streaming_terminates.py` | 0 | 11 | 3 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_f_retry_side_effects.py` | 0 | 1 | 0 | property | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: property public-seam unit |
| `tests/test_property_g_recovery_signal.py` | 0 | 11 | 1 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_h_bounded_resources.py` | 0 | 5 | 13 | property | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: property public-seam unit |
| `tests/test_property_i_timing_safety.py` | 0 | 2 | 0 | property | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: property public-seam unit |
| `tests/test_property_k_trust_boundary.py` | 0 | 4 | 0 | property | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: property public-seam unit |
| `tests/test_property_l_zero_progress_and_resume.py` | 0 | 2 | 0 | property | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: property public-seam unit |
| `tests/test_property_n_spill_inside_workspace.py` | 0 | 2 | 0 | property | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: property public-seam unit |
| `tests/test_pty_line_reader_pending_bound.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pty_line_reader_session_capture.py` | 0 | 4 | 9 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_pty_linux_fixes.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pty_transcript_path_resolution.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_pty_transcript_tool_result.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_conflict_done_landing_pending.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_crash_restart_matrix.py` | 5 | 5 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_entry_point_matrix.py` | 0 | 5 | 11 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_exhaustion_restart.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_remote_unavailable.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_public_workflow_target_movement.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_publication_decision_setter.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_publication_state_machine.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_raw_capture_guards_are_pinned.py` | 4 | 6 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_raw_overflow_display_writes_stay_jsonl.py` | 0 | 2 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_raw_transcript_corruption_detection.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_rebase_first_order.py` | 13 | 6 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_rebase_outcome_kind.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_rebase_worktree_head_ref.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_recovery_classifier_tool_availability.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_recovery_release_uses_record_store.py` | 0 | 12 | 3 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_recovery_three_caps_distinguished.py` | 2 | 3 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_remote_boundary_and_landing.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_repetition_tracker.py` | 0 | 0 | 9 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_resolution_action.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_resolution_failure_cause_contract.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_resolve_retry_intent.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_restart_from_scratch_detector.py` | 0 | 3 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_resume_action_aware_prompt.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_retained_resolution_dispatch.py` | 7 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_retained_resolution_handoff.py` | 2 | 6 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_retry_budget_durability.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_retry_progress_guard.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_retry_prompt_summary.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_retryable_failure.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_rfc013_db_close_best_effort.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_run_loop_configured_cap.py` | 12 | 0 | 9 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_decision_delegation.py` | 0 | 1 | 3 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_failed_ladder_terminal.py` | 9 | 0 | 8 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_pro_integration.py` | 30 | 3 | 11 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_resolver_cooldown_wait.py` | 6 | 2 | 4 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_resolver_ledger.py` | 0 | 0 | 3 | run-loop | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: run-loop public-seam unit |
| `tests/test_run_loop_setters_only.py` | 1 | 0 | 1 | run-loop | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: run-loop public-seam unit |
| `tests/test_runner_auto_integrate_seam.py` | 3 | 0 | 16 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_runner_cycle_deadline_revocation.py` | 0 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_runner_decision_delegation.py` | 1 | 2 | 7 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_runner_fan_out_opts_forwarding.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_runner_interrupt.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_runner_mcp_validation.py` | 10 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_runner_phase_transition_banner.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_runner_quiet_mode.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_runner_strategy_state_preserved.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_runtime_tool_metadata_async_dispatch.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_scoped_auto_commit.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_session_resume_single_source_of_truth.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_session_wrapup.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_agent_paths.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_agent_paths_research.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_agent_paths_staleness_audit.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_auto_commit.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_auto_update_no_repair.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_baseline_catalog.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_content.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_docs_mcp_probe.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_docs_mcp_probe_integration.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_install_auto_commit.py` | 0 | 5 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_installer_project.py` | 0 | 15 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_installer_sibling_symlinks.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_installer_update.py` | 6 | 2 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_skills_manager.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_package_skill_names_parity.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_package_sync_script.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_process_view.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_project_paths_research.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_recheck_policy.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_recheck_policy_config.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_skill_resolver.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_state.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_skills_state_store.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_agy_end_to_end.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_agy_full_lifecycle_e2e.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_canonical_path.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_capability_breaks_when_supported_but_unrendered.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_ccs.py` | 0 | 1 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_multimodal_end_to_end.py` | 1 | 0 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_smoke_plumbing_uses_canonical_submit.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_stale_resolution_replay.py` | 1 | 2 | 0 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_startup_timeouts_invariants.py` | 7 | 18 | 14 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_state_db_schema_guard.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_strategy_escalation_e2e.py` | 2 | 0 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_subprocess_agent_executor.py` | 0 | 0 | 12 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_subprocess_agent_executor_teardown.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_subprocess_env_secret_isolation.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_subprocess_reader_resume_safe.py` | 6 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_successful_attempt_captures_session_for_in_session_retry.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_suppression_deadlock_fixed.py` | 4 | 0 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_telemetry_sentry.py` | 1 | 5 | 14 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_telemetry_user_identity.py` | 0 | 7 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_template_engine_renderer_reuse.py` | 1 | 0 | 0 | root | private-attribute-patch | expose the patched attribute as an injected dependency or public config | follow-up: backlog white-box conversion unit |
| `tests/test_template_text_cache.py` | 0 | 2 | 15 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_terminal_authority.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_test_suites.py` | 0 | 0 | 16 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_test_suites_orchestration.py` | 1 | 0 | 12 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_test_suites_subprocess_orchestration.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_text_accumulator_bound.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_timing_safety_transports.py` | 0 | 3 | 14 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_tool_bridge_error_contract.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_bridge_retired_json_tools.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_coordination.py` | 6 | 1 | 10 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_tool_exec_check_version_control.py` | 0 | 2 | 2 | tool-exec | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: tool public-seam unit |
| `tests/test_tool_exec_execution_error.py` | 0 | 3 | 0 | tool-exec | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: tool public-seam unit |
| `tests/test_tool_exec_handle_exec_command.py` | 0 | 2 | 11 | tool-exec | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: tool public-seam unit |
| `tests/test_tool_exec_large_output_spill.py` | 0 | 3 | 0 | tool-exec | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: tool public-seam unit |
| `tests/test_tool_exec_process_cleanup.py` | 0 | 2 | 3 | tool-exec | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: tool public-seam unit |
| `tests/test_tool_exec_resource_uri.py` | 0 | 17 | 0 | tool-exec | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: tool public-seam unit |
| `tests/test_tool_exec_run_command.py` | 0 | 3 | 13 | tool-exec | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: tool public-seam unit |
| `tests/test_tool_exec_shell_command_segments.py` | 0 | 1 | 0 | tool-exec | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: tool public-seam unit |
| `tests/test_tool_exec_wait_cancel.py` | 0 | 3 | 0 | tool-exec | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: tool public-seam unit |
| `tests/test_tool_git_cwd_validator.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_git_read_nested_repo.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_media_format_metadata.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_raw_exec_handle.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_spec_capability_consistency.py` | 0 | 4 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_spec_default_consistency.py` | 0 | 9 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_specs_timeout_advertised.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_unsafe_exec_handle.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_tool_workspace_handle_read_media.py` | 0 | 1 | 1 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_transport_breaker_false_positives.py` | 0 | 6 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_transport_multimodal_status.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_unreadable_record_entry_points.py` | 0 | 4 | 17 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_unsafe_exec_stderr_and_timeout_consistency.py` | 0 | 1 | 5 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_upstream_result_shapes_are_all_normalised.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_validation_error_injection.py` | 0 | 1 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_verify.py` | 5 | 0 | 37 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_visual_capture.py` | 0 | 5 | 2 | root | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/test_visual_capture_command_contract.py` | 0 | 0 | 4 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_visual_capture_lifecycle.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_visual_judgement_tier.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_visual_verdict.py` | 0 | 0 | 1 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_wire_ledger_capture_exporter.py` | 0 | 2 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/test_workspace_fs_fs_workspace_read_lines.py` | 0 | 0 | 5 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_workspace_memory_memory_workspace_init.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_workspace_memory_memory_workspace_normalize.py` | 0 | 0 | 3 | root | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/test_workspace_monitor_never_delays_the_agent.py` | 0 | 3 | 0 | root | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/unit/display/_criteria_probes.py` | 6 | 35 | 9 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/display/test_brief_criteria_coverage.py` | 0 | 2 | 5 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/display/test_crash_output_containment.py` | 0 | 0 | 1 | display | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: display public-seam unit |
| `tests/unit/display/test_display_context.py` | 1 | 5 | 3 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/display/test_frequency_tier_evidence.py` | 0 | 2 | 1 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/display/test_palette.py` | 0 | 17 | 10 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/display/test_palette_resolution_cost.py` | 0 | 3 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/unit/display/test_parallel_display_t22.py` | 0 | 0 | 2 | display | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: display public-seam unit |
| `tests/unit/display/test_salience.py` | 0 | 4 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/unit/display/test_subscriber_prompt_caching.py` | 0 | 0 | 2 | display | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: display public-seam unit |
| `tests/unit/display/test_syntax_theme_monokai_mapping.py` | 0 | 1 | 0 | display | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: display public-seam unit |
| `tests/unit/display/test_terminal_bg_query.py` | 15 | 2 | 9 | display | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: display public-seam unit |
| `tests/unit/test_burst_scheduler.py` | 0 | 1 | 0 | dir:unit | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/unit/test_burst_scheduler_wiring.py` | 2 | 1 | 7 | dir:unit | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/unit/test_explore_pipeline_invariants.py` | 0 | 1 | 0 | dir:unit | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/unit/test_fs_health.py` | 2 | 3 | 1 | dir:unit | mixed | split the coupling: inject the dependency, then assert the public result | follow-up: backlog white-box conversion unit |
| `tests/unit/test_workspace_health_host_watch_pressure.py` | 0 | 1 | 0 | dir:unit | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |
| `tests/update_check/test_update_check_pypi.py` | 0 | 0 | 1 | dir:update_check | private-attribute-assert | return the value through a public result/property instead of the private field | follow-up: backlog white-box conversion unit |
| `tests/workspace/test_workspace_product_baselines.py` | 0 | 1 | 0 | dir:workspace | private-symbol-import | publish a public accessor or promote the private symbol to a public API | follow-up: backlog white-box conversion unit |

### Conversion results in this pass

| test file | white-box sites before | after |
| --- | --- | --- |
| `tests/test_run_loop_retry_diagnostic.py` | 96 | 0 |
| `tests/test_process_manager.py` | 22 | 0 |
| `tests/process/test_process_manager_listener_cap.py` | 8 | 0 |
| `tests/process/test_pty_process_external_returncode.py` | 6 | 0 |
| `tests/process/test_process_manager_terminate_executor.py` | 6 | 0 |
| `tests/process/test_managed_async_process_context_manager.py` | 6 | 0 |
| `tests/process/test_termination_status_harvest.py` | 4 | 0 |
| `tests/process/test_spawn_options_stdin_default.py` | 4 | 0 |
| `tests/process/monitor/test_monitor_consolidation.py` | 4 | 0 |
| `tests/process/test_termination_outcomes.py` | 2 | 0 |
| `tests/process/test_termination_diagnostics_and_psi.py` | 2 | 0 |
| `tests/process/test_teardown_ownership_guard.py` | 2 | 0 |
| `tests/process/test_spawn_options_pass_fds.py` | 2 | 0 |
| `tests/process/test_spawn_env_containment.py` | 2 | 0 |
| `tests/process/monitor/test_opencode_registry_discovery.py` | 2 | 0 |
| `tests/process/test_spawn_null_byte_rejection.py` | 1 | 0 |
| `tests/process/test_spawn_env_pwd_alignment.py` | 1 | 0 |

The run-loop conversion introduced the public `ralph.pipeline.retry_diagnostic`
module (`RetryDiagnosticHooks`, `wait_for_integration_retry()`,
`run_owned_integration_conflict_resolution()`) and the public
`ralph.pipeline.run_loop.LoopContext` carrier with a `hooks` injection field.
The process conversion introduced the public `is_terminal_status()`,
`install_process_manager()`, `shutdown_process_manager()`, the
`LivenessVerifier` protocol and the `ProcessManager.liveness_verifier=` /
`terminate_executor_factory=` constructor seams.

### Named KEEP records (retained white-box sites with a recorded gap)

- `tests/process/test_spawn_env_sanitizer.py` — 4 private CLI helper patches
  (`_handle_early_exit_flags`, `_validate_mode_flags`, `_resolve_policy_mode`,
  `_get_cli_context`). *Gap:* the sanitise-and-log step runs inside
  `ralph.cli.main.main()` and is unreachable before those four private helpers
  run. *Seam sketch:* extract it as a public
  `ralph.cli.main.sanitize_inherited_environment() -> tuple[str, ...]` and assert
  on its return value. *Follow-up owner:* the `ralph/cli/**` owner.
- `tests/process/monitor/test_dispatch_all_transports.py` — 2 private imports of
  `ralph.agents.invoke._monitor_factory._discovery_strategy_for_config`.
  *Gap:* the helper lives in a private module with no public re-export.
  *Seam sketch:* rename the module to `ralph/agents/invoke/monitor_factory.py`
  and the symbol to `discovery_strategy_for_config`, then re-export it from
  `ralph.agents.invoke`. *Follow-up owner:* the `ralph/agents/**` owner.
- `tests/pipeline/test_run_loop_resume_prefers_available_agent.py`,
  `tests/test_run_loop_pro_integration.py`, `tests/test_pipeline_runner.py`,
  `tests/test_run_loop_configured_cap.py`,
  `tests/test_run_loop_failed_ladder_terminal.py` — the remaining run-loop and
  pipeline collaborators (`_run_inner_loop`, `_reselect_preferred_agent`,
  `_resume_after_cooldown_wait`, `_block_unresolved_integration`,
  `_run_integration_conflict_resolution`, `_complete_in_progress_merge`,
  `_paused_rebase_at`, `_resolve_orphaned_unmerged_index`,
  `_resolve_paused_rebase`, `_exhaustion_still_binds`,
  `_announce_blocked_dispatch`). *Gap:* each is a private module function with
  no public entry point; the retry-diagnostic cluster now shows the pattern
  (a public module plus a `LoopContext.hooks` injection carrier). *Seam
  sketch:* publish the remaining collaborators as public entry points on
  `ralph.pipeline.run_loop` and extend the hooks carrier. *Follow-up owner:*
  the run-loop/pipeline public-seam unit.

### Merge records (duplication removed)

- **Analysis-iteration counter family (11 modules -> 1).** The eleven
  `tests/test_analysis_iteration_new_*.py` modules (14 test functions, 26
  assertions, 1761 lines) were merged into the single parametrized module
  `tests/test_analysis_iteration_counters.py` (407 lines) with exactly 14
  explicitly-IDed parameter cases, one per original test function. Every
  original assertion survives and is expressed through public seams
  (`PipelineState.phase`, `get_loop_iteration()`, `get_outer_progress()`,
  `policy.loop_counters[...].default_max`); the merged policies were proven
  byte-identical to the originals with `model_dump()` and the 26 original
  assertion expressions were replayed against the consolidated table.
- **Artifact-tool helper stack (8 modules -> 1 implementation + 8 shims).** The
  duplicated `_tool_artifact_1_helper_*` / `_tool_artifact_2_helper_*` modules
  now hold one shared implementation in `tests/_tool_artifact_helpers.py`; each
  legacy module is a re-export-only shim (10-11 lines, no `class`/`def`), so
  every consumer import path keeps working unchanged. The `_2` stack was the
  base because it is a strict superset of the `_1` stack.

<!-- whitebox-ledger:end -->

