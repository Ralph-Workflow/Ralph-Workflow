# How to Tell if an AI Coding Task Is Actually Done

A lot of AI coding frustration comes from one simple problem: the tool says it is done before the work actually holds up.

That creates fake speed. You got output quickly, but you still need to figure out whether the result is trustworthy.

The useful question is not whether the agent produced code.

The useful question is whether you got back something you would actually merge.

## What Ralph Workflow is for

Ralph Workflow is a free and open-source tool that orchestrates the coding agents you already use on your own machine.

It is for developers and technical teams with work that is too big to babysit and too risky to trust blindly.

What makes it different is the finish: it is built to leave you with a reviewable result instead of a transcript plus a confident done claim.

Why use it now? Because you can run one real backlog task tonight and judge the morning-after result honestly.

## "Done" needs proof

For an AI coding task, "done" should mean more than:
- the tool stopped talking
- the diff looks busy
- the agent says tests passed
- the summary sounds confident

A task is only really done when you can verify that:
- the requested scope is clear
- the change matches that scope
- the checks actually ran
- the result is small enough to review
- any uncertainty is called out explicitly

## A boring checklist that matters more than prompt cleverness

1. **Clear task boundary**
   - what is changing?
   - what should not change?
   - what counts as success?

2. **Isolated execution**
   - one task
   - one branch or worktree
   - no mixed objectives

3. **Verification before claiming success**
   - tests run
   - failures surfaced
   - weak spots fixed before handoff

4. **Reviewable finish**
   - real diff
   - changed files visible
   - short reasoning trail
   - open questions noted

If one of those is missing, the task is not really done yet.

## Why this matters more for unattended runs

Unattended AI coding only makes sense if you can come back to a clean re-entry point.

If the morning-after result is:
- hard to retrace
- too broad to inspect
- missing checks
- vague about what changed

then the workflow failed even if it generated a lot of code.

The value is not that the tool kept working while you were away.

The value is that you came back to something reviewable.

## Where Ralph Workflow fits

This is the gap Ralph Workflow is built around.

Not just generating output, but pushing the job through:
- sharpen the request
- build and verify in one loop
- land on a result that actually holds up

The goal is simple:

**proof of completion, not just a claim it is done**

## The final test

At the end of the run, ask one question:

**Would you merge this?**

If yes, the task is probably done.

If not, the workflow needs to keep going or needs a clearer handoff.

That question is still the best filter I know.

## If you want to inspect it

Start with the primary Codeberg repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

Mirror only if you need it: https://github.com/Ralph-Workflow/Ralph-Workflow

Best next steps on Codeberg:
- inspect the repo
- star or watch it if the workflow fits
- open an issue if your first run hits friction
