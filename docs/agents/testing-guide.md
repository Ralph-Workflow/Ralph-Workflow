# Testing Guide (Canonical)

**Single source of truth for all test strategy, rules, and patterns.**
Read before writing or modifying any test.

---

## ⚡ Test Performance Policy — MANDATORY FOR ALL AGENTS

> **This policy is non-negotiable. AI agents must comply before marking any test task complete.**

### The Rule

**YOU MUST ensure every test finishes within 10 seconds.** If a test exceeds this limit, stop and fix it — do not defer, do not skip, do not mark work done.

### What "fix it" means

1. **Refactor code to be testable.** If a test is slow because production code does real I/O, sleeps, or calls real processes, that is a design defect. Extract the I/O into an injectable boundary (`Workspace`, `ProcessExecutor`) and test the pure logic with fakes.
2. **Replace blocking calls with fakes.** Use `MemoryWorkspace` instead of real filesystem, `MockProcessExecutor` instead of real processes. Never let real I/O or real network into unit or integration tests.
3. **Eliminate real `sleep` / wall-clock waits.** Inject a clock abstraction and advance it in tests — never call `std::thread::sleep`, `tokio::time::sleep`, or `time.sleep` inside test code.
4. **Never use `#[ignore]` to hide a slow test.** A timeout problem is a design problem. Fix the design.

### Tests must test behavior, not implementation

| ✅ Test the behavior (observable contract) | ❌ Do not test the implementation (internals) |
|---|---|
| Phase transitions, effect variants emitted | Private field mutations, internal buffer sizes |
| Checkpoint round-trips through the public seam | Which internal function was called |
| Observable output through `TestPrinter` / `VirtualTerminal` | Cache hit counters, task handle lifetimes |
| Drain-addressed runtime effects | Which branch of a `match` was taken |

If changing the implementation (without changing behavior) would break a test, **the test is wrong — rewrite it.**

### Agent checklist (complete before marking any test work done)

- [ ] All tests in the affected tier pass in < 10 s total wall-clock
- [ ] No test calls `sleep` or polls real wall-clock time
- [ ] No test reaches through a boundary into real I/O (filesystem, process, network)
- [ ] Every test asserts on observable behavior, not internal state
- [ ] Any refactor needed to make code testable within time is done — not deferred

---

## Test Pyramid

```
         ▲ system_tests (tests/system_tests/)
         │ Real git, real filesystem, libgit2 — serial (#[serial] required)
         │ NOT in CI — run manually only
         │
        ██ process-system-tests (tests/process_system_tests/)
        │  Real OS processes / PATH — NO libgit2 — parallel
        │  NOT in CI — run manually only
        │
       ████ Integration tests (cargo test -p ralph-workflow-tests --test integration_tests)
       │    MemoryWorkspace + MockProcessExecutor, no real I/O — parallel
       │    Target: < 60 s wall-clock
       │
   ████████ Unit tests (cargo test -p ralph-workflow --lib)
            Pure reducers, domain logic, parsers — parallel
            Target: subsecond per test, total < 10 s
```

### Tier summary

| Tier | Binary | Parallelism | CI? | Run command |
|------|--------|-------------|-----|-------------|
| Unit | `ralph-workflow --lib` | parallel | yes | `cargo test -p ralph-workflow --lib --all-features` |
| Integration | `integration_tests` | parallel | yes | `cargo test -p ralph-workflow-tests --test integration_tests` |
| Process system | `process-system-tests` | parallel | **no** | `cargo test -p ralph-workflow-tests --test process-system-tests` |
| git2 system | `system_tests` | serial (libgit2) | **no** | `cargo test -p ralph-workflow-tests --test system_tests` |

---

## Parallelism Rules

**All tests are parallel by default.**
The only justified exception is the `system_tests` binary, where `#[serial]` is required on every test because concurrent `git2::Repository` drops from multiple threads trigger thread-unsafe libgit2 global shutdown (SIGABRT).

| Location | `#[serial]` | Reason |
|----------|-------------|--------|
| `ralph-workflow/src/` unit tests | **BANNED** | Use env-injection |
| `tests/integration_tests/` | **BANNED** | Use MemoryWorkspace / MockProcessExecutor |
| `tests/process_system_tests/` | **BANNED** | Use module-local Mutex for PATH mutation |
| `tests/system_tests/` | **REQUIRED** | libgit2 global reference counter (SIGABRT) |

