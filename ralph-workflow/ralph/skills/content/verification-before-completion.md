# verification-before-completion

## Purpose
Verification-before-completion is the rule that no task is complete until the relevant checks have actually passed. It protects against accidental claims of success when code, tests, or docs still disagree with the request.

This is one of the most important quality controls in unattended development. It makes completion claims trustworthy and prevents the repo from being left in a half-finished state that only looks done from a distance.

## When To Use
- Before declaring a task complete.
- After a meaningful code change.
- When the user asked for a bug fix, feature, or refactor.
- Whenever verification commands are available and relevant.

## Key Steps / Approach
1. Identify the exact checks that prove the user outcome.
2. Run the checks on the changed surface, not just a tiny subset.
3. Inspect diagnostics and fix any issues caused by the change.
4. Do not claim success until the relevant commands pass cleanly.
5. Report the proof succinctly and honestly.

## Common Pitfalls
- Declaring victory without running the checks.
- Using vague language instead of concrete evidence.
- Ignoring unrelated existing failures without labeling them as pre-existing.
