# Code Style Guide

This is the brief standalone style guide for the project. The detailed reference lives in `docs/code-style/index.md`, but the core expectation is simple: Ralph code should separate pure decision-making from effectful execution, favor explicit data flow over cleverness, and stay easy to test and refactor.

Finished code should read like a sequence of clear transformations. The shape described in `docs/code-style/code-shape.md` is the target: parse and validate inputs, make pure decisions, then hand off to thin boundaries that perform the effect. A good module makes it obvious what happened, what it means, what should happen next, and which part of the system actually touches the outside world.

At the architectural level, described in `docs/code-style/architecture.md`, the project is reducer-driven: `State -> Orchestrator -> Effect -> Handler -> Event -> Reducer -> State`. State is immutable, orchestrators choose the next effect, handlers perform one concrete attempt, events describe facts that already happened, and reducers apply those facts to produce new state. Policy belongs in reducers and orchestration; handlers execute work but do not hide retries, fallback logic, or state changes.

That separation depends on strong boundaries, which `docs/code-style/boundaries.md` explains in detail. Domain code owns parsing, validation, normalization, reducers, and other pure logic. `io/` owns filesystem and transport work, `runtime/` owns process and OS-facing behavior, `boundary/` wires pure logic to concrete capabilities, and `ffi/` is reserved for foreign-library edges. If a function both decides and does, it is doing too much and should be split.

The same idea applies to file structure. As `docs/code-style/module-organization.md` explains, modules should be organized by stable responsibility, not by convenience or arbitrary line-count goals. Split files when they mix concerns such as parsing, validation, execution, and rendering, but do not split a cohesive reducer, state machine, or type definition only to make the numbers smaller. A file should have one clear reason to change.

Within those modules, the preferred style is explicit and composable. `docs/code-style/coding-patterns.md` favors plain Rust with pure helpers, immutable transformations, and iterator/combinator pipelines over hand-written imperative control flow. Use comments to explain why, not what. Use named constants instead of magic numbers. Prefer straightforward naming and obvious control flow over clever abstractions or compact tricks.

Errors and diagnostics should also be explicit. The guidance in `docs/code-style/errors-and-diagnostics.md` is to model failures with meaningful types, return diagnostics as data when they are part of the result, and keep domain meaning separate from presentation such as terminal output or UI display. Avoid turning important state into loose strings or incidental logging.

Abstractions should earn their keep. `docs/code-style/generics-and-abstractions.md` recommends starting with concrete code and introducing generics, traits, or heavier abstractions only when they clearly improve correctness, reuse, or readability. Indirection is not a virtue by itself; if an abstraction hides behavior or makes the code harder to follow, it is the wrong tradeoff.

Tests should match the layer they exercise, as described in `docs/code-style/testing.md`. Pure reducers, selectors, and helpers get plain unit tests with no mocks. Boundary and handler behavior gets integration-style tests with boundary-level doubles. Real filesystem, git, or process behavior belongs in system tests. In every case, tests should assert observable outcomes rather than internal implementation details.

Across all of this, keep a few working rules in mind: keep domain code pure and deterministic, push mutation and I/O to explicit boundaries, prefer clear and boring code over clever code, avoid `unwrap()` and `.expect()` outside documented exceptions, and delete dead code instead of suppressing warnings around it.

## Pipeline Architecture

If you change pipeline behavior, also read:

- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`

## Testing And Verification

- Read `docs/agents/testing-guide.md` before changing tests.
- Run `docs/agents/verification.md` before completion.

Use `docs/code-style/` as the source of truth for the detailed end-state guidance.
