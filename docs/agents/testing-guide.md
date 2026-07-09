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
| Unit | `tests/` root, `tests/unit/` | No | `make test-unit` |
| Integration | `tests/integration/` | No | `make test-integration` |
| Full suite | all tests | Mixed | `make test` |
| Verification | lint + typecheck + `make test` (60 s combined budget) | Mixed | `make verify` |

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