> **Note on `signal_cleanup`:** The `signal_cleanup` module resides in `system_tests/` (not `process_system_tests/`) because its tests call `init_git_repo()` to set up a real git repository for SIGINT/cleanup verification. This is a direct libgit2 dependency, so `#[serial]` is required and the serial binary is the correct location.

If a test seems to require `#[serial]`, refactor the implementation first (dependency injection, env-injection pattern) before escalating to a system test.

---

## Deterministic Tests

Every test must produce the same result on every run. Never let the following sources of
non-determinism leak into tests:

| Source | Isolation technique |
|--------|-------------------|
| Real time / wall clock | Injectable clock or frozen timestamp |
| Randomness / RNG | Seed injection or deterministic inputs |
| Network | Never in unit/integration tests; `MemoryWorkspace` / `MockProcessExecutor` instead |
| Process-global env vars | Env-injection pattern (see below) |
| Process-global singletons | Dependency injection; reset state in fixtures |
| Filesystem | `MemoryWorkspace` (integration), `TempDir` (system tests only) |

If a test is non-deterministic, treat it as a design defect: find the non-deterministic
source and inject it rather than tolerating flakiness. See also: **Flaky Test Policy** below.

---

## Env-Injection Pattern

The most common `#[serial]` cause: production code calling `std::env::var` directly.

**BEFORE — requires `#[serial]`:**
```rust
#[test]
#[serial]
fn test_cloud_disabled_by_default() {
    std::env::remove_var("RALPH_CLOUD_MODE");
    let cfg = CloudConfig::from_env();
    assert!(!cfg.enabled);
}
```

**AFTER — parallel-safe, no `#[serial]`:**
```rust
#[test]
fn test_cloud_disabled_by_default() {
    let cfg = CloudConfig::from_env_fn(|_| None);
    assert!(!cfg.enabled);
}

#[test]
fn test_cloud_enabled_with_full_env() {
    let env = [("RALPH_CLOUD_MODE", "true"), ("RALPH_CLOUD_API_URL", "https://x"), ...];
    let cfg = CloudConfig::from_env_fn(|k| env.iter().find(|(key, _)| *key == k).map(|(_, v)| (*v).to_string()));
    assert!(cfg.enabled);
}
```

**Production pattern** — always provide both forms:
```rust
pub fn from_env_fn(get: impl Fn(&str) -> Option<String>) -> Self { /* impl */ }
pub fn from_env() -> Self { Self::from_env_fn(|k| std::env::var(k).ok()) }
```

For unit tests (`src/`), use `MemoryConfigEnvironment::with_env_var()` — no `#[serial]` needed.

---

## Test Doubles

Use the right double. Never mock domain logic — only mock at architectural boundaries.

| Double | When | Codebase Example |
|--------|------|-----------------|
| **Fake** | Working implementation, in-memory | `MemoryWorkspace` |
| **Stub** | Returns canned values | `MockProcessExecutor` with preconfigured results |
| **Spy** | Records calls for assertion | `TestPrinter` capturing output |
| **Mock** | Pre-programmed expectations | `MockAppEffectHandler` |
| **Dummy** | Placeholder, never used | Empty `MemoryWorkspace` for pure reducer tests |

**Required doubles:**

| Operation | Use | Never use |
|-----------|-----|-----------|
| File I/O | `MemoryWorkspace` | `TempDir`, `std::fs::*` |
| Process execution | `MockProcessExecutor` | `std::process::Command` |
| Parser output | `TestPrinter` / `VirtualTerminal` | Direct string inspection |

---

## Architecture Boundaries

Ralph uses two effect layers:

| Layer | Mock With | Tests |
|-------|-----------|-------|
| `AppEffect` (CLI setup) | `MockAppEffectHandler` | integration tests |
| `Effect` (pipeline) | `MemoryWorkspace` + `MockProcessExecutor` | integration tests |
| Real OS / libgit2 | none (real implementations) | system tests only |

### Capability Gate Testing (RFC-009 Phase 2)

The capability gate is tested at two levels:

#### Unit Tests (`reducer/boundary/tests/capability_gate_enforcement.rs`)

Test `MainEffectHandler::execute()` directly with specific drain sessions:

