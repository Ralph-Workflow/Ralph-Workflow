# When to Use Ralph Workflow

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.

It is for developers and technical teams doing ambitious software work that benefits from a structured workflow instead of a chat session.

## The short answer

Use Ralph Workflow when the task is:
- too big to babysit in one chat
- clear enough to verify
- important enough that you want explicit planning, implementation, verification, and review
- better served by a repeatable workflow than by prompt improvisation

Do **not** reach for it first when the task is:
- a tiny tweak
- a quick interactive edit inside your editor
- vague exploration
- constant back-and-forth steering

## The difference in one table

| If your default tool is... | It is strongest at... | Switch to Ralph Workflow when... |
| --- | --- | --- |
| Aider / Claude Code / Copilot / Cursor / Continue | fast pair-programming, quick edits, interactive iteration | you need the work to hold up tomorrow morning without depending on the same chat context |
| Generic agent orchestration tools | wiring agents and automations together | you want a stronger default software-delivery workflow instead of assembling the whole process from scratch |
| A raw chat session | brainstorming or one-off implementation help | you need a bounded task contract, explicit checks, and a reviewable outcome |

## Best fit signals

Ralph Workflow is a good fit if your real pain sounds like this:
- “The agent said done, but I still cannot trust the result.”
- “The work is too large to babysit step by step.”
- “I want a strong default workflow, not another blank orchestration canvas.”
- “I need the morning-after review to be clean: diff, checks, outcome, open risks.”
- “I want to use the default now and extend it later without throwing it away.”

## Bad fit signals

Ralph Workflow is probably the wrong first tool if your situation sounds like this:
- “I just need to rename a variable.”
- “I want the fastest possible autocomplete loop.”
- “I do not know what done looks like yet.”
- “I want to steer every step live.”
- “There are no meaningful checks or review criteria.”

## Why it is different

Ralph Workflow keeps a simple Ralph-loop core, then composes that core into planning, development, verification, and broader workflow loops with strong defaults.

That means you do not have to choose between:
- a tiny chat loop that breaks down on larger work, or
- a heavyweight orchestration setup that makes you design everything yourself

The core stays simple. The workflow gets stronger.

## Why use it now

Because you can use the default workflow as-is today on one real backlog item, then decide whether to keep that default or build on top of it.

That makes the best first evaluation very concrete:
1. pick one meaningful task
2. write a one-paragraph contract
3. run the workflow
4. judge the result by the diff and checks
5. decide whether you would merge it

## Best next step

- If you already know the task: read [Start here on one real task](../START_HERE.md)
- If you need help picking the task: read [First-task guide](./first-task-guide.md)
- If you want to see the workflow shape first: read [Workflow composition example](../content/examples/workflow_composition_example.md)

If Ralph Workflow matches your use case, follow the project on **Codeberg** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
