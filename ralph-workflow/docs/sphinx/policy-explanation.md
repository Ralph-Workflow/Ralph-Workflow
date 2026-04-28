# Policy Explanation

Ralph Workflow can render a human-readable explanation of the active policy configuration
with a single command. Use this to verify your pipeline definition before running or
to document the workflow your team is using.

## The command

```bash
ralph --explain-policy
```

This reads the active `pipeline.toml` (project-local `.agent/pipeline.toml` when present,
otherwise the bundled defaults) and prints a structured summary to stdout.

To inspect a custom policy directory instead:

```bash
ralph --explain-policy --explain-policy-dir /path/to/policy/dir
```

## What the output shows

The explanation covers all policy-declared elements:

| Section | What it contains |
|---------|-----------------|
| **Entry phase** | The phase where every run starts |
| **Terminal phase** | The phase that marks successful completion |
| **Phases** | Each declared phase with its role, drain, and key routing |
| **Loop counters** | Iteration counters with their names and caps |
| **Budget counters** | Outer-progress counters with names and budget-tracking flag |
| **Terminal outcomes** | All phases declared as terminal with their outcome type |
| **Parallel execution** | Whether parallel fan-out is configured and its source |
| **Recovery** | Cycle cap and where terminal failures route |

## Example output (default pipeline)

```
Policy Explanation
==================

Entry phase:    planning
Terminal phase: complete

Phases (9)
----------
  planning              role=execution      drain=planning
  development           role=execution      drain=development
  development_analysis  role=analysis       drain=development_analysis
  development_commit    role=commit         drain=development_commit
  review                role=review         drain=review
  review_analysis       role=analysis       drain=review_analysis
  fix                   role=execution      drain=fix
  review_commit         role=commit         drain=review_commit
  complete              role=terminal       drain=complete

Loop counters (2)
-----------------
  development_analysis_iteration  max=3
  review_analysis_iteration       max=2

Budget counters (2)
-------------------
  iteration      tracks_budget=True
  reviewer_pass  tracks_budget=True

Terminal outcomes (1)
---------------------
  success    → complete

Parallel execution
------------------
  Source: planning_artifact_work_units
  Phase:  development

Recovery
--------
  Cycle cap:               200
  Terminal recovery route: failed
```

## Why this is useful

Reading the explanation output answers "what will Ralph Workflow do?" without reading
TOML files. It is the machine-enforced statement of how the active policy routes work.

The explanation output is deterministic: for the same `pipeline.toml` the output is always
the same. Pin it in a review artifact, CI log, or runbook to record the exact workflow
a run used.

## How the explanation is generated

The command calls `ralph.policy.explain.explain_policy()` which traverses the validated
`PipelinePolicy` in memory and produces a `PolicyExplanation` dataclass. That dataclass
is rendered by `ralph.policy.render.render_explanation_text()` into the human-readable
format shown above.

Because it runs against the already-validated policy, an explanation can only be
produced if the policy is complete. If `pipeline.toml` is invalid, the command exits 1
and prints a `PolicyValidationError` to stderr instead of partial output.

## Related pages

- [Configuration](configuration.md) — `pipeline.toml` policy fields reference
- [Concepts](concepts.md) — phase roles, loop counters, and budget counters explained
- [Troubleshooting](troubleshooting.md) — common `PolicyValidationError` patterns
