# Example Review Bundle

See the canonical product description in [README.md](../../README.md).

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: this folder shows the kind of **reviewable output** Ralph Workflow is meant to leave behind after one unattended run.

Why look at it now? Because you can inspect the prompt, the morning-after notes, and the artifact shape before you decide whether Ralph Workflow is worth trying on your own backlog.

## What this example shows

This is a small first-run example for a real kind of task:

- reject empty project names in a CLI before any files are created
- keep valid behavior unchanged
- add tests

The point is not that this exact task is special.
The point is that the handoff should be easy to review.

## Review order

1. Open [`PROMPT.md`](./PROMPT.md).
2. Read [`.agent/DEVELOPMENT_RESULT.md`](./.agent/DEVELOPMENT_RESULT.md).
3. Read [`.agent/ISSUES.md`](./.agent/ISSUES.md) and [`.agent/FIX_RESULT.md`](./.agent/FIX_RESULT.md).
4. Glance at the small JSON artifacts under [`.agent/artifacts/`](./.agent/artifacts/).
5. Ask one question: **would I merge this?**

If that path feels clear and fast, the handoff is doing its job.
