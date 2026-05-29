---
title: Claude Code Unattended — What Actually Survives the Morning
date: 2026-05-20
type: technical
keyword: claude code unattended
cta: install_ralphworkflow
---

# Claude Code Unattended — What Actually Survives the Morning

The promise of running Claude Code unattended is compelling: start a session, define the task, come back in the morning to finished code. The reality is more nuanced. Most unattended runs produce results that need significant reconstruction before they're useful.

This is not a model problem. It is a finish-line problem.

## What Breaks First in Unattended Claude Code Runs

The failure modes are consistent:

**Scope creep without a stop condition.** Claude keeps making progress visible. It is not lying — it genuinely believes it is done. But "done" in a Claude session is not the same as "done" in a codebase.

**No automated verification.** If tests are not explicitly wired into the run, there is no proof the change holds. You come back to code that looks right but may not work.

**No clean handoff artifact.** The transcript is long, the diff is unclear, and there is no short receipt naming what changed and what still needs a human decision.

**Stale assumptions compound.** If the task runs overnight and the codebase changes in the meantime, the morning result may be based on assumptions that no longer hold.

## The Structural Fix

The pattern that actually works:

1. **Define the finish line before starting.** "Implement X with these constraints and these acceptance criteria" beats "improve the codebase."

2. **Attach a verification bundle to the run.** Tests, lint, type checks — whatever is relevant. These become the run's receipt.

3. **End with a short written handoff.** One paragraph: what changed, what ran, what failed, what still needs your call.

4. **Treat the diff as the artifact, not the transcript.** If the diff is not small enough to read in five minutes, the task was too large.

## Ralph Workflow's Role

Ralph Workflow is a composable loop framework that runs Claude Code (and Codex, OpenCode) through this exact structure. It is a CLI that:

- runs each phase against a spec you define upfront
- executes the verification bundle automatically  
- loops on failures instead of propagating them
- produces a clean morning-after receipt: diff, checks, and open decisions named explicitly

The goal is not to make Claude Code smarter. It is to make unattended runs end in something you can actually review.

## What You Can Do Today

If you are already running Claude Code unattended:

- Add a test bundle to every run, even if it is just `npm test` or `pytest`
- Define done as "diff is readable in 5 minutes" not "Claude says done"
- Write one sentence at the end: what changed, what still looks risky

If you want the structure built in: [try Ralph Workflow on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).
