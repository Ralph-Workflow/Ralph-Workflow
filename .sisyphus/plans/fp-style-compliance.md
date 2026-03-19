# FP Style Compliance

## Overview

This plan systematically brings `ralph-workflow` into genuine compliance with the project's code
style guide and its functional programming principles. The goal is **real architectural quality**:
pure domain code, thin boundaries, explicit effects, typed failures, testable design. That goal
is the target. Dylint is not.

**On the role of dylint:** The lints are a diagnostic tool — a signal that a particular pattern
is likely violating a principle. They are not a compliance checklist. A codebase that silences
every lint by superficially shuffling code into boundary modules, or by writing technically
FP-shaped code that misses the point, has made Goodhart's mistake: when the measure becomes the
target, it ceases to be a good measure. Run dylint to *find* things to investigate. Fix the
underlying architectural problem. Do not chase a zero-error count.

Every task in this plan describes the architectural transformation that needs to happen. If dylint
fires on the result of a correct architectural decision, that is a lint false-positive — document
it and move on. If dylint is silent on code that is still architecturally wrong, that is worse —
fix the code anyway.

**Critical constraint:** Do NOT modify `lints/ralph_lints/`. Dylint lints are being developed in
parallel by other contributors. New lints may surface new problem areas to investigate — treat
those as additional diagnostic signals, not new tasks to mechanically clear.

**Companion reading (required before any task):**
- `docs/code-style/boundaries.md` — the normative boundary guide
- `docs/code-style/functional-transformations.md` — practical FP cookbook
- `docs/code-style/architecture.md` — reducer-driven architecture
- `docs/code-style/errors-and-diagnostics.md` — errors as values
- `docs/tooling/dylint.md` — lint policy and FP principles behind each lint
- `docs/agents/testing-guide.md` — test pyramid and TDD discipline

---

## Current Compliance Gaps — Where The Project Falls Short

Dylint is used here as a diagnostic lens to locate architectural problems — the violation
counts below describe *where things are wrong*, not a score to optimise. The actual problem
in each case is described in plain language. Fix the architectural problem; the lint output
will naturally improve as a consequence.

### Gap 1 — Boundary Architecture Has Collapsed (the Major Problem)

The `boundary/` module has grown into a deep workflow engine. Boundary modules are supposed
to be thin, flat wiring layers (gather input → call pure helper → perform effect). Instead,
`boundary/` now owns entire workflow sub-systems:

```
boundary/                          ← should be thin leaf adapters
  commit/          (7 nested files) ← contains commit workflow logic
  development/     (6 nested files) ← contains development workflow logic
  planning/        (6 nested files) ← contains planning workflow logic
  review/          (9 nested files) ← contains review/fix workflow logic
  io/              (2 nested files)
```

Agent-adapter boundaries also grew deep structure where they should stay flat:
```
claude/delta_handling/             (5 files) — pure delta parsing logic inside effect seam
streaming_state/session/           (11 files) — session state management inside boundary
codex/event_handlers/              (6 files) — event dispatch logic inside boundary
opencode/formatting/               (3 files) — formatting logic inside boundary
printer/virtual_terminal/          (1 file)
runtime/streaming/                 (1 file)
```

The root cause: workflow orchestration logic (prompt preparation, output parsing, validation,
XML handling, retry materialisation) was placed inside boundary modules instead of domain
modules. Boundaries became a second policy engine — exactly what `docs/code-style/boundaries.md`
forbids. This is not a "lint violation" — it is an architectural collapse that makes
everything harder to test, reason about, and change.

### Gap 2 — Domain Code Reaches Into Effect Seams Instead of Receiving Capabilities

Domain (non-boundary) modules directly `use` boundary implementations instead of accepting
capability traits as parameters. This is the opposite of the Reader pattern: the function
grabs its dependencies from the ambient module tree instead of having them injected by the
caller. The consequence: domain functions become untestable without setting up real I/O,
environment variables, or process spawners.

The most common offenders by boundary imported:
`io/` (most prevalent), `runtime/`, `executor/`, `boundary/` itself, `claude/codex/gemini`.

### Gap 3 — Mutable Bindings Pervasive in Domain Code

`let mut` appears throughout domain code where iterator pipelines, struct-update syntax,
shadowing, or `with_*` builder chains should be used. The symptom is code that reads as
a sequence of state mutations rather than a sequence of value transformations. The problem
is not the keyword — it is that the code is harder to reason about, harder to test, and
signals that the function may be doing too much at once.

### Gap 4 — Imperative Loops in Domain Code

`for`, `loop`, and `while` appear in domain code where they should not. The deeper issue
is not the syntax: it is what these loops are doing. Retry loops belong in the state
machine. Accumulation loops belong as `fold`/`collect` pipelines. Polling loops belong in
`runtime/` boundaries. When a loop appears in domain code, it is a signal to ask: what is
this loop actually FOR, and does this code belong here at all?

### Gap 5 — Interior Mutability in Domain Code