```rust
#[test]
fn planning_denies_workspace_write() {
    let session = AgentSession::for_drain(run_id.clone(), SessionDrain::Planning, 0);
    let ctx = create_context_with_session(&workspace, session);
    let mut handler = MainEffectHandler::new(state);

    // Planning trying to execute development effect
    let result = handler.execute(Effect::InvokeDevelopmentAgent { iteration: 0 }, &mut ctx);

    assert!(matches!(
        result.event,
        PipelineEvent::Agent(AgentEvent::CapabilityDenied { capability, .. })
    ));
    assert!(capability.contains("WorkspaceWriteTracked"));
}
```

#### Integration Tests (`tests/integration_tests/brokered_sessions/`)

Test the full pipeline with `MockEffectHandler` using `session_override`:

```rust
#[test]
fn brokered_session_enforces_read_only() {
    let workspace = MemoryWorkspace::new_test();
    let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0);

    let mut handler = MockEffectHandler::new()
        .with_session_override(session);

    // Planning session trying to invoke development agent
    let ctx = create_context(&workspace);
    let result = handler.execute(Effect::InvokeDevelopmentAgent { iteration: 0 }, &mut ctx.clone());

    // Should be denied at capability gate level
    assert!(matches!(result.event, PipelineEvent::Agent(AgentEvent::CapabilityDenied { .. })));
}
```

#### Key Testing Patterns

| Scenario | Test Location | Pattern |
|----------|---------------|---------|
| Drain denies wrong effect type | `capability_gate_enforcement.rs` | `MainEffectHandler` + direct session |
| Behavioral equivalence (dev works) | `capability_gate_enforcement.rs` | `MainEffectHandler` + `Development` session |
| Full pipeline with session override | `brokered_sessions/` | `MockEffectHandler::with_session_override()` |
| Audit trail accumulation | `brokered_sessions/audit_trail_tests.rs` | Inspect `ctx.audit_trail` |
| Session handshake persistence | `brokered_sessions/session_handshake_tests.rs` | Check `.agent/audit/*.jsonl` |

---

## Contract and Boundary Testing

Validate the typed contracts that cross architectural seams explicitly. Ralph has three
primary boundary types:

### 1. Serialization contracts

Checkpoint JSON format is a persistence contract. Test it with round-trip assertions:

```rust
// ✅ CORRECT — round-trip: serialize → deserialize → serialize produces identical output
let original = PipelineState::initial(5, 2);
let json = serde_json::to_string(&original).expect("serialize");
let restored: PipelineState = serde_json::from_str(&json).expect("deserialize");
assert_eq!(original, restored);

// Any field rename or type change must break this test — that is the point.
```

Serialization tests live in the same module as the type. Never skip them when renaming
or restructuring persisted fields.

### 2. Effect/event contracts

`Effect` and `AppEffect` enum variants are the typed API between reducers and handlers.
Test that the reducer emits the **correct variant with the correct payload** — not that
a specific internal function was called:

```rust
// ✅ CORRECT — validate the effect that crosses the boundary
let (new_state, effects) = reduce(state, event);
assert!(effects.iter().any(|e| matches!(e, Effect::WriteCheckpoint { .. })));

// ❌ WRONG — asserting on an internal call, not the boundary
mock.assert_called_once_with("write_checkpoint_internal");
```

### 3. Persistence boundary

Integration-test checkpoint read/write using `MemoryWorkspace`. After a checkpoint is
written and the pipeline resumes, observable state must match what was saved:

```rust
// ✅ CORRECT — test through the public persistence seam
let ws = MemoryWorkspace::new();
save_checkpoint(&ws, &state).expect("save");
let restored = load_checkpoint(&ws).expect("load");
assert_eq!(state.phase, restored.phase);
assert_eq!(state.iteration_count, restored.iteration_count);
```

---

## AAA Structure

Every test: one behavior, Arrange-Act-Assert.

```rust
// Arrange
let state = PipelineState::initial(5, 2);
let event = PipelineEvent::developer_exhausted();

// Act
let new_state = reduce(state, event);

// Assert
assert_eq!(new_state.phase, PipelinePhase::Review);
```

Keep Arrange short. If setup exceeds 10 lines, extract a named builder helper.

---

## Test Naming

