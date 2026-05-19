# AI Agent Orchestration CLI: Practical Evaluation Path

Ralph Workflow is a **free and open-source** AI agent orchestration CLI for developers who want work that is **too big to babysit and too risky to trust blindly** to come back as a reviewable result.

If you are comparing orchestration CLIs, the useful question is not whether a tool can call an agent. It is whether it can hand back something you would actually inspect, test, and maybe merge.

## What a good orchestration CLI should do

- require a written task or spec
- use the agents you already have on your own machine
- move through plan, build, verify, and review instead of stopping at a draft
- preserve artifacts and checkpoints for long runs
- hand back a reviewable diff with checks and notes

## Where Ralph Workflow fits

Ralph Workflow is built for that exact gap: longer coding work that needs a clean morning-after handoff instead of a transcript and a done claim.

Use it when you want:

- spec-first runs instead of prompt-first drift
- phase-gated workflow instead of draft-and-stop
- agent-agnostic routing instead of a one-tool lock-in
- reviewable output instead of a confident summary

## Best first evaluation path

1. Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. Use [Try Ralph Workflow on one real backlog task](../START_HERE.md)
3. Open [What Good Ralph Workflow Output Looks Like](reviewable-output.md)
4. Turn the result into one public next step with [After Your First Ralph Workflow Run](after-your-first-run.md)

If GitHub is where you already track projects, the mirror is here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Best next step on Codeberg

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg if the workflow earns trust:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Need the fuller hosted-docs version?

- [Hosted-docs version](sphinx/ai-agent-orchestration-cli.md)
- [Getting Started](sphinx/getting-started.md)
- [How to Review AI Coding Output Before You Merge](sphinx/review-ai-coding-output-before-merge.md)
