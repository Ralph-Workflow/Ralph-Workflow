# Start Here: Try Ralph Workflow on One Real Task

If you are evaluating Ralph Workflow, start on **Codeberg** and do not start with a vague demo.

Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow  
GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow

Start with **one real backlog task** you already care about.

## Pick the right first task

Choose something that is:
- small enough to judge in one sitting
- real enough to matter
- bounded enough that rollback is cheap
- clear enough that success is easy to define

Good first tasks:
- a small feature slice
- a bounded refactor with tests
- a backlog item with obvious acceptance criteria
- repetitive implementation work with clear verification

Bad first tasks:
- a vague product idea
- risky production surgery
- mixed multi-part work
- anything where no one agrees what “done” means

## Write the task like a one-paragraph spec

Before the run starts, write down:
- what needs to change
- what should stay untouched
- what done looks like
- what checks matter

If you want a sharper mental model first, read **[Spec-Driven AI Agent](./content/guides/spec_driven_ai_agent.md)**.

If you are still unsure whether your first task is shaped correctly, use **[Good Unattended Task vs Bad One](./content/guides/good_unattended_task.md)** before you run it.

## What a good result should include

A useful Ralph Workflow run should hand back:
- a scoped result
- a real diff
- changed files you can inspect
- checks that actually ran
- a reasoning trail
- open questions called out clearly

## How to judge the result honestly

Do not ask whether the tool looked smart.

Ask:
- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- **would I merge this?**

That is the whole evaluation.

## If the first run is promising

Use the public next step on **Codeberg**:
- star the repo if you want to track it
- watch it if you want updates
- open an issue if your first run exposed friction or a missing doc

That turns a private evaluation into a useful public signal or actionable feedback.

## Next examples

See:
- [content/guides/good_unattended_task.md](./content/guides/good_unattended_task.md)
- [content/guides/spec_driven_ai_agent.md](./content/guides/spec_driven_ai_agent.md)
- [content/examples/first_task_example.md](./content/examples/first_task_example.md)
- [content/examples/review_bundle_example.md](./content/examples/review_bundle_example.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
