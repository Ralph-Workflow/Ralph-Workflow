# Choose Your First Ralph Workflow Task

Ralph Workflow is **the operating system for autonomous coding** — a free and open-source workflow for developers who want work that is **too big to babysit** but **too risky to trust blindly**.

If you are evaluating it, do not start with a vague demo. Start with **one real backlog task** you would actually want merged.

**Primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>  
**GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The fastest honest first run

1. Pick one meaningful task you can still judge tomorrow morning.
2. Write it as a one-paragraph spec.
3. Run Ralph Workflow tonight.
4. Open the diff and the checks tomorrow.
5. Ask: **would I merge this?**

That is the whole evaluation.

## What makes a good first task

A strong first task is:

- small enough to review in one sitting
- real enough that you already care about shipping it
- bounded enough that rollback is cheap
- clear enough that success is easy to define
- testable enough that the checks can prove something

Good first tasks usually look like:

- a bounded validation rule
- a small feature slice with tests
- a narrow refactor with existing invariants
- a bug fix with a clear reproduction path
- a documentation or test pass with an obvious finish line

## What you can ship tonight

If you want the lowest-friction first run, start with one of these exact shapes:

- **Validation rule:** reject empty or whitespace-only names in one CLI or form flow
- **Feature slice:** add one filter, one export, or one settings toggle with tests
- **Isolated refactor:** replace one duplicated helper path with a shared utility and keep behavior stable
- **Test coverage pass:** add missing tests around behavior you already rely on
- **Documentation catch-up:** update one underexplained feature that already exists

If none of those feel easy to judge tomorrow morning, the task is still too broad.

## What makes a bad first task

Avoid first runs that are:

- vague exploration
- risky production surgery
- a broad migration with no clear stopping point
- work that needs constant mid-run human steering
- anything where nobody agrees what success looks like

These fail as first runs because the diff gets too large, the finish line gets fuzzy, and the review becomes harder than the coding.

If you want the sharper pass/fail version with examples, read [Good Unattended AI Coding Task vs Bad One](./good-unattended-ai-coding-task.md).

## Write the task like a one-paragraph spec

Use this shape:

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, or other verification]
```

Example:

```md
Change:
Reject empty or whitespace-only project names in the CLI create flow.

Keep unchanged:
Do not alter the rest of the creation flow or the generated file layout.

Done means:
Invalid names show a clear error and create no project.

Checks:
Existing create-flow tests still pass and new validation tests pass.
```

If you want more starter shapes, use [First-Task Prompt Templates](./first-task-prompt-templates.md).
If you want the reasoning behind this structure, read [Spec-Driven AI Agent](./spec-driven-ai-agent.md).

## How to judge the morning-after result

Do not ask whether the tool looked smart.

Ask:

- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- what still needs a human judgment call?
- **would I merge this?**

A good first run should come back with **finished code**, **tested code**, and a result that is **ready to review**.

## Best next step if it works

If the first run feels real, do the public next step on **Codeberg**:

- inspect the primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- star or watch it there
- open an issue for any first-run friction or missing docs

Use GitHub only as the mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Related pages

- Short first-run path: [START_HERE.md](../START_HERE.md)
- Prompt shaping help: [First-Task Prompt Templates](./first-task-prompt-templates.md)
- What good output looks like: [Reviewable Output](./reviewable-output.md)
- Public proof asset: [Example Review Bundle](./example-review-bundle.md)
