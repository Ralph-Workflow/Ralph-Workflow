# Unattended Coding Agent: What It Is, When It Helps, and Why Ralph Workflow Exists

If you are looking for an **unattended coding agent**, the useful question is not just "which model writes code?"

The real question is: **can I hand off a real backlog task, walk away, and come back to something reviewable?**

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal agent session is the handoff: Ralph Workflow is built to bring back a **reviewable diff, checks, artifacts, and finish notes** instead of just a long transcript and a claim that the work is done.

Why use it now? Because you can try it tonight with the agents you already have, on one real task, for free.

## What an unattended coding agent should actually do

A useful unattended coding agent should help you:

- hand off a bounded engineering task
- keep the work inside your repo and normal tooling
- run long enough to finish a meaningful chunk of work
- leave behind proof you can review in the morning
- make it obvious what still needs human judgment

That is the gap Ralph Workflow is trying to close.

## What usually goes wrong with unattended coding

Most unattended coding breaks trust in predictable ways:

- the task was too vague
- the run touched shared boundaries with no explicit owner
- the agent produced a lot of edits but weak proof
- the morning-after handoff is a transcript, not a finish receipt
- the branch looks fine alone, but the merged state is still unclear

That is why Ralph Workflow focuses less on "let the agent run" and more on **what comes back when it finishes**.

## When Ralph Workflow is a good fit

Ralph Workflow is strongest when you want an unattended coding agent for work like:

- a bounded feature slice
- a narrow refactor with checks
- a test-writing pass
- a docs or cleanup task with clear acceptance criteria
- a real overnight backlog item you can judge with a merge decision

Good first runs share the same shape:

- one real task
- clear acceptance criteria
- cheap rollback if it misses
- a reviewer who can honestly answer: **would I merge this?**

If that is your use case, start with [Choose Your First Ralph Workflow Task](first-task-guide.md) and [First-Task Prompt Templates](first-task-prompt-templates.md).

## When you do not want an unattended coding agent

Ralph Workflow is a weak fit when the task still needs constant human steering, the scope is still being invented, or the rollback would be painful.

For that filter, read [When Ralph Workflow Fits — and When It Does Not](when-unattended-coding-fits.md).

## What Ralph Workflow adds beyond running Claude Code or Codex directly

If you already use Claude Code, Codex, or another agent CLI, Ralph Workflow is not asking you to throw that away.

It adds a repo-native unattended workflow around those tools so you can:

- orchestrate the agent you already trust on your machine
- run planning, implementation, and verification as one flow
- wake up to a result that is easier to inspect
- judge the work on the diff, checks, and finish receipt instead of the agent's self-report

That is the practical difference between "an agent session ran overnight" and "I got back something I can review quickly."

## Fastest honest way to try it

Use one real backlog task tonight.

1. Install Ralph Workflow
2. Pick a task small enough to judge in one sitting
3. Write a short `PROMPT.md` with acceptance criteria
4. Run it overnight
5. Review the diff, checks, and notes in the morning
6. Ask: **would I merge this?**

If you want proof before setup, inspect the public [Example Review Bundle](example-review-bundle.md).

If you want the shortest install path, go to [Start Here](../START_HERE.md).
