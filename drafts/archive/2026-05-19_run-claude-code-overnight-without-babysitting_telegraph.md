# Run Claude Code Overnight Without Babysitting

If you are searching for how to run Claude Code overnight without babysitting, the real problem is not just keeping a terminal open longer.

The real question is simpler:

**Can you come back to code you can actually review and decide to merge?**

Ralph Workflow is a free and open-source orchestration CLI that runs the coding agents you already use on your own machine.

It is for developers and technical teams with work that is too big to babysit and too risky to trust blindly.

What makes it different is the finish state: Ralph Workflow leaves you with a reviewable result — a real diff, checks that ran, readable artifacts, and a clean morning-after merge question.

Why use it now? Pick one real backlog task tonight, run it, and judge the result tomorrow instead of hovering over an unattended session and hoping it stayed on track.

## What “without babysitting” should actually mean

For overnight Claude Code work, “without babysitting” should not mean:

- letting a long chat run and hoping it stayed coherent
- waking up to a transcript instead of a diff
- seeing a done claim without a proof path
- spending the morning reconstructing what changed

It should mean:

- one bounded task
- one unattended run on your own machine
- checks that actually ran
- a result you can review in normal engineering terms

That is the gap Ralph Workflow is meant to close.

## What Ralph Workflow adds on top of Claude Code

Ralph Workflow does not replace Claude Code. It wraps the agent you already use in a repo-native overnight workflow so the finish state is easier to trust:

- what changed
- what checks ran
- what still needs human judgment
- whether you would merge it

That is more useful than just making the session longer.

## What a trustworthy overnight handoff looks like

Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- unit tests for create flow
- lint / formatting checks if applicable

Open questions:
- should reserved names be rejected too?
- should whitespace be trimmed before validation?

That is the real standard: **proof of completion, not just a confident done claim**.

If you want to inspect that finish state before installing anything, open the public example review bundle on the Ralph Workflow repo.

## Best next step if Claude Code is already your default

Use Codeberg as the main public home:

- Inspect the primary repo on Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- Star / watch / fork on Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- Report first-run friction on Codeberg: https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new
- GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow

Keeping the trust signals on Codeberg makes the primary repo a clearer evaluation surface.

## Fastest honest first run

1. Keep Claude Code already installed and authenticated on your own machine
2. Pick one real backlog task with clear acceptance criteria
3. Run Ralph Workflow overnight
4. Review the diff, checks, and artifacts in the morning
5. Ask: **would you merge it?**

One task tonight. One honest merge decision tomorrow.
