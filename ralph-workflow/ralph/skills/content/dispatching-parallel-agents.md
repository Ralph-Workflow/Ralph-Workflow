# dispatching-parallel-agents

## Purpose
Dispatching-parallel-agents is the skill for splitting independent work across multiple workers at the same time. It is especially effective for repository discovery, test inventory, file pattern analysis, and other tasks where the same question can be asked from different angles without overlap.

Parallel workers shorten wall-clock time and make it easier to cross-check findings. The key is to keep the units disjoint so the output is additive rather than redundant.

## When To Use
- Two or more work chunks do not need the same files.
- You need multiple independent searches or readings.
- A single pass is likely to miss a useful pattern.
- The task benefits from discovery and cross-validation at once.

## Key Steps / Approach
1. Identify truly independent work units and give each one a unique file scope.
2. State the expected output format so results are easy to compare.
3. Use parallel dispatch only when the overlap risk is low.
4. Gather and reconcile the results before starting changes that depend on them.
5. Cancel disposable workers once their contribution is no longer needed.

## Common Pitfalls
- Launching workers with overlapping edit areas.
- Using parallelism to hide uncertainty instead of reducing it.
- Ignoring duplicate findings rather than using them as evidence.
