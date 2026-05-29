---
date: "2026-05-29"
product: "RalphWorkflow"
channel: "writeas"
experiment_id: "2026-05-29-technical"
content_type: "technical"
angle: "How nested analysis loops catch bugs before they commit"
keyword: "unattended ai coding"
cta: "install_ralphworkflow"
hypothesis: "Technical posts should outperform philosophy posts on write.as because they are more concrete and searchable."
---

# How Nested Analysis Loops Catch Bugs Before They Commit

The commit is not where you should catch bugs. The analysis loop is.

Here's the pattern that changes everything: each phase has its own feedback loop, separate from the program loop.

## Two Loops, Not One

Most AI coding workflows are one big loop:
- Write code → Run it → Looks good → Commit

Ralph Workflow separates concerns:

```text
PHASE LOOP (inside each phase)
  build → analyze → revise → analyze → ... → commit

PROGRAM LOOP (between phases)
  plan → [phase loop] → develop → [phase loop] → commit → plan (fresh)
```

The phase loop catches implementation mistakes. The program loop catches direction errors.

## What Analysis Actually Does

Analysis isn't "review the code." It's running the code against the spec, automatically.

```python
# The analysis agent checks:
# 1. Does the diff match the spec item?
# 2. Does it break existing tests?
# 3. Are there obvious bugs?
# 4. Is the code readable?
```

If any check fails, the loop goes back with specific feedback.

## Why This Matters for Unattended Runs

Without this, unattended runs are just unattended bug creation. With it, the loop acts as an automated senior developer review on every commit.

This is the difference between "it ran" and "it's correct."

---

**Try it on Codeberg:** [RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star, fork, and open issues there. The GitHub mirror is at [Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow) if you prefer it.

## Where Ralph Workflow Fits

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator. It keeps the core loop simple, ships with a strong default workflow for writing software, and lets you use that default as-is or build your own workflow on top.