`Mutex`, `LazyLock`, and `Cell` appear in domain modules. The deeper issue: shared mutable
state in domain code means multiple call sites can see different values of the "same" thing,
which destroys referential transparency and makes behaviour hard to reason about or test.
In Haskell terms, `IORef`/`MVar` exist only in `IO` — in pure code, every value is immutable.
Here, that means: if domain code needs shared mutable state, it is almost certainly modelling
state that belongs in the reducer/orchestrator cycle instead.

### Gap 6 — No Writer Monad: Diagnostics Leaked as Side Effects

Domain functions call `println!`/`eprintln!` or rely on logging side effects instead of
returning diagnostics as data (`WithDiagnostics<T>` / `Logged<T>`). The boundary should be
the only place that emits user-facing output.

### Gap 7 — Except Monad Incomplete: Unwrap/Panic in Domain Code

`git_helpers/config_state.rs` alone has ~85 `.unwrap()` calls. Domain code uses
`.unwrap()`, `.expect()`, and `panic!` where `Result<T, E>` with typed error enums should
propagate failures explicitly.

### Gap 8 — No Property-Based Testing

The test suite uses unit and integration tests only. Pure parsers, reducers, and state
machines would benefit from property-based tests (`proptest`) to verify invariants over
arbitrary inputs.

### Gap 9 — No Code Coverage Instrumentation

No `tarpaulin`, `llvm-cov`, or `codecov` configuration exists. Coverage gaps are invisible.

---

## Architectural Principles Being Applied

These are the principles the code should embody. The lints are imperfect proxies for
violations of these principles — use them as hints, not as definitions of success.
A codebase that violates every principle but passes every lint has done something worse
than starting from scratch.

The three core patterns map to Haskell monad analogs:

### Reader Monad → Capability Injection

Dependencies must be passed IN, not looked up. Domain functions never call `std::env::var`,
`std::fs::*`, or agent boundary modules directly. Instead they accept trait objects:

```
WRONG:  fn load_config() -> Config { std::fs::read_to_string("file").unwrap() }
RIGHT:  fn load_config(ws: &dyn Workspace) -> Result<Config, LoadConfigError>
```

Existing traits: `Workspace`, `ProcessExecutor`, `AppEffectHandler`. Where domain code
imports from `io/`, `runtime/`, `executor/`, `boundary/` — it needs the Reader pattern.

### Writer Monad → Diagnostics as Data

Pure functions return diagnostics as values. Boundaries emit them.

```
WRONG:  fn normalize(x: Raw) -> Out { println!("warn"); Out::default() }
RIGHT:  fn normalize(x: Raw) -> WithDiagnostics<Out>
        // boundary calls normalize(), iterates .diagnostics, emits each
```

### Except Monad → Typed Result Propagation

Failures are values, not panics. Every recoverable failure travels through `Result<T, E>`
with a domain-specific error enum. `.unwrap()` / `.expect()` / `panic!` are forbidden in
domain code.

### The Standard Boundary Shape (IMPURE → PURE → IMPURE)

Every boundary function must follow:
1. **IMPURE** — gather inputs from capabilities (read file, run process, read env)
2. **PURE** — call domain parsers, validators, planners, or reducers on plain values
3. **IMPURE** — perform the requested edge interaction, emit diagnostics, return typed result

Boundary code must NOT contain: retry policy, workflow decisions, business branching,
invariant enforcement, state-machine logic, hidden dependency lookups, or capability-native
types flowing inward.

### Reducer-Driven Architecture

The retry problem (many `loop` violations): retry policy belongs in the reducer/orchestrator
state machine, not in inline loops inside boundary or domain functions.

```
State → Orchestrator (decides next effect) → Handler (executes ONE attempt)
→ Event (reports what happened) → Reducer (updates state, decides if retry needed) → State
```

---

## TODOs

### Phase 1 — Fix Compiler Errors (Immediate Blockers)

These prevent `cargo check` and `dylint` from running cleanly and block all downstream work.

- [ ] Fix E0255 (×2): `llm_output_extraction` and `result_extraction` defined twice in
  `ralph-workflow/src/files/mod.rs` — both have a `pub mod X;` declaration AND a
  `pub use self::X;` re-export creating a name collision. Remove the redundant `pub use`
  lines (keep `pub mod` declarations only). Verify: `cargo check -p ralph-workflow` passes.

- [ ] Fix E0599 (×5): `no method named push found for &mut EventTraceBuffer` in
  `ralph-workflow/src/app/trace/` — `EventTraceBuffer` must be refactored to an immutable
  type. Add a `fn with_event(self, event: TraceEvent) -> Self` consuming builder, then
  replace all call sites that do `buf.push(event)` with `let buf = buf.with_event(event)`.
  Alternatively provide a `fn append(self, events: impl IntoIterator<Item=TraceEvent>) -> Self`.

- [ ] Fix E0599 (×2): `no method named filter_map found for Vec<DirEntry>` — change
  `vec.filter_map(...)` to `vec.into_iter().filter_map(...).collect()` at the 2 call sites.

