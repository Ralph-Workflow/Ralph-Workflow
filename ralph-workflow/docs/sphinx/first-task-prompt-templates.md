# First-Task Prompt Templates

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow returns a **reviewable result** in your repo — diff, checks, artifacts — instead of a transcript and a claim that the task is done.

Why use it now? Copy one of these templates into `PROMPT.md`, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## How to use these templates

1. Pick the template closest to your real task.
2. Replace the placeholders with your repo-specific details.
3. Keep the scope narrow enough to review in one sitting.
4. Run Ralph Workflow and ask one question in the morning: **would I merge this?**

If you are still not sure whether your task is a good fit, read [Choose Your First Ralph Workflow Task](first-task-guide.md) first.

## Template 1: Small feature slice

Use this when you want to add one focused behavior to an existing app.

```markdown
# Goal

Add <feature> to <surface>. Keep the rest of the flow unchanged.

## Acceptance criteria

- <user action> now produces <expected result>
- Existing behavior for <adjacent flow> stays unchanged
- Tests cover the new behavior
- Documentation or help text is updated if user-visible behavior changed
```

Example fit:

- add a `/health` endpoint
- add empty-state UI for a list page
- add a CLI flag with clear output

## Template 2: Validation or guardrail

Use this when the first task is preventing bad input or unsafe behavior.

```markdown
# Goal

Reject or block <invalid input / unsafe action> before <bad outcome> happens.
Keep the normal success path unchanged.

## Acceptance criteria

- <invalid input> fails with a clear error or message
- <bad side effect> does not happen for invalid input
- Existing valid behavior stays unchanged
- Tests cover the new validation or guardrail
```

Example fit:

- reject empty project names
- block invalid config values
- stop destructive commands unless a flag is present

## Template 3: Bounded refactor with tests

Use this when the code already works but needs cleanup you can verify.

```markdown
# Goal

Refactor <module / component / command> to improve <maintainability / duplication / structure>
without changing external behavior.

## Acceptance criteria

- Behavior stays the same for existing supported inputs
- The targeted duplication or structural problem is reduced
- Existing tests still pass
- New or updated tests cover the area if needed to lock behavior in place
```

Example fit:

- extract repeated parsing logic
- split a large function into smaller helpers
- simplify conditionals in one module

## Template 4: Test coverage pass

Use this when you want Ralph Workflow to strengthen confidence around code that already exists.

```markdown
# Goal

Add or improve automated tests for <feature / module / workflow>.
Do not change production behavior unless a small testability fix is required.

## Acceptance criteria

- Tests cover the key success path for <feature>
- Tests cover at least one important failure or edge case
- Production changes stay minimal and scoped to testability if needed
- The relevant test command passes
```

Example fit:

- add tests around a CLI command
- cover a serialization edge case
- add regression coverage for a fixed bug

## Template 5: Docs or onboarding improvement

Use this when the first task is making the project easier for humans to understand or use.

```markdown
# Goal

Improve <doc / README / onboarding page> so a new user can complete <specific outcome>
without guessing.

## Acceptance criteria

- The doc clearly explains <specific concept or setup path>
- Steps are ordered and runnable
- Ambiguous wording or missing prerequisites are removed
- The updated doc matches current behavior in the codebase
```

Example fit:

- improve install docs
- add a troubleshooting section
- clarify configuration steps for first-time users

## Keep the first run honest

A strong first Ralph Workflow task is:

- real enough to matter
- narrow enough to review quickly
- easy to verify with checks
- cheap to roll back if it misses

A weak first task is:

- vague product exploration
- risky production surgery
- a broad migration with no clear stopping point
- anything where nobody agrees what `done` means

## Next step

- Continue with [Getting Started](getting-started.md) for the fastest first-run path
- Read [What Good Ralph Workflow Output Looks Like](reviewable-output.md) to see what good output should look like
- Read [Choose Your First Ralph Workflow Task](first-task-guide.md) if you still need a task fit check
