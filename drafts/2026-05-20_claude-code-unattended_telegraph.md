# Run Claude Code Unattended — What the Documentation Does Not Tell You

The Claude Code documentation explains how to run the CLI. It does not explain how to run it unattended and wake up to something useful.

Most people who try end up with one of two outcomes: a session that stalled on a rate limit three hours in, or a session that ran all night and produced a diff nobody can review.

This is not a model intelligence problem. It is a workflow design problem.

## What Unattended Actually Requires

Running Claude Code unattended is not the same as running it in the background. Unattended means:

- The task is bounded before the run starts
- Progress is checkpointed so a crash does not lose everything
- The run has a defined finish condition, not a time limit
- The result is reviewable without re-executing the session

Without these four properties, you are not running unattended. You are just hoping.

## The Spec-First Pattern

The practical pattern that works:

**Before the run:** Write a short SPEC.md. What does success look like? What should be different when this finishes? What should still be true?

**During the run:** Let the agent work. If it hits a rate limit, a fallback agent continues. If it crashes, it resumes from the last checkpoint.

**After the run:** You open your editor and see: a diff, test results, and a short receipt of what changed and what still needs attention. No transcript archaeology.

## How Ralph Workflow Handles It

Ralph Workflow is a CLI that wraps Claude Code (and Codex, OpenCode) into an unattended loop with these properties built in.

- Planning loop: draft and critique the spec before code is touched
- Development loop: build, check, retry against the spec
- Fallback chains: switch agents automatically on rate limits
- Checkpoint resume: crash recovery without starting over
- Reviewable finish artifact: diff + checks + receipt

It runs on your own machine. No cloud dependency. No session monitoring required.

**Primary repo (Codeberg):** https://codeberg.org/RalphWorkflow/Ralph-Workflow
**GitHub mirror:** https://github.com/Ralph-Workflow/Ralph-Workflow