- [ ] Fix E0282 (×3): type annotations needed — add explicit type annotations (`::<Type>` or
  `let x: Type`) at each of the 3 inferred sites. Find with: `cargo check -p ralph-workflow
  2>&1 | grep E0282`.

---

### Phase 2 — Flatten Nested Boundary Modules (Architecture)

**This is the most structurally impactful phase.** The rule: boundary module names mark effect
seams — they are leaf categories, not containers for deeper structure. Code inside `boundary/`,
`claude/`, `streaming_state/`, `codex/`, `opencode/` must be flat files, not subdirectory trees.

The fix pattern for every nested structure below:
1. Identify what is **pure logic** (parsing, validation, prompt preparation, XML handling,
   state interpretation) → move to a non-boundary domain module
2. Keep only **thin wiring** (gather capability input → call pure helper → perform effect) in
   the boundary flat file
3. Delete or flatten the nested sub-directory

#### 2A — Flatten `boundary/` workflow sub-trees (23 nested files)

The `boundary/` module contains complete workflow sub-trees for planning, development, review,
and commit. These are NOT boundary concerns — they are orchestration and domain logic that was
incorrectly placed inside an effect seam.

- [ ] **boundary/commit/ (7 files)** — Audit `commit/agent.rs`, `commit/execution.rs`,
  `commit/inputs.rs`, `commit/mod.rs`, `commit/prompts.rs`, `commit/validation.rs`,
  `commit/xml.rs`. Extract pure logic (prompt construction, XML parsing, input validation,
  commit message formatting) into `ralph-workflow/src/phases/commit/` (domain). Keep a
  single flat `boundary/commit.rs` that calls pure helpers and performs the agent invocation
  effect. Verify the boundary file follows IMPURE→PURE→IMPURE shape.

- [ ] **boundary/development/ (6 files)** — Audit `development/core.rs`,
  `development/materialization.rs`, `development/mod.rs`, `development/preparation.rs`,
  `development/preparation/modes.rs`, `development/validation.rs`. Extract domain logic
  into `ralph-workflow/src/phases/development/` (domain). Flatten to a single
  `boundary/development.rs` that wires pure logic to capabilities.

- [ ] **boundary/planning/ (6 files)** — Audit `planning/agent_execution.rs`,
  `planning/input_materialization.rs`, `planning/mod.rs`, `planning/output_processing.rs`,
  `planning/prompt_preparation.rs`, `planning/xml_validation.rs`. Extract pure XML
  validation, prompt preparation, and output parsing to `ralph-workflow/src/phases/planning/`
  (domain). Flatten to a single `boundary/planning.rs` thin adapter.

- [ ] **boundary/review/ (9 files)** — Audit `review/fix_flow.rs`,
  `review/review_flow/agent_invocation.rs`, `review/review_flow/input_materialization.rs`,
  `review/review_flow/io/mod.rs`, `review/review_flow/mod.rs`,
  `review/review_flow/output_rendering.rs`, `review/review_flow/prompt_generation.rs`,
  `review/review_flow/regex_cache.rs`, `review/review_flow/validation.rs`,
  `review/review_flow/xsd_retry_materialization.rs`. Extract prompt generation, regex
  caching, output rendering, validation, and XSD retry logic to
  `ralph-workflow/src/phases/review/` (domain). Flatten boundary to a single
  `boundary/review.rs` and `boundary/fix.rs`.

- [ ] **boundary/io/ (2 files)** — Audit `io/mod.rs`, `io/cloud.rs`. Move any domain
  logic to `ralph-workflow/src/io/` proper or to domain modules. If the files contain
  only effect wiring, rename them to flat files alongside the boundary root.

#### 2B — Flatten `claude/delta_handling/` (5 files)

The delta-handling logic (content block parsing, message finalization, error classification)
is pure parsing that was placed inside the `claude/` effect boundary.

- [ ] Extract pure delta-handling types and parsers into
  `ralph-workflow/src/json_parser/delta_parsing/` (domain module). This includes
  `delta_handling/content_blocks.rs`, `delta_handling/errors.rs`,
  `delta_handling/finalization.rs`, `delta_handling/messages.rs`. Keep a thin flat
  `claude/delta_handling.rs` (single file, not directory) that calls pure parsers and
  performs the streaming I/O interaction. Alternatively, move pure parsing to the existing
  `json_parser/` domain module and delete the boundary sub-directory entirely.

#### 2C — Flatten `streaming_state/session/` (11 files)

The session state management under `streaming_state/` contains delta-handling, state
management, and session lifecycle logic — all of which mix pure and effectful concerns.

- [ ] Audit `session/delta_handling/` (7 files: hashing, render, snapshot, text, thinking,
  tool, plus delta_handling.rs) and `session/session_struct.rs`, `session/state_management.rs`.
  Extract pure session state types, delta-parsing logic, and state transitions to a
  `ralph-workflow/src/streaming/` domain module. Keep only I/O-facing session management
  in a flat `streaming_state/session.rs` adapter file.

#### 2D — Flatten `codex/event_handlers/` (6 files)

