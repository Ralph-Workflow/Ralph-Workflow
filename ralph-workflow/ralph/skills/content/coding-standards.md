# coding-standards

## Purpose
Coding-standards is the skill for keeping code aligned with the repository's style, typing, naming, and maintainability expectations. It helps ensure a change reads like part of the codebase rather than a one-off patch.

Consistent standards make reviews faster and bugs easier to spot. They also reduce the chance that a fix introduces awkward patterns, dead code, or avoidable complexity.

## When To Use
- A change adds or refactors Python code.
- You need to decide between several acceptable styles.
- A new file or module should match the repository's conventions.
- The code needs to remain easy to test and maintain.

## Key Steps / Approach
1. Follow the repo's established patterns unless there is a clear reason not to.
2. Prefer explicit names, small functions, and narrow interfaces.
3. Avoid dead code, broad suppressions, and unnecessary abstraction.
4. Keep the change testable and compatible with the repo's verification tools.
5. Document any intentional deviation in a tight inline note only when unavoidable.

## Common Pitfalls
- Introducing cleverness where clarity is enough.
- Using type or lint suppressions as the first answer.
- Letting style drift make the code harder to verify later.
