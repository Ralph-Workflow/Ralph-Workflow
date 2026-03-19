# Learnings

## 2026-03-19 — Plan Creation

### Core principle: lints are diagnostic signals, not the metric
The user explicitly clarified that dylint is a GUIDELINE, not a rule. Goodhart's Law applies.
Chasing "zero lint errors" would incentivise moving code into boundary modules to silence lints,
or writing superficially FP-looking code that misses the point. The goal is genuine architectural
quality; lints are a compass for finding violations worth investigating.

### Architectural snapshot at plan creation
- `boundary/` module has collapsed into a full workflow engine with 23 nested files across
  commit/, development/, planning/, review/, io/ sub-trees — none of which should be inside
  a boundary module
- Agent adapters (claude/, streaming_state/, codex/, opencode/) have similar nesting problems
- Domain code imports directly from boundary modules in 160+ places (should be capability injection)
- Mutable bindings, imperative loops, interior mutability scattered through domain code
- ~85 .unwrap() in git_helpers/config_state.rs alone
- No property-based testing, no coverage instrumentation

### Key style guide files to read before any task
- `docs/code-style/boundaries.md` — authoritative on what boundaries ARE and ARE NOT
- `docs/code-style/functional-transformations.md` — cookbook for FP transformations
- `docs/code-style/architecture.md` — State→Orchestrator→Effect→Handler→Event→Reducer→State
- `docs/code-style/errors-and-diagnostics.md` — errors as values, diagnostics as data
- `docs/tooling/dylint.md` — FP principles behind each lint; lint is a hint, not the spec

### The standard boundary shape (memorise this)
1. IMPURE — gather inputs from capabilities
2. PURE — call domain helpers on plain values
3. IMPURE — perform the requested edge interaction, return typed result

### The three FP monad analogs
- Reader: accept capabilities as parameters (never import from io/, runtime/, executor/)
- Writer: return diagnostics as `WithDiagnostics<T>`, emit only at boundary
- Except: `Result<T, E>` with typed error enums, never .unwrap()/.expect()/panic! in domain

### Retry belongs in the state machine, not boundary loops
State→Orchestrator decides retry → Effect schedules → Handler executes ONE attempt →
Event reports outcome → Reducer updates retry count in state → Orchestrator decides again

## 2026-03-19 — Baseline and behavioral equivalence

### Baseline commit: ceb66980
Last commit before the FP refactoring project began. Used ONLY in F5 (Final Verification Wave)
for integration test behavioral equivalence — not during individual phases.

The baseline check compares which integration tests passed on ceb66980 vs HEAD. For every
discrepancy, triage as: regression (fix it), baseline-was-testing-a-bug (update test + document),
or implementation-detail-test (replace with equivalent coverage through new seam).

### Commits since baseline (context for what has already been done)
Many refactors have already happened since ceb66980 — see `git log ceb66980..HEAD --oneline`.
Some modules may already be partially refactored. Always read the current state of a file
before making changes.

### Integration tests are the behavioral specification
The integration test suite (`cargo test -p ralph-workflow-tests --test integration_tests`)
is the authoritative source of truth for what the system is supposed to DO. If a refactor
breaks an integration test, it is either a regression or evidence that the old test was
wrong. Both require investigation — neither justifies deleting the test.
