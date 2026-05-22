# verification-loop

## Purpose
Verification-loop is the practice of iterating until the repository checks and the observable behavior both agree with the intended change. It turns verification into a structured feedback loop instead of a one-time command run.

The loop is useful because it keeps the agent from stopping at a single green check when other relevant checks still need attention. It also helps separate new failures from pre-existing ones.

## When To Use
- You need to prove a change is actually complete.
- A check fails and you need to converge on green.
- You want to avoid false confidence from partial verification.
- The task requires explicit proof before handoff.

## Key Steps / Approach
1. Run the relevant verification commands and read the outputs carefully.
2. Fix only the issues caused by the current change.
3. Re-run the checks after each fix so progress is measurable.
4. Stop only when the required proof is clear and reproducible.
5. Document the exact commands that established the final result.

## Common Pitfalls
- Running a check once and assuming the work is done.
- Conflating old repository debt with new regressions.
- Skipping the second pass that proves the fix stuck.
