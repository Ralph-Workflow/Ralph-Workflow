# Project Code Style Guide

This directory is the detailed reference for what Ralph code should look like.

The root `CODE_STYLE.md` file stays short and policy-focused. This directory expands those rules into concrete organization guidance and code patterns.

## Sections

- `docs/code-style/code-shape.md` - the defining shape of finished code
- `docs/code-style/architecture.md` - how reducers, orchestration, handlers, and effects fit together
- `docs/code-style/boundaries.md` - where `domain/`, `io/`, `runtime/`, `boundary/`, and `ffi/` code belongs
- `docs/code-style/module-organization.md` - how files and modules should be split and named
- `docs/code-style/coding-patterns.md` - detailed before/after examples for everyday Rust refactors
- `docs/code-style/functional-transformations.md` - practical cookbook for writing lint-compliant FP Rust (collection building, loop replacement, struct updates, conditional chains)
- `docs/code-style/errors-and-diagnostics.md` - how to model failures, warnings, and returned diagnostics
- `docs/code-style/generics-and-abstractions.md` - when explicit Rust is best and when abstractions like `frunk` are justified
- `docs/code-style/testing.md` - what tests should look like for each layer

## Recommended reading order

1. `docs/code-style/code-shape.md`
2. `docs/code-style/architecture.md`
3. `docs/code-style/boundaries.md`
4. `docs/code-style/module-organization.md`
5. `docs/code-style/coding-patterns.md`
6. `docs/code-style/functional-transformations.md`
7. `docs/code-style/errors-and-diagnostics.md`
8. `docs/code-style/generics-and-abstractions.md`
9. `docs/code-style/testing.md`

## What this guide is for

Use this directory when you need to answer questions like:

- Where should this code live?
- Should this function be pure or effectful?
- How should this module be split?
- What does a good reducer or handler look like?
- How do I rewrite this imperative pattern to satisfy the FP lints?
- How should errors and diagnostics be modeled?
- When is a generic abstraction justified?
- How should tests be shaped for this architecture?

This is not a migration history and not a refactoring diary. It is the reference for the codebase we want.

`docs/code-style/code-shape.md` is the permanent top-level shape document for this guide set.

## Example quality bar

All Rust examples in this directory should be written as if they belong in this codebase.

That means the examples should:

- be compatible with the repository's clippy and dylint rules
- avoid non-boundary mutation patterns
- avoid `unwrap()` and `.expect()` in ordinary code and tests
- show the preferred project code shape
