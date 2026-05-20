# Claude Code Automation: Making Unattended Runs Actually Pay Off

Claude Code is capable enough that running it unattended for multi-hour sessions is tempting. Set it going, let it refactor the legacy module, come back to the result. In practice, most unattended Claude Code runs come back in one of two states: a large rewrite that requires significant review, or a session that hit a rate limit or network blip and exited early with no resume state.

The root cause in both cases is the same: the run had no defined finish contract and no checkpoint system.

## Why Most Claude Code Automation Feels Incomplete

Claude Code is designed to be used interactively. When you run it unattended, the interactive elements — your feedback at approval points, your responses to clarification prompts — disappear. Without them, the agent proceeds based on its own judgment of what matters, which often diverges from what you actually needed.

The result is a session that did a lot of work and handed back something hard to evaluate. The more ambitious the unattended task, the worse this gets.

## The Three Failure Modes of Unattended Claude Code

**Rate limit exit.** Claude API hits a rate limit, the session exits, and you lose all context. When you resume, you are starting over.

**Scope drift.** The agent decides the task includes refactoring the tests, updating the documentation, and cleaning up the configuration files it encountered along the way. You come back to a broader change than you expected, with no clean diff.

**Confident stopping.** The agent reaches a natural stopping point and declares the task complete, but the result is missing edge cases, the tests are not updated, and the API contract is not verified. It looks done. It is not done.

## What Unattended Claude Code Actually Needs

Unattended Claude Code needs the same structure that makes any team handoff work: a written spec before it starts, bounded execution during, and a reviewable result at the end.

The spec defines what "done" means. Bounded execution keeps the agent from drifting into adjacent files. The reviewable result — a bounded diff, check output, and explicit unresolved list — makes the morning-after review fast instead of a reconstruction project.

Checkpoint resume is also essential: if the session exits, you should be able to pick up where it left off, not restart from scratch.

## Making Claude Code Automation Work

Claude Code automation works when the workflow enforces these constraints around the agent. This means:

- Writing a scoped spec before starting the unattended session
- Running with explicit fail-closed behavior: if the agent cannot verify the result, it should stop and flag rather than proceed
- Capturing checkpoint state so rate limit exits can be resumed
- Producing a diff and check bundle as the finish receipt instead of a transcript

This is the pattern Ralph Workflow is built around. It runs a planning loop before the Claude Code session, enforces a bounded development phase, and captures a reviewable receipt at the end. Checkpoint resume handles rate limit exits.

The goal is not to make Claude Code more autonomous. It is to make unattended sessions produce results that are actually worth running unattended — bounded, checkable, and ready to review when you come back.
