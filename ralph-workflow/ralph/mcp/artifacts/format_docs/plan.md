# plan artifact format

A plan is the executor's instruction set. Submit it with
`ralph_submit_md_artifact` using `artifact_type: plan`.

The only submission requirements are readable text (valid UTF-8, no NUL bytes,
mostly printable characters), non-empty content, at least ten words, no more
than 4,000,000 raw UTF-8 bytes, and recognizable plan intent. The boundary
does not require headings, IDs, fields, dependencies, ownership, or a unit
count. There are no structural errors or advisories. Prose remains
authoritative; extraction is best effort. The planning analyzer judges coverage,
truthfulness, actionability, parallel decomposition, execution fit, and conflicts.

An executor-ready plan is a compact, evidence-grounded handoff rather than a task
inventory. State requested outcomes, constraints, non-goals, safe assumptions,
and observable acceptance criteria. Ground the chosen approach in current files,
call or data flow, existing patterns, and real verification entry points. Map each
requirement to owned work and proof, describe consequential interface and
integration decisions, and cover compatibility, migration, rollback, failures,
security, or performance only when applicable. Include focused checks plus an
integrated end-to-end observation. Name evidence that would trigger replanning of
an assumption, contract, dependency, ownership boundary, or acceptance check.
Scale detail to the task; do not add speculative internals or irrelevant sections.

Parallel work is recommended by default. When independent work exists, use
`## Work Units` as a helpful convention. **Work Units are a planning aid for
the developer agent's own sub-agent fan-out: they describe how the developer
agent should split and dispatch independent work to its sub-agents and do not
instruct Ralph Workflow to start, schedule, or coordinate workers.** Units may
name `Directories:` and/or exact `Paths:`, real prerequisites, relevant current
behavior, consumed or produced interface constraints, focused acceptance
evidence, a compact return format, shared contracts before their consumers, and
integration after fan-in. A wholly linear plan explains the coupling.

Finish with the visually explicit heading
`## PARALLEL EXECUTION PLAN — DISPATCH MANIFEST`. Prefix independently runnable
unit titles with `PARALLEL:` and dependency-gated work with `AFTER:`. Under
`Initial wave:`, name every unit that can start immediately, then name the later waves released by each
prerequisite. If fewer than two units can start, add `Why not parallel:` with
the concrete shared file or prerequisite output that forces serialization.
This makes the first dispatch an explicit executor action instead of an
inference from plan order.

The planning prompt supplies the configured development-phase timebox. Retries and loopbacks consume that same timebox rather than receiving a fresh budget. Use them to size self-contained unit context packets and estimate the critical path, including cap-induced queues. Leave an explicit reserve for fan-in, integration repair, full verification, and artifact submission. If the total plan is large but parallel decomposition keeps that bounded critical path inside the budget, the plan remains valid. If the runtime does not expose a numerical token limit, do not invent one; bound context through narrow ownership and compact unit returns instead.

At execution, ownership combines `Paths:` and `Directories:`; if neither is declared, the unit's steps' `Files:` supply the scope. Conflicting scopes run sequentially, and additional ready units run in later waves. Protected paths are dropped from unit briefs. Unknown scope and unextractable work stays with the main session; none of these execution safeguards rejects the plan.

## Recommended parallel example

This illustrative shape is optional, not a submission template. For a worked
example using repository paths and check commands, read
`.agent/artifact-formats/examples/plan.md`.

```markdown
---
type: plan
---

## Work Units

- [U-1] Shared parser contract
  Paths: ralph/parser/contracts.py

### [S-1] Define the shared parser result
Update the shared result used by both independent consumers.

- [U-2] Command consumer
  Paths: ralph/commands/parse.py
  Depends on: U-1

### [S-2] Adopt the parser result in the command
Update command behavior and run its focused test.

- [U-3] Documentation consumer
  Directories: docs/parser
  Depends on: U-1

### [S-3] Document the parser result
Describe the public behavior and build documentation.

- [U-4] Integration
  Depends on: U-2, U-3

### [S-4] Verify the combined behavior
Run the focused checks after the consumers converge.
```

An accepted prose plan can simply explain the requested outcome, repository
areas, changes, and proof in ten or more words. An explicit no-op is also
accepted when its explanatory prose meets the same sanity checks.

For a revision use `ralph_edit_md_artifact`; staging is not submission. After
a receipt, call `declare_complete` as the final action. See the complete
example at `.agent/artifact-formats/examples/plan.md`.