- [ ] Audit `event_handlers/context.rs`, `event_handlers/error.rs`,
  `event_handlers/item_dispatch.rs`, `event_handlers/items_completed.rs`,
  `event_handlers/items_started.rs`, `event_handlers/turn.rs`. Extract pure event
  interpretation/dispatch logic to `ralph-workflow/src/agents/codex/` domain module.
  Keep flat `codex/event_handling.rs` boundary adapter.

#### 2E — Flatten `opencode/formatting/` (3 files)

- [ ] Audit `formatting/step.rs`, `formatting/text_and_error.rs`, `formatting/tool.rs`.
  Move pure formatting logic to a `ralph-workflow/src/agents/opencode/formatting.rs`
  domain module. Keep `opencode/` boundary flat.

#### 2F — Flatten `printer/virtual_terminal/` and `runtime/streaming/`

- [ ] Move `printer/virtual_terminal/mod.rs` logic: pure terminal rendering belongs in
  a domain module; only actual terminal I/O belongs in `printer/`.
- [ ] Move `runtime/streaming/streaming_line_reader.rs` logic: pure line-parsing belongs
  in a domain module; only the streaming I/O belongs in `runtime/`.

---

### Phase 3 — Apply Reader Pattern: Capability Injection into Domain Code

Domain modules currently import from effect seams (`io/`, `runtime/`, `executor/`,
`boundary/`, and agent adapters) instead of accepting capabilities as parameters. The goal
of this phase is not "fix N lint errors" — it is to make every domain function independently
testable with plain value inputs and no I/O setup. If you can call a function in a test with
`f(input)` and get a deterministic `output` with no fakes or mocks, the Reader pattern is
applied correctly. If you need to set up a filesystem, spawn a process, or set an environment
variable to test a domain function, the phase is not done.

**The transformation pattern:**
```rust
// BEFORE: domain code reaching into an effect seam
use crate::io::workspace::WorkspaceImpl;
fn process(path: &str) -> Data { WorkspaceImpl::new().read(path).unwrap() }

// AFTER: plain-value input, capability injected by the caller
use crate::workspace::Workspace;
fn process(ws: &dyn Workspace, path: &str) -> Result<Data, ProcessError> {
    ws.read(path).map(parse_data).map_err(ProcessError::Read)
}
// test: f(&MemoryWorkspace::new().with_file("path", "content"), "path")
```

- [ ] **T3-io**: For every non-boundary module that imports from `io/`, replace the import
  with `&dyn Workspace` or a more specific capability trait. The `Workspace` trait already
  exists — prefer it. When the import is for transport/HTTP, create or use an abstract
  transport trait. The success condition: `cargo test` on the module passes using only
  `MemoryWorkspace` — no real filesystem access required.

- [ ] **T3-runtime**: For every non-boundary module importing from `runtime/`, inject the
  capability: clock reads → injectable clock trait; env access → `impl Fn(&str) -> Option<String>`
  (env-injection pattern from the testing guide); process spawning → `&dyn ProcessExecutor`.
  The success condition: env-dependent tests pass without `#[serial]` or `std::env::set_var`.

- [ ] **T3-executor**: For every non-boundary module importing from `executor/`, inject
  `&dyn ProcessExecutor`. Domain code receives typed `CommandOutput` — it does not spawn.
  Success condition: all tests use `MockProcessExecutor`.

- [ ] **T3-boundary-module**: Domain code importing from `boundary/` itself. After Phase 2
  flattening many of these disappear. Any remaining case means logic was split incorrectly —
  the decision belongs in the domain caller, not the boundary.

- [ ] **T3-agent-adapters**: For `claude`, `codex`, `gemini` imports from domain code,
  define an agent-agnostic domain trait (e.g., `AgentInvoker`) and inject it. The boundary
  adapters implement the trait. Domain code never sees the concrete agent type.

---

### Phase 4 — Remove Policy Leakage from Boundary Functions

Even after Phase 2 flattening, each flat boundary file must be audited for policy leakage.
The lints (`boundary_function_too_complex`, `forbid_boundary_policy_calls`,
`forbid_boundary_retry_loops`) will flag likely offenders, but they are starting points for
investigation, not a definition of the problem. The test is architectural: can you describe
the function as "gather X, call pure_helper(X), perform_effect(result)"? If not — regardless
of whether a lint fires — the function contains policy that belongs elsewhere.

**The test for each boundary function:** Can you describe the function purely as
"gather X, call pure_helper(X), perform_effect(result)"? If not, extract the policy.

- [ ] **Retry/loop in boundary**: Audit every boundary function with a `loop` or `while`.
  Retry loops belong in the orchestrator/reducer state machine (see architecture doc).
  For each: replace the inline loop with a single-attempt handler that returns a typed
  event. The reducer then decides if a retry effect should be scheduled.

  ```
  WRONG (in boundary): loop { match exec_attempt() { Ok(r) => return r, Err(_) => attempts+=1 } }
  RIGHT: fn execute_once(...) -> Result<SuccessEvent, AttemptFailedEvent>
         // Reducer: if AttemptFailedEvent && retries_remaining > 0 → schedule retry Effect
  ```

