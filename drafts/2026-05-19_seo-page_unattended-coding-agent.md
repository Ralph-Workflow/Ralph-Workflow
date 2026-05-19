# Unattended Coding Agent: What It Actually Means and How to Run One Safely

**Target keyword:** `unattended coding agent`  
**Page type:** SEO guide / landing page  
**Goal:** Rank for the keyword; convert searchers to Codeberg repo visits

---

## What "Unattended" Actually Means

Most AI coding tools work fine when you are watching. The problem starts when you need to leave.

"Unattended" does not mean "fire and forget." It means:

- The task has a clear spec before it starts
- The agent works within acceptance criteria, not just a prompt
- Something verifies output before you have to trust it blind
- You can inspect what happened when you come back

Without those four things, you are not running an unattended agent. You are running an unsupervised one.

## The Difference That Matters

| Supervised (watching) | Unattended (spec-driven) |
|---|---|
| You catch mistakes as they happen | Spec catches mistakes before they compound |
| You decide when it is "done" | Acceptance criteria define done |
| Output trust is manual | Output trust is built into the loop |
| Works for small, bounded tasks | Works for tasks that run overnight |

## What Actually Breaks Unattended Runs

From real usage patterns, the top failure modes are:

1. **No spec** — the agent optimizes for completing the prompt, not solving the problem
2. **No checkpointing** — a crash at hour 3 loses everything
3. **No verification** — the agent says done, the code does not work
4. **No review surface** — you come back to a pile of changes with no way to audit them

## How to Run an Unattended Coding Agent (Without Losing Your Mind)

### 1. Sharpen the task first

Write a `PROMPT.md` or spec before anything runs. Not a prompt injection — a real specification:

```
The task: add rate limiting to the /api/users endpoint
Constraints:
- Use in-memory token bucket, no external Redis
- Return 429 with Retry-After header
- Do not change the existing auth flow
- Add unit tests for the rate limiter in isolation
```

### 2. Isolate the work

Run in a dedicated worktree or branch. The agent should not touch your main branch until you say so.

### 3. Build and verify in loops

Not: run → done.

Real unattended loop:
- sharpen → build → verify → if checks pass, next phase → if not, fix and retry
- Log every phase so you can reconstruct what happened

### 4. End with a reviewable handoff

When the agent says it is done, you should be able to:

- See exactly what changed (diff)
- Run the checks independently (not just trust the log)
- Read a summary of what the agent did and why

## The Tool That Does This: Ralph Workflow

[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is an open-source CLI that runs this loop for you. It is agent-agnostic — it works with Claude Code, Codex, or any other coding agent you already have installed.

It is not trying to replace your agent. It is trying to make unattended runs actually unattended: spec-first, verified, reviewable.

**Primary repo (star here):** [RalphWorkflow/Ralph-Workflow on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)  
GitHub mirror: [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)

## When Unattended Runs Are Worth It

- Batching a set of well-scoped backlog items overnight
- Running verification suites on a branch while you sleep
- Large refactors where you want to see the diff in the morning
- Spike investigations that would otherwise consume a whole afternoon

## When Not to Run Unattended

- You do not have a clear spec for the task
- The task touches auth, payments, or other high-trust surfaces without a test harness
- You are the only reviewer and you need to watch the agent work to trust it

---

*This page is maintained by the [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) open-source project.*
