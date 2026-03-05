# Refactoring Guide

This guide documents best practices and patterns for refactoring the Ralph Workflow codebase, especially when addressing technical debt like oversized files, unsafe code patterns, and deprecated APIs.

## Table of Contents

- [When to Refactor](#when-to-refactor)
- [File Splitting Guidelines](#file-splitting-guidelines)
- [TDD Workflow for Refactoring](#tdd-workflow-for-refactoring)
- [Documentation Requirements](#documentation-requirements)
- [Verification Checklist](#verification-checklist)
- [Common Patterns](#common-patterns)

## When to Refactor

### File Size Limits

Split a file when it exceeds:

- **Production code**: 300 lines (guideline), 500 lines (hard limit per `CODE_STYLE.md`)
- **Test code**: 1000 lines (hard limit per `tests/INTEGRATION_TESTS.md`)

Or when it violates single responsibility:

- Needs a paragraph to explain what the file does
- Combines unrelated concepts (e.g., parsing + validation + rendering)
- Has deeply nested logic that's hard to follow

### Code Smells That Warrant Refactoring

- **Unsafe patterns**: `unwrap()` in production code, panics in handlers
- **Deprecated APIs**: Methods with migration notes, `#[deprecated]` annotations
- **Code duplication**: Similar logic repeated across multiple locations
- **Poor naming**: Unclear variable/function names that require comments to explain

## File Splitting Guidelines

### How to Split (TDD Workflow)

**Critical**: Every refactoring step MUST follow TDD principles per `AGENTS.md`.

1. **Baseline**: Run tests, verify 100% pass
   ```bash
   cargo test -p ralph-workflow --lib --all-features
   cargo test -p ralph-workflow-tests
   ```

2. **Coverage**: Identify test coverage for each logical group
   - Check which functions are tested
   - Identify missing tests for code being split

3. **Missing tests**: Write tests for uncovered code BEFORE splitting
   - Use TDD: write test first, then ensure it passes
   - This is your safety net for the refactoring

4. **Incremental**: Move code in small batches (50-100 lines)
   - Create new file with module documentation
   - Move one logical unit at a time
   - Update imports in original file

5. **Verify**: Run tests after EACH batch
   ```bash
   cargo test -p ralph-workflow --lib
   ```
   - If tests fail, STOP and investigate
   - Revert if needed to understand the issue

6. **Document**: Add module docs to each new file (see below)

7. **Final**: Run full verification suite (see below)

### Common Split Patterns

**Events**: By category (Planning, Development, Review, Commit)
```
event.rs (771 lines)
  → event/
      mod.rs          - Module docs, re-exports
      types.rs        - Core event enum definitions
      agent.rs        - Agent-related events
      development.rs  - Development phase events
      review.rs       - Review phase events
      ...
```

**Handlers**: By effect type or pipeline phase
```
handler.rs (large)
  → handler/
      mod.rs          - Module docs, shared types
      planning.rs     - Planning effect handlers
      development.rs  - Development effect handlers
      review.rs       - Review effect handlers
      commit.rs       - Commit effect handlers
```

**Tests**: By scenario type or test category
```
edge_cases.rs (1640 lines)
  → edge_cases/
      mod.rs              - Module docs, shared fixtures
      conflict_scenarios.rs - Merge conflict tests
      noop_scenarios.rs     - No-op condition tests
      validation.rs         - Precondition validation tests
```

**Utils**: By domain (filesystem, parsing, validation)
```
utils.rs
  → utils/
      mod.rs      - Module docs, re-exports
      fs.rs       - Filesystem utilities
      parsing.rs  - Parsing helpers
      validation.rs - Validation functions
```

### Preserving Public API with Re-exports

When splitting a public module, use re-exports in `mod.rs` to avoid breaking changes:

```rust
//! # Module Name
//!
//! Brief description...

// Internal modules
mod types;
mod constructors;
mod conversions;

// Re-export public API
pub use self::types::*;
pub use self::constructors::*;
pub use self::conversions::*;

// Keep tests at module level
#[cfg(test)]
mod tests {
    use super::*;
    // ...
}
```

This ensures external code using `use module::Type` continues to work.

## TDD Workflow for Refactoring

Even pure refactoring (no behavior change) follows a modified TDD cycle:

```
RED:    Verify tests exist and pass before changes
GREEN:  Make the structural change, tests still pass  
REFACTOR: Clean up, improve docs, tests still pass
```

### Testing Each Layer

**Reducer tests (pure, no mocks):**
```rust
#[test]
fn test_agent_failure_increments_retry_count() {
    let state = PipelineState { retry_count: 0, .. };
    let event = AgentEvent::InvocationFailed { retriable: true, .. };
    
    let new_state = reduce(state, event);
    
    assert_eq!(new_state.retry_count, 1);
}
```

**Handler tests (with MemoryWorkspace):**
```rust
#[test]
fn test_invoke_agent_emits_success_event() {
    let workspace = MemoryWorkspace::new_test();
    let mut ctx = create_test_context(&workspace);
    let mut handler = MockEffectHandler::new()
        .expect_agent_success("output");
    
    let result = handler.execute(Effect::InvokeAgent { .. }, &mut ctx)?;
    
    assert!(matches!(result.event, AgentEvent::InvocationSucceeded { .. }));
}
```

### When Tests Fail

If tests fail unexpectedly during refactoring:

1. **STOP** - Do not push forward hoping it will work out
2. **Investigate** - Understand what broke and why
3. **Revert if needed** - Small commits make this easy
4. **Fix the root cause** - Don't patch symptoms
5. **Re-run tests** - Verify the fix before continuing

## Documentation Requirements

### Module-Level Documentation

Every new file MUST have comprehensive module documentation:

```rust
//! # Module Name
//!
//! Brief description of what this module does.
//!
//! ## Overview
//!
//! More detailed explanation of the module's role in the system.
//! Explain concepts, architecture, and design decisions.
//!
//! ## Usage
//!
//! ```rust
//! // Example of how to use this module
//! use ralph_workflow::module::Type;
//!
//! let x = Type::new();
//! ```
//!
//! ## See Also
//!
//! - `related::module` - Related functionality
//! - `docs/architecture/system.md` - Architecture documentation
```

### Function Documentation

Public items (functions, structs, enums) MUST have doc comments:

```rust
/// Brief description of what this does.
///
/// More detailed explanation if needed. Explain the "why" not just the "what".
///
/// # Arguments
///
/// * `param` - What this parameter represents and constraints
///
/// # Returns
///
/// What the function returns and when
///
/// # Errors
///
/// When this function returns an error (for Result types)
///
/// # Examples
///
/// ```rust
/// use ralph_workflow::example_function;
///
/// let result = example_function(42)?;
/// assert_eq!(result, expected);
/// ```
pub fn example_function(param: Type) -> Result<Output, Error> {
    // Implementation
}
```

### Documentation for Test Infrastructure

Test utilities and mock implementations need especially good documentation:

```rust
//! # Mock Effect Handler
//!
//! Test infrastructure for mocking effect execution in integration tests.
//!
//! ## Usage Example
//!
//! ```rust
//! use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
//!
//! let mut handler = MockEffectHandler::new()
//!     .expect_effect(Effect::InvokeAgent { role: Developer })
//!     .with_result(Ok(success_event));
//!
//! // Execute effect in test
//! let result = handler.execute(effect)?;
//!
//! // Verify expectations
//! handler.verify_all_expectations();
//! ```
```

## Verification Checklist

### Before Committing Refactoring Work

Run ALL verification commands from `docs/agents/verification.md`:

```bash
# 1. Format check
cargo fmt --all --check

# 2. Lint main crate
cargo clippy -p ralph-workflow --lib --all-features -- -D warnings

# 3. Lint integration tests  
cargo clippy -p ralph-workflow-tests --all-targets -- -D warnings

# 4. Unit tests
cargo test -p ralph-workflow --lib --all-features

# 5. Integration tests
cargo test -p ralph-workflow-tests

# 6. Integration test compliance
./tests/integration_tests/compliance_check.sh
./tests/integration_tests/no_test_flags_check.sh

# 7. Release build
cargo build --release

# 8. Custom lints
make dylint
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable).

### Critical Rule: Fix ALL Failures

Per `AGENTS.md`: **If verification fails (command failure or ERROR/WARNING diagnostics), you MUST fix ALL failures before committing** — even pre-existing issues you did not introduce.

Pre-existing failures become your TOP PRIORITY. The longer a failure exists, the more urgent it becomes.

## Common Patterns

### Pattern 1: Replacing unwrap() with expect()

For test infrastructure where panics are acceptable:

```rust
// Before
let files = self.files.read().unwrap();

// After - descriptive panic message
let files = self.files
    .read()
    .expect("MemoryWorkspace: RwLock poisoned - indicates panic in another thread");
```

### Pattern 2: Replacing unwrap() with Result

For production code:

```rust
// Before
fn get_config() -> Config {
    load_config().unwrap()
}

// After - proper error handling
fn get_config() -> Result<Config, Error> {
    load_config().map_err(|e| {
        Error::ConfigLoad(format!("Failed to load config: {}", e))
    })
}
```

### Pattern 3: Regex in lazy_static

Move runtime regex compilation to compile-time validation:

```rust
use lazy_static::lazy_static;
use regex::Regex;

lazy_static! {
    /// Regex for extracting issue IDs from review output.
    /// Format: issue-<number>
    static ref ISSUE_ID_RE: Regex = Regex::new(r"issue-(\d+)")
        .expect("ISSUE_ID_RE: invalid regex pattern - this is a developer error");
}

fn extract_issue_id(text: &str) -> Option<String> {
    ISSUE_ID_RE.captures(text)
        .and_then(|cap| cap.get(1))
        .map(|m| m.as_str().to_string())
}
```

### Pattern 4: Builder Pattern for Many Arguments

When a function has too many arguments:

```rust
// Before - suppressed warning
#[allow(clippy::too_many_arguments)]
fn create_scenario(a: A, b: B, c: C, d: D, e: E, f: F, g: G) -> Scenario {
    // ...
}

// After - builder pattern
struct ScenarioBuilder {
    a: Option<A>,
    b: Option<B>,
    // ...
}

impl ScenarioBuilder {
    fn new() -> Self { /* ... */ }
    fn with_a(mut self, a: A) -> Self { /* ... */ }
    fn build(self) -> Scenario { /* ... */ }
}

// Usage
let scenario = ScenarioBuilder::new()
    .with_a(value_a)
    .with_b(value_b)
    .build();
```

## Reducer Architecture Considerations

When refactoring reducer/handler/orchestration code, remember the architecture contract:

**Reducers MUST be pure:**
- No I/O (std::fs, std::env, network)
- No side effects (logging, random, time)
- Deterministic: same (state, event) → same new state

**Events MUST be descriptive facts:**
- Past tense: `InvocationSucceeded`, not `RetryAgent`
- Describe what happened, not what to do
- Carry data needed for reducer decisions

**Handlers execute, reducers decide:**
- Handler: perform ONE effect attempt, report outcome as event
- Reducer: decide next action based on events
- No hidden retry loops in handlers

See `docs/architecture/event-loop-and-reducers.md` for full details.

## Summary

The key to successful refactoring:

1. **TDD always** - Tests before and after every change
2. **Small batches** - Move 50-100 lines at a time
3. **Test frequently** - After every small change
4. **Document thoroughly** - Especially for complex code
5. **Verify completely** - All verification commands must pass
6. **Fix all failures** - Even pre-existing ones

Remember: The goal is to make the code more maintainable. If a refactoring makes code harder to understand, reconsider the approach.
