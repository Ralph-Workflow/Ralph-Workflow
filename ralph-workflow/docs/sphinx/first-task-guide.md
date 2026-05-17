# Choose Your First Ralph Workflow Task

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding session is the handoff: Ralph Workflow is built to return a **reviewable result** in your repo instead of a long transcript and a claim that the task is done.

Why try it now? Because you can pick one real backlog task tonight, run it with the tools you already trust, and decide tomorrow whether the result is something you would actually merge.

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

## Next step

- Continue with [Getting Started](getting-started.md) for the install and first-run flow
- Read [First-Task Prompt Templates](first-task-prompt-templates.md) if you want copy-paste starter specs instead of drafting from scratch
- Read [What Good Output Looks Like](reviewable-output.md) to see the kind of handoff you should expect
