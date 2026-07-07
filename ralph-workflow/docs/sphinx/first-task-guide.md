# Your First Ralph Workflow Task

This page helps you pick a first task that is well-specified enough for an unattended run to be worth the trust you place in it.
 Its composable loop — planning → development → verification — drives each phase into the next, and it runs the coding agents you already use (Claude Code, Codex, OpenCode) on your own machine.

This guide helps you pick the right first task, set it up, and judge the outcome honestly. It is not a feature tour — it is a minimal path to your first verdict: *does this produce code I would actually merge?*

If you want the shortest possible path, read [START_HERE.md](../../../START_HERE.md). This page is the core conversion guide: task selection, spec writing, execution, and morning-after review in one place.

> **See it in action first.** Read [Getting started](./getting-started.md) (init → diagnose → dry-run).

## Step 1: Pick the right first task

The most common mistake is starting with a task that is too ambitious or too vague. Either kills trust in the tool, even when the tool would have worked.

A good first task fits four criteria:

1. **It has a clear boundary.** You should be able to describe what "done" means in one sentence. If you need a paragraph to define the boundary, the task is too big.

2. **It has a clear correctness check.** Something concrete must tell you whether the result is right — tests that pass, a script that runs end to end, a diff shape you can recognize at a glance.

3. **It is real but not critical.** Pick a backlog item you would actually merge, but not one where a mistake breaks production. The goal is to build trust in the workflow, not to gamble the deployment.

4. **It is 2-6 hours of work.** Micro-tasks (15 minutes) do not exercise the loop. Multi-day monsters do not let you build a tight feedback cycle. A good first task fits in an evening while you sleep.

### Good candidates

| Type | Example | Why it works |
|---|---|---|
| Refactor | Split an 800-line module into layers | Clear boundary, tests validate behavior |
| Migration | Move from config files to a database-backed config | Mechanical work, well-scoped |
| Test coverage | Add unit tests for an untested service class | Verifiable, low risk |
| Documentation | Generate API docs from docstrings + examples | Helpful, hard to break |
| Feature slice | Add pagination to an existing list endpoint | Bounded scope, testable |

### Bad candidates

| Type | Why it fails |
|---|---|
| "Improve performance" | Too vague — no clear correctness check |
| "Add feature X" where X requires design decisions | The agent will guess instead of asking |
| Tasks needing access to SaaS dashboards | The agent cannot log into your Stripe console |
| "Explore and suggest" assignments | No concrete deliverable to judge |
| "Rewrite the entire frontend" | Scope explosion; no tight feedback cycle |

The sweet spot: a task where the path is clear but the work is tedious enough that you would prefer not to do it yourself.

> **Action:** Open your backlog right now. Find one item that fits all four criteria.

## Step 2: Write the spec in five minutes

Ralph Workflow uses `PROMPT.md` in your project root as the task contract. This file is the single most important thing you will write. A weak spec produces a weak run. A tight spec produces a run you can actually review.

The spec is not a prompt. A prompt says "please do this." A spec defines what done means.

### The spec template

```markdown
## Task
[One sentence describing what to do.]

## Scope
- [Concrete boundary 1]
- [Concrete boundary 2]
- [Concrete boundary 3]

## What must not change
- [Behavior that must be preserved]
- [Tests that must still pass]
- [Public interfaces that must stay stable]

## Verification
- [ ] [Test or check 1]
- [ ] [Test or check 2]
- [ ] [Test or check 3]
```

Here is a real example. The task is to replace raw SQL queries in a data-access layer with an ORM while keeping all existing tests passing.

```markdown
## Task
Migrate all raw SQL queries in `dal/report_store.py` to SQLAlchemy ORM, using the existing model definitions in `models/report.py`.

## Scope
- Replace raw `execute()` calls with SQLAlchemy session queries
- Keep the same method signatures on `ReportStore`
- Add session-scoped transaction handling

## What must not change
- Every test in `tests/unit/test_report_store.py` must pass without modification
- The `ReportStore` public API surface stays identical
- Return types (list of dicts) stay the same even though the backend changes

## Verification
- [ ] `pytest tests/unit/test_report_store.py -v` passes
- [ ] `pytest tests/integration/ -v` passes
- [ ] `black --check dal/` passes (no formatting drift)
- [ ] `mypy dal/` passes (type coverage holds)
```

That took about four minutes to write. The key elements: scope boundaries, invariants, and concrete verification steps.

### The "what must not change" section is mandatory

This is where most prompts go wrong. They describe what to do but not what to protect. The invariant section makes the output reviewable — you can scan the diff and check whether the protected things stayed intact.

> **Action:** Write your spec using the template above. Take five minutes. Keep it under 25 lines. If it is longer, the task is either too large or your boundaries are fuzzy. Save it as `PROMPT.md` in your project root.

