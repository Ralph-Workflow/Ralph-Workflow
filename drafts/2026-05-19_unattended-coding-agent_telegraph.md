# Unattended Coding Agent: What It Actually Means and How to Run One Safely

Most AI coding tools work fine when you are watching. The problem starts when you need to leave.

"Unattended" does not mean "fire and forget." It means the task has a clear spec before it starts, the agent works within acceptance criteria instead of improvising from a vague prompt, something verifies output before you have to trust it blind, and you can inspect what happened when you come back.

Without those things, you are not running an unattended coding agent. You are running an unsupervised one.

## What actually breaks unattended runs

From real usage patterns, the top failure modes are predictable:

- no spec, so the agent optimizes for completing the prompt instead of solving the problem
- no checkpointing, so a crash at hour three loses everything
- no verification, so the agent says done and the code does not hold up
- no review surface, so you come back to a pile of changes with no clear way to audit them

## How to run an unattended coding agent without losing your mind

### 1. Sharpen the task first

Write a real spec before anything runs. Not prompt fluff. A handoff note with constraints and acceptance criteria.

Example:

The task: add rate limiting to the /api/users endpoint
Constraints:
- Use in-memory token bucket, no external Redis
- Return 429 with Retry-After header
- Do not change the existing auth flow
- Add unit tests for the rate limiter in isolation

### 2. Isolate the work

Use a dedicated branch or worktree. The unattended run should not touch your main branch until you decide the result is worth merging.

### 3. Build and verify in loops

A trustworthy unattended loop is not run once and declare victory.

It should sharpen, build, verify, fix what breaks, and only move forward when the checks actually hold up.

### 4. End with a reviewable handoff

When the run finishes, you should be able to:

- see exactly what changed
- run the checks independently instead of trusting a claim
- read a short finish note that explains what the agent did and why

## Where Ralph Workflow fits

Ralph Workflow is a free and open-source CLI for developers who want work that is too big to babysit and too risky to trust blindly to come back as a reviewable result.

It works with the coding agents you already use on your own machine. The point is not to replace Claude Code, Codex CLI, or OpenCode. The point is to make unattended runs spec-first, verified, and easier to review in the morning.

## Best first evaluation path

1. Inspect the primary Codeberg repo first
2. Run one real backlog task tonight
3. Judge the morning-after handoff with one question: would I merge this?

If the answer is yes, it earned a real place in your workflow. If not, the gap should be visible enough to fix.

## Why try it now

Because Ralph Workflow is free and open source, runs on your own machine with the agents you already have, and gives you a practical way to test whether an unattended coding agent can handle one real task without turning into transcript archaeology.
