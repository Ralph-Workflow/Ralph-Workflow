# systematic-debugging

## Purpose
Systematic-debugging is the discipline of diagnosing failures by reproducing them, isolating causes, and testing hypotheses one at a time. It replaces guesswork with evidence and keeps you from making random changes in the hope that something sticks.

This skill matters whenever a check fails, a regression appears, or the runtime behavior does not match the design. A structured loop prevents wasted edits and makes the final fix easier to explain and verify.

## When To Use
- A test or verification command fails.
- The implementation behaves differently than expected.
- A recent change introduced a regression.
- You need to distinguish root cause from symptom.

## Key Steps / Approach
1. Reproduce the failure reliably and capture the exact error.
2. Reduce the problem to the smallest failing surface.
3. Check the most likely root cause before touching unrelated code.
4. Apply one targeted fix and re-run the relevant verification.
5. If the failure persists, revise the hypothesis rather than stacking guesses.

## Common Pitfalls
- Shotgun debugging across unrelated files.
- Changing tests to match broken behavior instead of fixing the code.
- Stopping after one pass without proving the root cause.
