---
name: executing-plans
description: Use when executing a written implementation plan in a separate session
---

# Executing Plans

Load the plan and execute every stable step in dependency order. A valid plan gives each step its purpose, typed targets or discovery location, dependencies, and completion proof; do not invent missing details or silently replace the plan with what the repository happens to contain.

For each `[S-n]`:

1. Read its target files or evidence and the relevant request constraints.
2. Check dependencies first; do not start a step whose required prior evidence is absent.
3. Make only the described change, reusing existing project patterns before adding code.
4. Run the step's `Verify` command or inspect its declared location.
5. Record concrete command output, file locations, and changed paths for the matching plan-proof ID.
6. Stop on a criteria/evidence conflict, a broken test, or a requirement that cannot be evaluated; do not weaken checks, special-case code, or redefine the criterion to fit the worktree.

If proof reveals a real blocker, report it with the affected step and repository evidence. Finish by running the repository gate and submitting the required development-result proof for every plan item.

## Execution discipline

Treat the plan as the execution contract, not as a source of unstated requirements. Re-read the current step before changing files, and keep its `Depends on` relationship intact. Do not start a dependent step until its prerequisite has produced the declared evidence. When a step names a discovery location, inspect that location first and use the observed behavior to guide only the change already committed by the plan.

Keep work units disjoint when the plan declares them. An isolated worker proves only its assigned work-unit ID; the main integration session proves every unit and any fan-in step. Never have two workers edit the same file without explicit ownership and ordering. Read every dependency before moving to its consumer; a later step may rely on a new typed field, a changed return value, or a durable record produced earlier.

Keep the diff small. Reuse the repository's existing helpers, types, and test seams rather than adding parallel machinery. Prefer the existing helper or standard library over a new abstraction. Delete superseded paths instead of leaving compatibility scaffolding that no caller needs.

Use the narrowest useful check early, but discover the project gate rather than assuming its command. The focused check is feedback, not completion proof. Before a completed result, run the full project gate unless the plan documents why that proof cannot be performed; then submit an honest partial result with the remaining work and continuation context. When a check fails, read the failure, identify the root cause, repair it, and rerun the relevant check. Do not weaken tests, validation, lint, types, or time budgets to make a plan look complete. Do not claim completion until the gate has actually finished successfully.

Preserve user data and compatibility boundaries. Existing public behavior, checkpoint formats, and configuration defaults need explicit migration behavior when the plan changes them. Before declaring a step complete, ensure its evidence names the actual files and command outcome. A development result is evidence, not a narrative: list each changed file, use the stable plan IDs exactly, and cite prior analysis finding IDs only when their localized evidence has been closed. If the run cannot complete, submit a partial result with the remaining work and a clear continuation point rather than claiming unverified success. Finish by submitting the required development-result artifact and calling the completion sentinel; a clean process exit or a prose summary does not replace either durable record.