Name tests by **observable behavior**, not implementation:

| ✅ Good | ❌ Avoid |
|---------|---------|
| `test_agent_fallback_after_retry_exhaustion` | `test_internal_counter_updates` |
| `test_pipeline_transitions_to_failure` | `test_buffer_management` |
| `test_parser_streams_deltas_to_terminal` | `test_cache_size_tracking` |

---

## Length Assertions

Length assertions are acceptable **only when combined with content checks**:

```rust
// ✅ CORRECT — count + content
let logs = logger.get_logs();
assert_eq!(logs.len(), 2, "should buffer two writes");
assert!(logs[0].contains("Partial line"));
assert!(logs[1].contains("Another line"));

// ❌ WRONG — count without content
assert_eq!(logger.get_logs().len(), 2);
```

If content checks already verify correctness (e.g., indexing), the length check is redundant — remove it.

---

## Common Anti-Patterns

| Anti-pattern | Fix |
|--------------|-----|
| `#[serial]` in integration/unit tests | Use env-injection or MemoryWorkspace |
| `std::env::set_var` / `remove_var` in tests | Use env-injection pattern |
| `cfg!(test)` in production code | Use dependency injection |
| `TempDir` / `std::fs::*` in integration tests | Use `MemoryWorkspace` |
| `std::process::Command` in integration tests | Use `MockProcessExecutor` |
| Testing private fields / internal state | Test through public APIs |
| Asserting `.len()` without content | Add content assertions |
| Test updates when implementation changes | Only update tests when behavior changes |

---

## Observable vs. Internal State

Ralph's public state fields (`PipelineState`, `AgentChainState`) are part of the observable contract — they are persisted in checkpoints and drive behavior. Testing them is testing observable behavior.

**Observable (test freely):** public fields, fields in checkpoint JSON, counters that enforce behavioral bounds, phase transitions.
**Internal (do not test):** private fields, transient state not in checkpoints, buffer/cache sizes, implementation helpers.

---

## Flaky Test Policy

A flaky test fails non-deterministically. Flaky tests must not remain in gating paths.

### Protocol

1. **Fix** the root cause (inject clock/random seed, use TempDir isolation, apply env-injection).
2. **Quarantine** if the fix is non-trivial — open a GitHub issue first, then annotate:

```rust
#[test]
#[ignore = "flaky: https://github.com/org/repo/issues/N — timing-sensitive signal delivery"]
fn test_something_timing_sensitive() { ... }
```

3. **Resolve** the quarantine issue within one sprint.

**Rules:**
- Every `#[ignore]` attribute must include a `https://` URL (enforced by `cargo xtask verify`).
- A `#[ignore]` without a URL will fail the audit.
- Do not let quarantined tests accumulate.

**Common root causes:** `std::env::var` races → env-injection; real filesystem → `MemoryWorkspace`; real time → injectable clock; process-global singletons → dependency injection.

---

## Compliance Verification

Run `cargo xtask verify` before every PR.

See `docs/agents/verification.md` for the complete pre-PR command list.

If a test change, test run, or verification step exposes a pre-existing failure, flake, warning, stale fixture, or other existing repo issue, that issue becomes your top priority immediately. Fix it now rather than documenting or deferring it.

**Key audit checks:**
- No `cfg!(test)` in integration tests
- No `TempDir` / `std::fs::*` in integration tests
- No `std::process::Command` in integration tests
- No `#[serial]` in integration tests or `src/` unit tests
- No `std::env::set_var` / `remove_var` in integration tests
- No `#[serial]` in `process_system_tests/`
- No `git2::` imports in `process_system_tests/`
- No `#[ignore]` without a `https://` issue URL

---

## TDD Discipline

**Write the failing test first.** No production code without a red test.

1. **Red** — write a test that describes the new behavior and watch it fail.
2. **Green** — implement the minimal production change to make the test pass.
3. **Refactor** — clean up duplication and structure, keeping tests green.

```rust
// Step 1 (Red): write test first — it will not compile / will fail
#[test]
fn test_pipeline_rejects_empty_agent_chain() {
    let state = PipelineState::initial_with_agents(vec![]);
    let result = reduce(state, PipelineEvent::start());
    assert_eq!(result.phase, PipelinePhase::Failure);
}

// Step 2 (Green): add the minimal production logic
// Step 3 (Refactor): simplify / extract helpers
```