- [ ] **Workflow decisions in boundary**: Audit every boundary function that contains
  `if condition { do_phase_A() } else { do_phase_B() }` style branching based on business
  state. This belongs in the orchestrator. Extract as: boundary returns a fact-shaped event,
  orchestrator decides the next effect.

- [ ] **Invariant enforcement in boundary**: Audit every boundary function that validates
  domain invariants (e.g., "check if path is non-empty before proceeding"). This should be
  done by parsing raw input into a stronger type at the boundary edge, then passing the
  proven type to pure domain code. Apply "parse, don't validate" pattern.

- [ ] **Hidden dependency lookups**: Audit every boundary function for calls to
  `std::env::var`, `std::env::current_dir`, or similar ambient lookups. These make the
  function appear pure when it is not. Extract to an injectable capability.

---

### Phase 5 — Apply Immutability in Domain Code

`let mut` in domain code is a symptom, not the disease. The disease is: the function is
written as a sequence of state mutations rather than a sequence of value transformations.
The goal of this phase is code that reads as a pipeline of transformations — not code that
happens to not use `let mut`. Use the lints as a finder (they point to files worth auditing)
but judge each case on whether the resulting code is genuinely clearer and more correct, not
on whether the lint is silenced.

Reference: `docs/code-style/functional-transformations.md` — especially the quick-reference
table and the "fold-with-mut-accumulator trap" section.

**Priority order for investigation:**

- [ ] **T5-parse-state (~50 vars: `buf`, `reader`, `text`, `content`, `current_text`,
  `inner_buf`, `inner_reader`, `bare_content_xml`, `raw_text_parts`)** — These are
  character-by-character or line-by-line parsing patterns. Two options:
  (a) Move to boundary module if the parsing is fundamentally I/O-driven (streaming reads)
  (b) Replace with pure parser: accept `&str` / `String` input, use iterator pipelines
  (`lines()`, `chars()`, `split()`, `scan()`, `fold()`) to produce the result without mutation

- [ ] **T5-accumulators (~30 vars: `result`, `output`, `summary`, `elements`, `collected`,
  `accumulated`, `parts`, `body`, `cells`, `entries`, `file_list`, `entry_list`,
  `content_fragments`)** — Replace with `map`/`filter`/`flat_map`/`collect` pipelines.
  Use `[a, b, c].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join("\n")`
  for multi-section assembly. Use `fold` for state-accumulating reductions.

- [ ] **T5-flags (~25 vars: `has_entries`, `has_git_config`, `found_root`, `no_issues_found`,
  `next_steps_present`, `files_changed_present`, `excluded_files_seen`, `previous_exists`,
  `in_tag`, `in_content`)** — Replace boolean accumulator flags with `any()`, `all()`,
  `find()`, `find_map()`, or `position()`. Scanning flags from iteration should use
  structured state returned from a pure helper, not a `let mut bool` side effect.

- [ ] **T5-config-builders (~20 vars: `config`, `handler`, `phase_ctx`, `prompt_monitor`,
  `skills_mcp`, `mcps`, `opts`, `diff_opts`, `filter_cb`, `agent_phase_guard`,
  `cleanup_guard`)** — Use consuming builder pattern (`with_*` methods) or struct-update
  syntax. Each `let mut x = X::default(); x.field = y; x.other = z` becomes
  `X::default().with_field(y).with_other(z)` or `X { field: y, other: z, ..X::default() }`.

- [ ] **T5-git (~8 vars: `git_helpers`, `diff_opts`, `index`, `perms`, `files`,
  `files_changed`, `excluded_files`, `reference_files`, `primary_files`)** — Git-specific
  `let mut` patterns in domain code. Many of these indicate git operations in domain modules
  (violation of boundary separation — coordinate with Phase 3/4 refactoring). For pure
  aspects (path manipulation, diff interpretation), use iterator pipelines. For effectful
  aspects, move to `git_helpers/` boundary or `io/` module.

- [ ] **T5-misc (~remaining vars: `depth`, `indent`, `i`, `name`, `kind`, `method`, `email`,
  `caption`, `next_steps`, `next_auto`, `issues`, `items`, `matches`, `columns`, `child`,
  `steps`, `status`, `timer`, `tf`, `nested`, `location`, `mitigation`, `rationale`,
  `expected_outcome`, `out`)** — Fix remaining `let mut` patterns using shadowing,
  `scan()`, `enumerate()`, or value-returning transforms.

---

### Phase 6 — Replace Imperative Loops with Intent-Revealing Combinators

The goal is not "no `for` loops." The goal is code where the intent is immediately legible.
`items.iter().filter(|x| x.is_ready()).map(|x| x.summarise()).collect()` communicates
what happens. `for item in items { if item.is_ready() { result.push(item.summarise()); } }`
requires the reader to reconstruct the intent from the mechanics. The lints surface loops
worth investigating; for each one, ask whether a combinator pipeline makes the intent clearer
— if yes, apply it; if the loop is genuinely clearer (rare but possible), leave it.

