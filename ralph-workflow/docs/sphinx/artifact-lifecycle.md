# Artifact and completion-detection lifecycle

> **Mental model page.** This is explanation, not a how-to. For the practical
> artifact authoring path, see [Artifacts](artifacts.md) and
> [Advanced artifact configuration](advanced-artifact-configuration.md).

An **artifact** is the durable evidence a phase produces.
Ralph Workflow's artifact lifecycle is what turns a phase's agent
invocation into reviewable, terminal evidence that survives the run.

## Why artifacts, not transcripts

A chat transcript shows what the agent *said*. An artifact shows what the
agent *did*. For unattended runs that you review in the morning, the
artifact is what you actually inspect.

Ralph Workflow's policy declares an **artifact contract** per phase. The
runtime validates the contract before accepting the phase as `done`. If
the contract is missing, the phase returns `fix-needed` or `blocked`,
not `done`.

## The artifact schema

Every artifact in Ralph Workflow is a JSON document that matches the
artifact submission contract:

- `ralph/mcp/artifacts/format_docs/<type>.md` — per-type schema docs
- `ralph/mcp/artifacts/canonical_submit.py` — the canonical submission path
- `ralph/mcp/artifacts/contract.py` — the Pydantic models that enforce
  the schema

The submission contract is verified by
`tests/test_artifact_submission_canonical_path.py` and audited by
`ralph.testing.audit_artifact_submission_canonical_path`.

## The submission path

Every artifact is submitted via `submit_artifact_canonical` in
`ralph/mcp/artifacts/canonical_submit.py`. This is the **only** supported
submission path; ad-hoc writes to the artifact store are not permitted.

The submission path:

1. Validates the artifact against the per-type Pydantic model
2. Writes the artifact to `.agent/artifacts/<run-id>/<type>.json`
3. Emits an artifact-submitted event into the reducer stream
4. Returns the artifact path to the calling phase

The runtime then consults the artifact contract for the current phase and
decides whether the artifact satisfies it.

## The artifact types

Ralph Workflow defines several artifact types per phase. The most common:

| Type                | Produced by               | What it must contain                                             |
| ------------------- | ------------------------- | ---------------------------------------------------------------- |
| `development_result`| development phase         | Outcome, changed files, checks run, reviewer focus                |
| `issues`            | review phase              | List of issues raised during review                              |
| `fix_result`        | fix cycle                 | Same shape as `development_result`, scoped to the fix             |
| `commit_message`    | commit phase              | Conventional-commit subject + structured payload                  |
| `commit_cleanup`    | commit phase              | Actions to delete / gitignore / git-exclude before commit         |
| `planning_analysis_decision` | planning analysis | analysis decision for a plan section                              |
| `review_analysis_decision`   | review analysis   | analysis decision for a review section                           |
| `development_analysis_decision` | development analysis | analysis decision for a development section                |
| `smoke_test_result` | smoke-test commands       | Black-box test summary                                           |
| `product_spec`      | product spec phase        | Spec the run was built against                                   |

Each type's contract lives at
`ralph/mcp/artifacts/format_docs/<type>.md`. The format docs are the
authoritative source — read them when authoring an artifact.

## Completion detection

A phase is `done` when **all** of:

1. The agent invocation returned (no timeout, no crash)
2. The artifact submitted via `submit_artifact_canonical`
3. The artifact satisfies the phase's declared contract
4. The reducer for the artifact type advances the pipeline state

If any step fails, the phase is not `done`. The reducer typically returns
`fix-needed` (artifact missing or malformed) or `blocked` (a precondition
failed) instead of advancing.

## The `declare_complete` terminal

When the final reducer decides the run is at a terminal state, the runtime
calls `ralph_declare_complete`. The terminal is the single, structured
handoff the user reviews:

- `done` — the development_result is the review surface
- `blocked` — the issues artifact explains what blocked
- `budget-exceeded` — the most recent artifact shows what was achieved
- `regression` — the verification artifact shows what failed

The terminal is the **only** signal the runtime hands back. There is no
"trust the transcript" path. If the terminal is `done`, the run is done.

## Why the canonical path matters

The canonical submission path is audited because ad-hoc artifact writes
are an attack surface: a bad artifact could advance the pipeline past
verification. By making the canonical path the only supported write, the
runtime guarantees:

- Every artifact is validated against its Pydantic model
- Every artifact is associated with a run ID
- Every artifact is recorded in the audit sink
- No artifact can bypass schema validation

The audit (`ralph.testing.audit_artifact_submission_canonical_path`) flags
any artifact submission that does not go through the canonical path.

## Related pages

- [Artifacts](artifacts.md) — every supported artifact type and its fields
- [Advanced artifact configuration](advanced-artifact-configuration.md) —
  how to extend the artifact contract
- [Artifact submission contract](../agents/artifact-submission-contract.md) —
  the author-level contract
- [Verification model](verification-model.md) — what verification does with
  artifacts