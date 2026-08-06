---
name: executing-plans
description: Use when executing a written implementation plan in a separate session
---

# Executing Plans

Load the plan and execute every stable step in dependency order. A valid plan gives each step its purpose, typed targets or discovery location, and completion proof; do not invent missing details.

For each `[S-n]`:

1. Read its target files or evidence.
2. Make only the described change.
3. Run the step's `Verify` command or inspect its declared location.
4. Record concrete evidence for the matching plan-proof ID.

If proof reveals a real blocker, report it with the affected step and repository evidence. Finish by running the repository gate and submitting the required development-result proof for every plan item.

## Execution discipline

Treat the plan as the execution contract, not as a source of unstated requirements. Re-read the current step before changing files, and keep its `Depends on` relationship intact. Do not start a dependent step until its prerequisite has produced the declared evidence. When a step names a discovery location, inspect that location first and use the observed behavior to guide only the change already committed by the plan.

Keep ownership explicit when a plan has independent units: one writer owns each file, integration happens once, and shared files are not edited concurrently. Read every dependency before moving to its consumer; a later step may rely on a new typed field, a changed return value, or a durable record produced earlier.

Keep the diff small. Reuse the repository's existing helpers, types, and test seams rather than adding parallel machinery. Prefer the existing helper or standard library over a new abstraction. Delete superseded paths instead of leaving compatibility scaffolding that no caller needs.

Use the narrowest useful verification after each change. Update the nearest relevant test when the step changes behavior, and use the command specified by the plan before widening verification. A focused test is fast feedback, not a substitute for the final gate. When a check fails, read the failure, identify the root cause, repair it, and rerun the relevant check. Do not weaken tests, validation, lint, types, or time budgets to make a plan look complete. A passing focused command proves the step; the final repository gate proves integration. Do not claim either result until the command has actually finished successfully.

Preserve user data and compatibility boundaries. Existing public behavior, checkpoint formats, and configuration defaults need explicit migration behavior when the plan changes them. Before declaring a step complete, ensure its evidence names the actual files and command outcome. The final artifact is a concise proof index, not a status narrative: each stable step ID receives exactly one concrete result. If the run cannot complete, submit a partial result with the remaining work and a clear continuation point rather than claiming unverified success.
