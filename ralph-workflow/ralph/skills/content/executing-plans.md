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

## Working rules

Treat the plan as the execution contract, not as a source of unstated requirements. Re-read the current step before changing files, and keep its `Depends on` relationship intact. Do not start a dependent step until its prerequisite has produced the declared evidence. When a step names a discovery location, inspect that location first and use the observed behavior to guide only the change already committed by the plan.

Keep the diff small. Reuse the repository's existing helpers, types, and test seams rather than adding parallel machinery. Update the nearest relevant test when the step changes behavior, and use the command specified by the plan before widening verification. A passing focused command proves the step; the final repository gate proves integration. Do not claim either result until the command has actually finished successfully.
