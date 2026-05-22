# writing-plans

## Purpose
Writing-plans is the skill for turning a user goal into an executor-ready sequence of steps. It matters when work spans multiple files, when verification must be explicit, or when the task needs to be handed off to another agent without extra planning.

A good plan reduces rework by making scope, dependencies, and proof expectations clear before code changes start. It is especially valuable in unattended workflows where the executor must not guess which file owns which behavior.

## When To Use
- A task has two or more steps.
- The change touches several modules or tests.
- The acceptance criteria need explicit verification commands.
- You need to preserve a clear handoff to development or review.

## Key Steps / Approach
1. Summarize the user outcome and the repository context in concrete terms.
2. Break the work into ordered steps with real file targets and rationale.
3. State dependencies and any safe parallel chunks explicitly.
4. Attach exact verification commands and the expected passing result.
5. Keep the plan concise enough that a weaker agent can execute it directly.

## Common Pitfalls
- Writing advice instead of an executable sequence.
- Leaving file ownership or verification ambiguous.
- Making the plan depend on a later replanning cycle to become usable.
