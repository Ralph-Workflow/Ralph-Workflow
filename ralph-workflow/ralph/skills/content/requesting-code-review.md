# requesting-code-review

## Purpose
Requesting-code-review is the skill for preparing work so another reviewer can inspect it efficiently. It helps you present the change, its risks, and its proof without forcing the reviewer to reconstruct context from scratch.

A good review request raises the quality of feedback and shortens the cycle to approval. It also keeps handoff expectations honest by showing what was changed, verified, and left intentionally untouched.

## When To Use
- The implementation is complete and verified.
- You want another set of eyes before merge or handoff.
- The change touches important behavior or a risky boundary.
- You need review feedback that is actionable instead of vague.

## Key Steps / Approach
1. Summarize the goal and the exact files changed.
2. Call out the verification commands and their results.
3. Mention any deliberate trade-offs or known limitations.
4. Make it easy for the reviewer to inspect the highest-risk areas first.
5. Ask for concrete feedback on correctness, maintainability, and tests.

## Common Pitfalls
- Submitting an unexplained diff.
- Hiding verification failures or skipped checks.
- Asking for review before the work is actually ready.
