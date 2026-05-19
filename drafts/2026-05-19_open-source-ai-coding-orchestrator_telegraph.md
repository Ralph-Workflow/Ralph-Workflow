# Open-Source AI Coding Orchestrator: What Ralph Workflow Is Actually For

If you are searching for an **open-source AI coding orchestrator**, the real question is not just whether it can call multiple agents.

The real question is: **can it take one real backlog task, run on your own machine, and bring back something you would actually review and maybe merge?**

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the finish line: Ralph Workflow is built to hand back a **reviewable result** — a diff, checks, artifacts, and enough context to decide whether the work actually holds up.

Why use it now? Because you can inspect the source on **Codeberg**, install it for free, run one real task tonight, and judge the result tomorrow with one honest question: **would I merge this?**

## What an AI coding orchestrator should actually do

A useful open-source AI coding orchestrator should help you:

- keep the work inside your repo and normal tooling
- use the agents you already trust instead of forcing a new hosted workflow
- run a meaningful task unattended without constant prompting
- leave behind proof you can review in the morning
- make the next human decision obvious

That is the gap Ralph Workflow is trying to close.

## What Ralph Workflow adds beyond a normal agent session

Running Claude Code, Codex CLI, or OpenCode directly can still leave you with:

- a long transcript instead of a clean handoff
- a claim that tests passed without an easy review path
- unclear morning-after re-entry
- too much manual glue between planning, implementation, and review

Ralph Workflow wraps those tools in one repo-native flow so the outcome is easier to judge in normal engineering terms:

- what changed
- what checks ran
- what still needs human judgment
- whether you would merge it

## What a good first result looks like

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

That is the real promise: **proof of completion, not just a done claim**.

If you want to inspect that shape before you install, open the public example review bundle in the Ralph Workflow docs.

## Best next step if this is what you want

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
- **Star / watch / fork on Codeberg:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
- **Report first-run friction on Codeberg:** https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new
- **Use GitHub only as the mirror:** https://github.com/Ralph-Workflow/Ralph-Workflow

Keeping adoption and feedback on Codeberg makes the primary repo a clearer trust surface.

## Fastest honest first run

1. Pick one real backlog task
2. Write a short `PROMPT.md` with acceptance criteria
3. Run Ralph Workflow overnight on your own machine
4. Review the diff, checks, and artifacts in the morning
5. Ask: **would I merge this?**

If you want the shortest path, start with `START_HERE.md` in the primary repo.
If you want the best task filter first, read `when-unattended-coding-fits.md`.
If you want the clearest proof before setup, read `free-open-source-proof.md` and `review-ai-coding-output-before-merge.md`.