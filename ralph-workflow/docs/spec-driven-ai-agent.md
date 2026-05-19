# Spec-Driven AI Agent: Why the Spec Matters More Than the Prompt

Ralph Workflow is a **free and open-source** spec-driven AI agent workflow for developers who want results they can actually review instead of transcripts they have to decode.

If an agent keeps saying it is done before the work actually holds up, the problem is often not raw model capability. The problem is the absence of a real spec.

## What spec-driven actually means

A spec-driven run makes four things explicit before the work starts:

- what should change
- what should stay unchanged
- what done looks like
- what checks prove the work holds up

That is the difference between hoping the run converges and giving it a real finish line.

## Where Ralph Workflow fits

Ralph Workflow is built around a spec-first loop:

1. write the task in `PROMPT.md`
2. run planning, implementation, verification, and review
3. come back to a real diff, checks, artifacts, and open questions
4. decide whether you would merge it

## Best first evaluation path

1. Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Use [Try Ralph Workflow on one real backlog task](../START_HERE.md)
3. Use [What Good Ralph Workflow Output Looks Like](reviewable-output.md)
4. Turn that result into one public next step with [After Your First Ralph Workflow Run](after-your-first-run.md)

If you already track projects on GitHub, the mirror is here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Best next step on Codeberg

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if the workflow earns trust:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or spec/proof gaps on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Need the fuller hosted-docs version?

- [Hosted-docs version](sphinx/spec-driven-ai-agent.md)
- [Choose Your First Ralph Workflow Task](sphinx/first-task-guide.md)
- [How to Review AI Coding Output Before You Merge](sphinx/review-ai-coding-output-before-merge.md)
