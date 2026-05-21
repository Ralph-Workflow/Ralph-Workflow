---
orphan: true
---

# Good Unattended AI Coding Task vs Bad One

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


If your first Ralph Workflow run is too vague, too risky, or too sprawling, you will learn the wrong lesson.

The best first run is not the most impressive one.

It is the one you can judge honestly the next morning.

**Primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>  
**GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The simple test

A good unattended task is:

- **too big for one chat reply**
- **small enough to review in one sitting**
- **clear enough to verify**
- **safe enough to roll back**

If the task fails any one of those, it is probably a bad first run.

## Good first-task shapes

Good first tasks usually look like:

- add one bounded validation rule
- implement one small feature slice with tests
- refactor one isolated module while keeping behavior stable
- wire one integration point with clear acceptance criteria
- clean up repetitive code where checks can prove nothing broke

Examples:

- reject empty project names in a CLI create flow
- add rate limiting to one API endpoint
- add one export format without changing the rest of the pipeline
- replace one duplicated helper path with a shared utility and update tests

Why these work:

- the scope is visible
- the finish line is obvious
- the diff should stay reviewable
- the checks can actually tell you something

## Bad first-task shapes

Bad first tasks usually look like:

- redesign the whole architecture
- build a new product from a vague idea
- fix "everything wrong with onboarding"
- do risky production surgery with no rollback room
- change many systems at once with no clean test boundary

Examples:

- migrate the entire auth stack
- rewrite the frontend and backend in one run
- improve performance everywhere
- make the product feel more polished
- refactor the codebase for maintainability

Why these fail as first runs:

- there is no sharp finish line
- success is subjective
- the diff explodes
- review gets harder than the coding
- the agent can sound confident without earning trust

## A one-paragraph spec template

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
Do not alter the rest of the creation flow or file layout.

Done means:
Invalid names show a clear error and create no project.

Checks:
Existing create-flow tests still pass and new validation tests pass.
```

## How to judge the morning-after result

Do not ask whether the agent looked smart.

Ask:

- does the diff match the task?
- did the checks really run?
- is the output small enough to review?
- are open questions called out clearly?
- **does the implementation hold up?**

That last question matters most.

## What to do if your first run fails

If the result is messy, do not conclude that unattended coding never works.

First ask whether the task was:

- too broad
- under-specified
- hard to verify
- unsafe for an overnight run

Usually the repair is not "use more hype."

It is:

- pick a narrower task
- sharpen the spec
- keep the checks explicit

## Best next step

If you want to try Ralph Workflow honestly, start on **Codeberg**, choose one backlog task that fits the good-task test above, run it tonight, and judge the result in the morning.

- **Codeberg (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
