# Bounded Autonomy for Unattended Coding

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already run **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is not raw autonomy. Ralph Workflow is built to make unattended runs end in a **reviewable result**: a real diff, checks that ran, artifacts, and clear open questions.

Why use it now? Because you can try it tonight on one real backlog task, keep the run bounded, and decide tomorrow whether the result actually earned a merge.

## The real goal is not maximum autonomy

The useful question is not:

> How long can I let the agent run?

It is:

> Can I keep this run cheap to fail, easy to inspect, and boring to review tomorrow morning?

A run is healthy when it can stop cleanly and hand back something you can judge quickly.

## What a bounded unattended run needs

### 1. One bounded task

Good unattended runs start with a task that has a clear stopping point.

Bad example:
- "Improve the onboarding experience"

Better example:
- "Reject empty project names before file creation and add tests for the validation path"

If the task cannot fit in a short `PROMPT.md`, it is usually too open-ended for a first unattended pass.

### 2. Explicit proof of success

Before the run starts, you should already know what proves it worked:

- which files should probably change
- which checks should run
- what behavior must stay unchanged
- what still requires human judgment

If success is vague, the run will drift.

### 3. A fail-closed finish line

Unattended automation should fail closed.

That means the run should stop in a way that is obviously incomplete when:

- required checks did not run
- the task expanded beyond the brief
- the agent hit an unresolved decision
- the result still needs active human steering

A confident summary is not a finish line.
A readable diff plus checks plus open questions is.

### 4. Cheap rollback

Your first unattended tasks should be cheap to undo.

Good first-run categories:
- bounded feature slices
- validation work
- tests
- docs
- contained refactors with clear coverage

Avoid risky production surgery, vague exploration, or work where missing the target would be expensive.

### 5. A morning-after review surface

The whole point of autonomy is getting back to a reviewable state without transcript archaeology.

You want to reopen the repo and immediately see:
- what changed
- what passed
- what still needs judgment
- whether you would merge it

If the handoff does not make those four answers obvious, the run was not bounded enough.

## A simple bounded-autonomy filter

Before you launch an unattended run, ask:

1. Can I describe the task in one paragraph?
2. Can I name the checks that should pass?
3. Is rollback cheap if the run misses?
4. Would tomorrow's review mostly be a diff-and-checks decision rather than a fresh design session?

If the answer is no to any of those, tighten the task before you automate it.

## Where Ralph Workflow fits

Ralph Workflow exists for exactly this middle ground:

- bigger than a tiny interactive prompt
- smaller than a full project with no boundaries
- serious enough to hand off overnight
- constrained enough to review honestly in the morning

That is why the primary repo lives on Codeberg:
- inspect it on **Codeberg** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- use the synced **GitHub mirror** second: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Next steps

- Start with [../START_HERE.md](../START_HERE.md)
- Use [when-unattended-coding-fits.md](./when-unattended-coding-fits.md) if you are still choosing the first task
- Use [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md) if the merge decision still feels fuzzy
- Inspect [free-open-source-proof.md](./free-open-source-proof.md) if you want to see a concrete review path before your own run
