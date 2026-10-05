# plan artifact format

A plan is the executor's instruction set. Submit it with
`ralph_submit_md_artifact` using `artifact_type: plan`.

The only submission requirements are readable text (valid UTF-8, no NUL bytes,
mostly printable characters), non-empty content, at least ten words, no more
than 4,000,000 raw UTF-8 bytes, and recognizable plan intent. The boundary
does not require headings, IDs, fields, dependencies, ownership, or a unit
count. There are no structural errors or advisories. Prose remains
authoritative; extraction is best effort. The planning analyzer judges coverage,
truthfulness, actionability, parallel decomposition and execution conflicts.

Parallel work is recommended by default. When independent work exists, use
`## Work Units` as a helpful convention. Units may name `Directories:` and/or
exact `Paths:`, real prerequisites, shared contracts before their consumers,
and integration after fan-in. A wholly linear plan explains the coupling.

At execution, ownership combines `Paths:` and `Directories:`; if neither is
declared, the unit's steps' `Files:` supply the scope. Conflicting scopes run
sequentially, and units beyond worker capacity run in waves. Protected paths
are dropped from worker briefs. Unknown scope and unextractable work stay
with the main session; none of these execution safeguards rejects the plan.

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
