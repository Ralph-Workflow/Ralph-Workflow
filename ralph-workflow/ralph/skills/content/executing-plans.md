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