Reference: `docs/code-style/functional-transformations.md` — "Replacing common imperative loops".

- [ ] **T6-for**: For each `for` loop in domain code — ask what this loop IS:
  - Collection building → `map(...).collect()`
  - Filtering → `filter(...).collect()`
  - Transform + filter → `filter_map(...).collect()`
  - Side effects on each element → `for_each(...)` (only acceptable for diagnostic emission)
  - Early return on first match → `find(...)` or `find_map(...)`
  - Short-circuit validation → `try_for_each(...)` with `?`
  - Accumulation → `fold(init, f)` (without `mut acc` in the closure)
  - Counting → `count()` or `filter(...).count()`

- [ ] **T6-loop**: Each `loop` in domain code is one of — and each requires a different architectural fix:
  - **Retry loop** → belongs in reducer/orchestrator state machine (see Phase 4)
  - **Read-until-done loop** → belongs in boundary (streaming/I/O)
  - **State machine step** → replace with recursive pure function returning `Option<NextState>`
    or refactor as a `fold` over a sequence of state-transforming events
  - **Spin/poll loop** → belongs in `runtime/` boundary; domain code never polls

- [ ] **T6-while**: Similar audit as `loop` — classify each before touching it:
  - `while condition { mutate_state }` → convert the condition + mutation to a pure step
    function and use `std::iter::successors(init, step).take_while(predicate).last()`
  - `while let Some(x) = iter.next()` → replace with `for x in iter` or combinator pipeline
  - `while bytes_read > 0` → streaming I/O, belongs in boundary

---

### Phase 7 — Fix Interior Mutability in Domain Code (Pure Shared References)

`&T` must be truly immutable in domain code. Interior mutability is permitted only in
boundary modules where the underlying capability demands it.

- [ ] **T7-mutex (6 violations — `std::sync::Mutex`)**: For each:
  - If guarding shared state across threads in domain code → re-model as explicit state
    flowing through the reducer/orchestrator cycle. Domain code is single-threaded
    and pure; concurrency is a boundary/runtime concern.
  - If guarding I/O resources → move the Mutex-wrapped resource to a boundary module.
  - If in a `LazyLock<Mutex<T>>` pattern → see LazyLock fix below.

- [ ] **T7-lazylock (4 violations — `std::sync::LazyLock`)**: For each:
  - Static compile-time data → replace with `const` or `static` without LazyLock
  - Compiled regex statics → move to the boundary module that uses them, or use
    `once_cell::sync::Lazy` in a `runtime/` boundary module with documented justification
  - Configuration-derived statics → replace with injected parameters (Reader pattern)

- [ ] **T7-cell (2 violations — `std::cell::Cell`)**: Replace `Cell<T>` usage with
  value-returning transformations. If `Cell` is used for a counter or flag, thread the
  value through the function signature explicitly instead.

---

### Phase 8 — Fix git_helpers Architecture (Major Module Refactor)

`git_helpers/config_state.rs` has ~85 `.unwrap()` calls — a symptom of deep architectural
issues: mixing pure git state interpretation with effectful git operations, using panic-driven
control flow throughout.

- [ ] **T8-audit**: Map the full `git_helpers/` module: for each function, classify as
  (a) pure — interpreting git output, building command specs, parsing diff format, or
  (b) effectful — executing git commands, reading `.git/` directory, writing refs.
  Record findings in notepad.

- [ ] **T8-split**: Reorganize `git_helpers/` into:
  - `git_helpers/` (domain) — pure functions: parse git status output, build `git` command
    specs, interpret diff hunks, classify commit messages. No `std::process`, no `git2`.
  - `git_helpers/boundary.rs` or `io/git.rs` (boundary) — thin adapter that runs git
    commands via `ProcessExecutor` trait or `git2` (in `ffi/`), returns typed events.
  
- [ ] **T8-errors**: Replace all ~85 `.unwrap()` in `git_helpers/` with proper `Result<T, E>`
  propagation. Create a `GitError` enum covering: `NotARepository`, `NoCommits`,
  `ParseFailed(String)`, `ExecutionFailed(ProcessError)`, etc.

- [ ] **T8-tests**: Write unit tests for every pure `git_helpers/` domain function before
  refactoring (red-first TDD). Pure parsers accept `&str` input — no git subprocess needed.

---

### Phase 9 — Fix Error and Diagnostics Architecture (Except + Writer Monads)

- [ ] **T9-unwrap-domain**: Audit all `.unwrap()` in non-test, non-boundary domain code.
  For each: replace with `?` propagation, or `ok_or_else(|| ErrorVariant::Specific)`.
  Never `.unwrap()` in domain code except where truly unreachable (then use
  `expect("invariant: <explanation>")` sparingly and document why in a code comment).

- [ ] **T9-expect-domain**: Audit `.expect()` in domain code. Each `expect("message")` is
  either a hidden `.unwrap()` (fix it) or a documented compile-time invariant (add a code
  comment explaining why the Option/Result can never be None/Err at this point).

