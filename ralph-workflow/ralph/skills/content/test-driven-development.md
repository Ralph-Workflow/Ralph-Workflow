# test-driven-development

## Purpose
Test-driven-development is the habit of writing a failing test before production code when behavior changes. It keeps the implementation honest, makes intent explicit, and prevents accidental broadening of scope during a fix or feature add.

TDD is especially effective in this repository because the verification story is strong and the code is expected to remain testable in a black-box way. It nudges design toward small seams, deterministic behavior, and clear assertions.

## When To Use
- Adding new behavior.
- Fixing a bug that should never regress.
- Refactoring code whose behavior must stay stable.
- Introducing a new boundary or public API.

## Key Steps / Approach
1. Write a focused test that captures the new observable behavior.
2. Confirm it fails for the right reason.
3. Implement the smallest production change that makes it pass.
4. Refactor only after the test is green.
5. Keep the test fast, deterministic, and behavior-focused.

## Common Pitfalls
- Writing implementation-shaped tests.
- Using sleeps, real I/O, or fragile timing in unit coverage.
- Skipping the red-green-refactor loop because the change seems simple.
