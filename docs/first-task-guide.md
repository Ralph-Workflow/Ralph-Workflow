# Choose Your First Ralph Workflow Task

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding session is the handoff: Ralph Workflow returns a **reviewable result** in your repo — diff, checks, artifacts — instead of a long transcript and a confident done claim.

Why try it now? Pick one real backlog task tonight, run it with the tools you already trust, and decide tomorrow whether the result is something you would actually merge.

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

## Skip the blank page: three copy-paste first-task starters

If your real blocker is not task choice but writing the first `PROMPT.md`, start with the closest template instead of improvising from scratch:

- **Validation / guardrail** — [reject bad input before it causes damage](./first-task-prompt-templates.md#template-2-validation-or-guardrail)
- **Small feature slice** — [add one focused behavior without changing the rest](./first-task-prompt-templates.md#template-1-small-feature-slice)
- **Test coverage pass** — [strengthen confidence around code that already exists](./first-task-prompt-templates.md#template-4-test-coverage-pass)

Those three starter shapes cover a large share of honest first runs.

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

- Continue with [../START_HERE.md](../START_HERE.md) for the install and first-run flow
- Read [First-Task Prompt Templates](./first-task-prompt-templates.md) if you want copy-paste starter specs
- Read [What Good Output Looks Like](./free-open-source-proof.md) to see the handoff you should expect
- Read [Example Review Bundle](./example-review-bundle.md) for a public sample before your first run
- After the run, use [After Your First Ralph Workflow Run](./after-your-first-run.md) to turn the result into one public Codeberg action

If this first-task filter matches how you want to evaluate Ralph Workflow, inspect the **primary Codeberg repo**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Best public actions:
- **Star / watch on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