- [ ] **T9-panic-domain**: Audit `panic!` in non-boundary, non-test domain code. Replace
  with `Result` typed failures. `panic!` belongs only in: test assertions, documented
  build-time invariants in `xtask/`, boundary crash-on-invariant helpers at app entry points.

- [ ] **T9-string-errors**: Audit domain functions that return `String`-typed errors or use
  `Box<dyn Error>` as the error type. Replace with small typed enums:
  ```rust
  #[derive(Debug, Clone, PartialEq, Eq)]
  pub enum ConfigParseError { MissingField(&'static str), InvalidValue(String) }
  ```

- [ ] **T9-diagnostics-as-data**: Identify domain functions that currently call `println!`,
  `eprintln!`, `log::warn!`, or similar side effects. Refactor to return
  `WithDiagnostics<T>` or `Logged<T>` (see `errors-and-diagnostics.md`). The BOUNDARY
  emits diagnostics; domain code returns them as values. This enables pure testing without
  output capture.

  ```rust
  // WRONG: domain printing side effects
  fn normalize_config(raw: RawConfig) -> Config {
      if raw.timeout.is_none() { eprintln!("warn: using default"); }
      Config { timeout: raw.timeout.unwrap_or(30) }
  }
  // RIGHT: diagnostics as data
  fn normalize_config(raw: RawConfig) -> WithDiagnostics<Config> {
      let diags = [raw.timeout.is_none().then_some(ConfigDiag::UsedDefaultTimeout)]
          .into_iter().flatten().collect();
      WithDiagnostics { value: Config { timeout: raw.timeout.unwrap_or(30) }, diagnostics: diags }
  }
  ```

---

### Phase 10 — Type-Driven Design at Edges (Parse, Don't Validate)

Apply "parse, don't validate" at every boundary intake. Once raw input is parsed to a
stronger type, downstream domain code never re-checks the same invariant.

- [ ] **T10-newtypes**: Audit boundary intake functions for raw `String`, `u32`, `Vec<T>`
  parameters where the type carries implicit invariants. Introduce newtypes:
  - `NonEmptyString(String)` — strings that must be non-empty
  - `BoundedRetryCount(u32)` — retry counts within a valid range
  - `NonEmptyTargets { first: String, rest: Vec<String> }` — collections needing ≥1 element
  Use smart constructors with `TryFrom` or a named parse function that returns `Result`.

- [ ] **T10-parse-at-edge**: For every boundary function that performs field-presence checks
  or domain invariant enforcement inline, extract a `parse_*` function that produces a
  stronger domain type. The boundary calls `parse_*` once; downstream code receives the
  proven type.

- [ ] **T10-raw-types-inward**: Identify boundary functions that return or pass inward
  capability-native types: `std::process::Output`, `git2::Oid`, `reqwest::Response`, raw
  byte buffers. Translate these to domain types at the boundary edge before they cross
  inward. (This is also enforced by `forbid_raw_effect_types_in_public_apis` lint.)

---

### Phase 11 — Test Coverage for All Refactored Code (TDD)

Per the testing guide, every refactored module must have test coverage. Write red-first.

- [ ] **T11-tdd-rule**: For every function touched in Phases 2–10: write a failing test
  FIRST, then make it pass. No exception. If a function is pure after refactoring, the
  test uses plain value inputs — no mocks, no fakes, no I/O.

- [ ] **T11-git-helpers**: Write unit tests for the pure `git_helpers/` domain functions
  after the Phase 8 split. Each pure parser (status output, diff format, commit message
  classification) tests on string inputs — no subprocess required.

- [ ] **T11-boundary-seams**: For each boundary seam created or restructured in Phase 2–3,
  write an integration test verifying: the right capability method is called; capability
  errors map to the right typed boundary error; the typed result or event is correct.
  Use `MemoryWorkspace` + `MockProcessExecutor`.

- [ ] **T11-error-types**: For each new typed error enum from Phase 9, write a test for
  every variant: the correct variant is produced from the corresponding invalid input.

- [ ] **T11-diagnostics**: For each `WithDiagnostics<T>` function from Phase 9, write:
  (a) test that correct value is produced, (b) test that diagnostics list is correct when
  defaults/clamping occur, (c) test that diagnostics list is empty when input is nominal.

- [ ] **T11-with-star-builders**: For each `with_*` consuming builder pattern introduced
  in Phase 5, write tests verifying: correct default state, each field set independently,
  chained builds produce correct composite state.

- [ ] **T11-no-serial**: Verify that all new tests pass in parallel mode (no `#[serial]`
  in unit or integration tests). Use env-injection pattern for any environment access.

---

### Phase 12 — Add Property-Based Testing Infrastructure

- [ ] **T12-proptest-dep**: Add `proptest = "1"` to `[dev-dependencies]` in
  `ralph-workflow/Cargo.toml`. Add a `[profile.test]` entry in workspace `Cargo.toml` to
  cap proptest case count for CI: `proptest.cases = 256`.

