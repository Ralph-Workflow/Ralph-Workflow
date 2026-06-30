<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# When Ralph Workflow Fits — and When It Does Not

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

Ralph Workflow is an **open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams who want to hand off engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different is not just that it can run overnight. It is built to hand back **reviewable output** — a real diff, checks, artifacts, and enough context to decide whether you would merge the result.

Why use it now? You can try it for free on one real backlog task tonight and decide tomorrow whether the output actually holds up.

Before you do that, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

If the first run earns trust, keep the public next step on Codeberg:

- **Star or watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## The simple rule

Use Ralph Workflow for work that should leave behind a **reviewable implementation chunk**, not just an interesting transcript.

## Good fits

These are the kinds of tasks Ralph Workflow is good at:

- a bounded feature slice with clear acceptance criteria
- a narrow refactor with tests
- repetitive implementation work where the expected shape is obvious
- a cleanup task with straightforward verification
- a docs or test pass where "done" is easy to check
- a backlog item you want to hand off overnight and review in the morning

Why these fit:

- the scope is clear
- success can be verified
- rollback is cheap if the run misses
- a human reviewer can quickly answer: **does the implementation hold up?**

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
- "done" is unclear
- the task needs live steering, not unattended execution

If you want concrete examples before you choose a first run, read [Good Unattended AI Coding Task vs Bad One](./good-unattended-ai-coding-task.md).

If you want the fastest copy-paste path from task choice to `PROMPT.md`, pair that with [Choose Your First Ralph Workflow Task](./first-task-guide.md) and [First-Task Prompt Templates](./first-task-prompt-templates.md).

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

If you are choosing fast, start with a **validation / guardrail**, **small feature slice**, or **test coverage pass**. Those three shapes usually produce the clearest morning-after yes/no answer.

That is the real product test.

## Next steps

- Start with [../START_HERE.md](../START_HERE.md)
- Read [Choose Your First Ralph Workflow Task](./first-task-guide.md) for the quickest first-run filter
- Read [Good Unattended AI Coding Task vs Bad One](./good-unattended-ai-coding-task.md) for concrete task shapes before you commit to a first run
- Read [First-Task Prompt Templates](./first-task-prompt-templates.md) if you want copy-paste starter specs
- See [free-open-source-proof.md](./free-open-source-proof.md) for an example first task and review bundle
- Use [quick-reference.md](./quick-reference.md) when you are ready to run it

After one real run, convert the result into exactly one Codeberg action:

- **promising run:** star or watch the repo
- **shaky run:** open the right issue with the missing proof or friction