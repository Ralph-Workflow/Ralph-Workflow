# Choose Your First Ralph Workflow Task

Ralph Workflow is **the operating system for autonomous coding**: a **free and open-source composable loop framework and AI orchestrator** that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding session is not just the handoff. Ralph Workflow turns the simple Ralph loop into a **composable workflow** with planning, implementation, verification, and review, and the default version of that workflow is already good for writing software.

Why try it now? Because you can pick one real backlog task tonight, run it with the tools you already trust, and decide tomorrow whether the result is something you would actually merge — with one honest question: **would I merge this?**

## Do not start with a vague demo

The fastest honest test is one real backlog task you already care about.

Choose something that is:

- small enough to judge in one sitting
- clear enough that success is easy to define
- cheap to roll back if the run misses
- real enough that you already want it done

## Good first tasks

These are strong first uses for Ralph Workflow:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup task with obvious verification
- repetitive implementation work where `done` is easy to judge
- a docs or test pass with a clear finish line

Why these work:

- the scope is easy to describe
- the checks are obvious
- the diff is reviewable
- failure is inexpensive to unwind

## Bad first tasks

These are weak first uses for Ralph Workflow:

- vague product exploration
- risky production surgery
- a broad multi-part migration with no clear stopping point
- tasks that depend on frequent mid-run human decisions
- anything where nobody agrees what success looks like

Why these fail:

- the agent has to guess too much
- the result is hard to review honestly
- `done` is unclear
- live steering matters more than unattended execution

## Write the task like a one-paragraph spec

Before the run starts, write down:

- what needs to change
- what should stay untouched
- what `done` looks like
- what checks prove it worked

A good starter spec looks like this:

```markdown
# Goal

Add a /health endpoint that returns HTTP 200 with {"status": "ok"}.

## Acceptance criteria

- GET /health returns HTTP 200
- Response body is valid JSON with status == ok
- A new test covers the endpoint
- Existing routes keep working unchanged
```

## The four-question first-task filter

Before you run, ask:

1. Can I describe the task in one paragraph?
2. Can I name the checks that prove it worked?
3. Would a diff be enough for a reviewer to judge the result?
4. If it misses, is the rollback cheap?

If the answer is yes to all four, it is probably a good Ralph Workflow task.

## How to judge the result honestly

Do not ask whether the agent looked smart.

Ask:

- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- **would I merge this?**

That is the real product test.

## Running `ralph --init` and diagnosing capability health

`ralph --init` installs Ralph Workflow's mirrored default skill bundles by copying the bundled skill files to `~/.claude/plugins/ralph-workflow-skills/skills/`. The shipped set includes the core workflow bundle plus a namespaced design bundle, and the mirrored snapshots ship inside the Python package without requiring a separate skill registry fetch or network access. After installing, it prints a Baseline Capabilities table showing:

- **Always-available built-in capabilities:** workspace_ops, git_read_ops, artifact_ops, plan_read, media_read (these are built into Ralph and cannot degrade)
- **Managed helpers:** web_search, visit_url, docs_mcp, and skills (these are tracked with health state)

`ralph --diagnose` shows a Baseline Capabilities section with the same table, including update availability and last-check timestamps.

If any managed capability shows a non-healthy status, re-running `ralph --init` retries skill installation and re-probes docs_mcp. The capability state is persisted in `~/.config/ralph-workflow-capabilities.json`.

## Next step

- Continue with [Getting Started](sphinx/getting-started.md) for the install and first-run flow
- Read [First-Task Prompt Templates](first-task-prompt-templates.md) if you want copy-paste starter specs instead of drafting from scratch
- Read [What Good Output Looks Like](reviewable-output.md) to see the kind of handoff you should expect
- Read [Example Review Bundle](sphinx/example-review-bundle.md) if you want a public sample `PROMPT.md`, result notes, and artifacts before your first run

If this first-task filter matches how you want to evaluate Ralph Workflow, inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Best next public actions:
- **Inspect / star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