- [ ] **T12-parser-props**: Add property tests for the core XML parsing pipeline in
  `ralph-workflow/src/files/llm_output_extraction/`. Verify: (a) parsing any valid XML
  snippet never panics; (b) a round-trip `serialize → parse` produces the original structure;
  (c) the parser rejects known-invalid inputs with the correct error variant.

- [ ] **T12-reducer-props**: Add property tests for key reducers. Verify:
  (a) `reduce(state, event)` always returns a valid state (no panic);
  (b) applying the same event twice on idempotent variants produces the same state;
  (c) arbitrary event sequences from the initial state produce states satisfying
  all documented state invariants (e.g., counters never go negative).

- [ ] **T12-builder-props**: Add property tests for `with_*` builder chains:
  applying an arbitrary sequence of valid `with_*` calls always produces a consistent
  struct (no panic, all fields coherent).

---

### Phase 13 — Add Code Coverage Instrumentation

- [ ] **T13-llvm-cov**: Add `cargo-llvm-cov` to the project's dev tooling. Document the
  installation step (`cargo install cargo-llvm-cov`) in `docs/agents/verification.md`.
  Add a `[workspace.metadata.coverage]` section to `Cargo.toml` specifying excluded paths
  (target/, lints/, test-helpers/).

- [ ] **T13-xtask-coverage**: Add a `cargo xtask coverage` subcommand that runs:
  ```bash
  cargo llvm-cov --all-features --lib -p ralph-workflow --html --output-dir target/coverage/html
  cargo llvm-cov report --lib -p ralph-workflow  # line coverage summary to stdout
  ```
  Coverage output is a diagnostic signal — use it to find untested paths and ask whether
  the untested code matters. Do not set a hard-fail threshold; a hard threshold incentivises
  writing tests that exist solely to bump a number rather than to verify behaviour.

- [ ] **T13-docs**: Document the coverage workflow in `docs/agents/verification.md`:
  add the `cargo xtask coverage` command reference and a note that coverage is an
  investigative tool — low coverage on a module is a prompt to ask "do we understand
  the failure modes here?", not a build gate.

---

## Final Verification Wave

These are APPROVAL GATES — each must pass before the plan is considered complete.
The gates are questions about real quality, not metrics to hit.

- [ ] **F1 — Architecture is genuinely correct (primary gate)**

  An oracle agent reads the boundary modules restructured in Phase 2 and answers
  these questions from first principles — NOT by checking lint output:

  - Does each boundary function follow the IMPURE→PURE→IMPURE shape? Can you describe
    every boundary function as "gather X from capability, call pure_helper(X), perform effect"?
    If a boundary function needs more than a sentence to describe, it is doing too much.
  - Is domain code genuinely pure? Do domain functions accept only plain values, return only
    plain values, and contain zero capability imports? Could you unit-test every domain
    function with a single `assert_eq!(f(input), expected_output)` and no setup?
  - Are errors modelled as values? Are all recoverable failures `Result<T, E>` with typed
    error enums? Is `unwrap()`/`panic!` absent from domain code for any reason other than
    a documented, unreachable compile-time invariant?
  - Are diagnostics returned as data? Do pure functions that normalise or default inputs
    return `WithDiagnostics<T>` instead of printing? Does only the boundary emit?
  - Is retry policy in the state machine, not in boundary loops? Is there any retry logic
    that lives inside a handler/boundary rather than being driven by reducer + orchestrator?

  VERDICT: APPROVE if the architectural principles are genuinely present.
  REJECT with specific file:line citations if any principle is violated — regardless of
  whether a lint fires on it.

- [ ] **F2 — Build and tests are clean**

  `cargo xtask verify` passes all 7 lanes with no ERROR/WARNING diagnostics.
  `cargo test -p ralph-workflow --lib --all-features` and
  `cargo test -p ralph-workflow-tests --test integration_tests` both pass.
  No `#[serial]` in unit or integration tests. All tests are deterministic.

- [ ] **F3 — Tests verify behaviour, not structure**

  An oracle agent reads a sample of 10 newly written tests and answers:
  - Does each test assert on an observable outcome (return value, event emitted, state
    transition) rather than on internal implementation details?
  - Are the test names descriptions of behaviour ("rejects_empty_agent_chain") not
    implementation ("calls_push_on_buffer")?
  - Do pure-function tests require zero mocking, zero I/O setup, zero fakes?
  - Do boundary tests verify the wiring contract (right capability called, errors mapped
    correctly, typed result returned) without asserting on internal call counts?

  VERDICT: APPROVE if tests are genuinely behaviour-oriented.
  REJECT with specific test names that violate this if found.

- [ ] **F4 — Dylint as a final diagnostic scan (informational, not a gate)**

  Run `cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet` and review
  the output. For each remaining error:
  - If it points to a genuine architectural problem missed by the plan → create a follow-up
    task, fix it, and re-run F1.
  - If it is a lint false-positive on genuinely correct code → document it with a code
    comment explaining why the code is correct, and note it as a known lint limitation.

  This gate has no "must be zero" requirement. Its purpose is to catch things the
  architecture review may have missed — using the lints as a second pair of eyes, not
  as a judge.