Need more examples? See the [first-task prompt templates](./first-task-prompt-templates.md) for ready-to-copy templates covering refactors, migrations, test expansions, and more.

## Step 3: Start the run and walk away

Install: see [README.md](../../README.md#start-your-first-run) for the canonical install + first-run walkthrough. The `pipx install ralph-workflow` and `ralph` recipe with expected output lives in the root README only.

That is it. Ralph Workflow takes over from here:

1. **Planning pass.** The agent reads your `PROMPT.md`, your repo structure, and your project conventions. It produces a plan. If the plan is weak — vague steps, missing boundaries — the workflow loops it back rather than charging into implementation.

2. **Implementation.** The agent writes code against the plan. Your filesystem carries the state forward. No long chat session accumulating fog.

3. **Verification.** Your spec's verification steps run automatically. If tests fail, the workflow retries with the failure context. If they pass, the handoff is clean.

4. **Handoff.** You get back commits, logs, and a diff you can read in a few minutes.

The whole thing runs on your machine, uses the agents you already have installed (Claude Code, Codex, whatever is on your `PATH`), and does not phone home. You can [inspect every line on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — it is AGPL-3.0, vendor-neutral, and local-first.

## Step 4: The morning-after review

This is the part that matters. Open your laptop, check the diff, and ask yourself three questions:

**1. Would I merge this?**

Not "would I merge this after fixing a few things." Would you merge it as-is? If yes, the workflow did its job. If no, ask the next question.

**2. What specifically would I need to change?**

The answer should be a short list. "The error handling in the new module swallows exceptions silently" is useful feedback. "It did not feel right" is not.

**3. Could I have written a better spec to prevent this?**

Most bad results are spec failures, not code-generation failures. If the agent did something you did not want, ask whether your `PROMPT.md` actually said not to do it. The invariant section should have caught it. If it did not, add it for next time.

This loop — run, review, tighten the spec — is the engine that makes unattended coding work. Each run teaches you something about what makes a good spec. After three or four runs, you will develop a sense for what "done" means in a way the agent can actually verify.

## Step 5: What to do next

After your first successful run, you have options:

**Run it again tonight.** Pick the next backlog item. Same workflow, less anxiety.

**Tune your verification steps.** Your spec's verification section is the highest-leverage part. Spend time on it. Good verification gates make the difference between "I trust this" and "I need to read every line."

**Try a larger task.** After a few successful runs, you can scope tasks that take 4-8 hours. The key is keeping boundaries tight even as the work gets larger.

**Use two agents together.** Ralph Workflow can run Claude Code for planning and Codex for implementation, or vice versa. Sometimes one model is stronger at design and the other at execution. The workflow handles the handoff.

**Configure checkpoints.** For long tasks, Ralph Workflow can checkpoint after verification gates pass. If a later step fails, you resume from the last good checkpoint instead of starting over.

The important thing: the first run builds the muscle. After one good outcome, "run it tonight" becomes a real option instead of a hypothetical.

## The honest assessment

Ralph Workflow is not magic. Here is when it works and when it does not.

### When it works

- Tasks with clear boundaries and existing tests
- Mechanical work (refactoring, migration, test expansion)
- Tasks where "correct" is checkable before merge
- Repos with good project conventions the agent can learn from

### When it does not

- Greenfield design where requirements are still forming
- Tasks requiring access to SaaS dashboards, databases you cannot expose, or accounts you would not share
- Work that needs a lot of back-and-forth with humans
- Tasks where the spec itself would take longer than just doing the work

If your task falls in the first list, you are in the right place. If it falls in the second, write a tighter spec or pick a different task.

## The first-task test

The best way to evaluate Ralph Workflow is not to read comparison pages — it is to try it on one real task and judge the result honestly.

This is the same test we recommend across every comparison page:

Pick a task from your backlog. Define it clearly in `PROMPT.md`. Run it. Come back to something you can review. That one experience will tell you more about whether Ralph Workflow fits your workflow than any comparison table can.

---

## Next pages

- **Shortest first run:** [START_HERE.md](../../../START_HERE.md) — minimal setup, one task, go
- **Prompt help:** [first-task-prompt-templates.md](./first-task-prompt-templates.md) — ready-to-copy templates
- **Diagnostics:** [diagnostics.md](./diagnostics.md) — pre-flight checks before you run
- **Agents:** [agents.md](./agents.md) — selection, auth, and invocation
- **Docs:** [Getting started](./getting-started.md) and the [Sphinx manual home](./index.rst)
- **Primary repo:** [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- **GitHub mirror:** [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)

Ralph Workflow is free, open source (AGPL for source / CC0 for outputs), and runs on your machine. No cloud. No vendor lock-in. Just a workflow that ends with something you can review.
