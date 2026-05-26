# subagent-driven-development

## Purpose
Subagent-driven-development is the discipline of using specialized workers to handle coherent chunks of work instead of trying to solve every subproblem in one context. It is useful when the repository task benefits from parallel discovery, specialized verification, or separate implementation tracks.

This approach scales better than a single-threaded process because each worker can stay focused on one boundary. It also reduces context churn and makes it easier to validate outputs independently.

## When To Use
- A task naturally splits into isolated file groups.
- Research and implementation can happen on separate tracks.
- You need to compare several options or code paths quickly.
- The repository has enough surface area that one agent would thrash.

## Key Steps / Approach
1. Define a narrow goal and concrete success criteria for each worker.
2. Give each worker a bounded file set and explicit do-not-touch constraints.
3. Collect results only after the background work is complete.
4. Verify that each worker stayed within scope before merging outputs.
5. Synthesize results into one coherent implementation or decision.

## Common Pitfalls
- Assigning vague goals that invite guesswork.
- Overlapping file scopes between workers.
- Treating worker output as authoritative without verification.
