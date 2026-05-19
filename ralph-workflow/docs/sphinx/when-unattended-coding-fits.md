# When Ralph Workflow Fits — and When It Does Not

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams who want to hand off engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different is not just that it can run overnight. It is built to hand back **reviewable output** — a real diff, checks, artifacts, and enough context to decide whether you would merge the result.

Why use it now? You can try it for free on one real backlog task tonight and decide tomorrow whether the output actually holds up.

## The simple rule

Use Ralph Workflow for work that should leave behind a **reviewable implementation chunk**, not just an interesting transcript.

## Good fits

These are the kinds of tasks Ralph Workflow is good at:

- a bounded feature slice with clear acceptance criteria
- a narrow refactor with tests
- repetitive implementation work where the expected shape is obvious
- a cleanup task with straightforward verification
- a docs or test pass where `done` is easy to check
- a backlog item you want to hand off overnight and review in the morning

Why these fit:

- the scope is clear
- success can be verified
- rollback is cheap if the run misses
- a human reviewer can quickly answer: **would I merge this?**

## Bad fits

These are poor first uses for Ralph Workflow:

- vague product exploration
- risky production surgery
- tasks that depend on frequent mid-run human decisions
- work where the spec is still being invented while coding happens
- broad multi-part projects with no clear stopping point
- tasks where failure would be expensive and hard to unwind

Why these fail:

- the agent has to guess too much
- the handoff is hard to review honestly
- `done` is unclear
- the task needs live steering, not unattended execution

## A good first-task filter

Before you run Ralph Workflow, ask:

1. Can I describe the task in one paragraph?
2. Can I name the checks that prove it worked?
3. Would a diff be enough for a reviewer to judge the result?
4. If it misses, is the rollback cheap?

If the answer is yes to all four, it is probably a good Ralph Workflow task.

## A practical first run

A strong first run looks like this:

- pick one real backlog task
- write the scope and acceptance criteria in `PROMPT.md`
- run Ralph Workflow overnight
- review the diff, checks, and notes in the morning
- decide whether you would merge it

That is the real product test.

## Next steps

- Start with [Getting Started](getting-started.md)
- Read [Choose Your First Ralph Workflow Task](first-task-guide.md)
- See [Example Review Bundle](example-review-bundle.md) for a public sample prompt, handoff notes, and artifacts
- Use [First-Task Prompt Templates](first-task-prompt-templates.md) if you want a copy-paste `PROMPT.md` shape before your first run
