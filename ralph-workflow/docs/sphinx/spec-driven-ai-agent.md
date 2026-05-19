# Spec-Driven AI Agent: Why the Spec Matters More Than the Prompt

Ralph Workflow is a **free and open-source** spec-driven AI agent workflow for developers who want results they can actually review instead of transcripts they have to decode.

If an agent keeps saying it is done before the work actually holds up, the problem is often not raw model capability. The problem is the absence of a real spec.

## What “spec-driven” actually means

A spec-driven AI agent does not start from vague intent alone. It starts from a written task that makes four things explicit:

- what should change
- what should stay unchanged
- what done looks like
- what checks prove the work holds up

That is the difference between hoping the agent converges and giving the run a finish line.

## Why specs beat prompts on substantial work

Prompts tell the agent what to do.
Specs tell the agent what success looks like.

That matters most when the task is too big to babysit and too risky to trust blindly. Without a spec, “done” becomes the agent's opinion. With a spec, “done” becomes something you can verify.

## The workflow Ralph Workflow is built for

[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is built around a spec-first loop:

1. write the task in `PROMPT.md`
2. run planning, implementation, verification, and review
3. come back to a real diff, checks, artifacts, and open questions
4. decide whether you would merge it

That is what makes it different from a normal AI coding chat. The point is not to produce a plausible answer. The point is to produce a reviewable result.

## Who this is for

Ralph Workflow is for developers and technical teams who already use coding agents on their own machine and want a better way to hand off meaningful work overnight.

Good fit:

- bounded features
- refactors with clear acceptance criteria
- test expansion
- cleanup work with obvious verification

Bad fit:

- vague exploration
- risky production surgery with no harness
- tasks where nobody agrees what success looks like

## Best first evaluation path

1. Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Use [Getting Started](getting-started.md) to run one real task
3. Use [Choose Your First Ralph Workflow Task](first-task-guide.md) and [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md) to judge the result honestly

If you already track projects on GitHub, the mirror is here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Why try it now

Because Ralph Workflow is free and open source, runs with the agents you already use on your own machine, and lets you test a spec-driven overnight workflow on one real backlog task tonight.

Start with Codeberg first:

- **Primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