If a behavior is hard to test first, that is a design signal: simplify coupling, add a seam, or separate I/O from decision logic.

---

## Architecture Driven by Tests

Tests are first-class architecture components. When a test is hard to write, simplify the production design — do not add brittle workarounds.

### Ralph's testable architecture

| Layer | What lives here | How to test |
|-------|----------------|-------------|
| Pure reducers (`reducer/`) | All decision logic, phase transitions, counters | Unit-test with value-type inputs and outputs — no mocks needed |
| Effect handlers (`app/`, `effects/`) | I/O, process spawning, filesystem writes | Integration-test with `MemoryWorkspace` + `MockProcessExecutor` |
| OS / libgit2 | Real git operations | System-test only (serial binary) |

When testing agent orchestration, seed the correct runtime `drain` in state fixtures. A populated agent chain alone is no longer enough to represent "already initialized" work, because planning, development, review, fix, commit, and analysis are distinct runtime consumers even when they share the same underlying chain definition.

When testing config behavior, assert on resolved drain bindings or drain-addressed runtime effects rather than legacy role-shaped fallback tables. Legacy `[agent_chain]` tests are still useful, but only to prove they normalize into the same resolved built-in drains as the named schema.

For checkpoint compatibility around agent execution state, include degraded or conflicting metadata cases in addition to round-trips: missing `current_role`, missing `current_drain`, and stale `current_role` that disagrees with the persisted drain should all restore deterministically with the drain as the authoritative runtime identity.

### Principles

- **Separate decision from I/O.** Keep reducers pure; keep side effects in handlers.
- **Depend on interfaces, not implementations.** Use `Workspace`, `ProcessExecutor`, and `AppEffectHandler` traits so collaborators can be replaced in tests.
- **Keep boundaries explicit.** Each seam (`AppEffect` before repo root is known, `Effect` after) can be validated independently.
- **Treat untestable code as a design defect.** If you must add a `cfg!(test)` branch, a skip flag, or a test-only boolean parameter, refactor the production code instead.
- **Use test failures as design feedback.** A test that requires elaborate setup usually means the unit under test has too many responsibilities.
- **Prefer stable seams over framework internals.** Test through the typed interfaces (`Workspace`, `ProcessExecutor`, `AppEffectHandler`) that are stable across refactors. Never test through async runtime internals, serializer internals, or framework routing internals — those couple tests to implementation, not behavior.

```rust
// ✅ Seam test — stable across internal refactors:
let ws = MemoryWorkspace::new();
let result = run_pipeline_phase(&ws, &config);
assert_eq!(ws.files_written(), &["checkpoint.json"]);

// ❌ Framework-coupled test — breaks on internal refactors:
// (Do not assert on tokio task structure — it is not the contract)
let handle = tokio::spawn(...);
handle.await.unwrap();
assert!(GLOBAL_SIDE_EFFECT_HAPPENED.load(Ordering::SeqCst));
```

---

## Definition of Done for Testing

A change is complete only when **all** of the following hold:

- [ ] New behavior is covered by a test that was **red before** the production change.
- [ ] Existing behavior regressions are prevented by focused, targeted tests.
- [ ] All tests pass in **parallel mode** (no new `#[serial]` outside `system_tests`).
- [ ] No new flaky tests introduced; quarantined tests include issue URLs.
- [ ] Test names describe observable behavior, not implementation details.
- [ ] AAA structure is clear; setup does not exceed ~10 lines without a named helper.
- [ ] Required refactors for testability are done; no `cfg!(test)`, skip flags, or test-mode booleans remain.
- [ ] `cargo xtask verify` completes with no ERROR/WARNING diagnostics.

---

## Documentation Quality

- **Single source of truth:** all test strategy lives in `docs/agents/testing-guide.md`. Other files (`tests/INTEGRATION_TESTS.md`, `docs/agents/integration-tests.md`) are redirect stubs.
- **Update docs in the same commit** as the behavior or architecture change.
- **Keep examples runnable.** Remove stale patterns immediately when the production API changes.
- **Prefer concise, decision-oriented wording.** Contributors should be able to find the rule they need in under a minute.
- **Every `#[ignore]` requires an issue URL** (enforced by `cargo xtask verify`).
