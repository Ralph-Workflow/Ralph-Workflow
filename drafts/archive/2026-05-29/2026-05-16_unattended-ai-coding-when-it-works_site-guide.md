# When Unattended AI Coding Actually Works

## The real question
The question is not whether an AI coding tool can keep running while you are away.

The real question is whether you can come back to a result that is small enough to review, clear enough to trust, and solid enough to merge.

## When unattended runs are worth it
Unattended AI coding works best when:
- the task is clearly scoped
- success is easy to define
- rollback is cheap
- tests exist or can be run
- the result can land as a reviewable diff

Good examples:
- backlog items
- bounded refactors with tests
- feature slices with clear edges
- repetitive implementation work with obvious checks

## When unattended runs go wrong
They usually fail when:
- the task is vague
- multiple objectives get mixed together
- the agent decides what “done” means on its own
- verification is weak or skipped
- the final handoff is too big to inspect quickly

## What the workflow needs
A useful unattended workflow should:
1. sharpen the task before code starts
2. isolate the work
3. build and verify in one loop
4. stop weak work before it reaches the finish line
5. return a clean re-entry point: diff, checks, reasoning, open questions

## Where Ralph Workflow fits
Ralph Workflow is built for work that is too big to babysit and too risky to trust blindly.

The goal is not just to keep a tool running.

The goal is to come back to something reviewable.

## The test
Ask one question:

**Would you merge this?**

If yes, unattended coding helped.
If not, the workflow still needs work.
