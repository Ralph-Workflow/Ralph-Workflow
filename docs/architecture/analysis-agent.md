# Result Verification (Analysis Agent)

This document describes the *analysis step* inside the Development iteration.
For the full end-to-end lifecycle (Planning -> Development -> Commit -> Review/Fix loops), see `pipeline-lifecycle.md`.

## Purpose

The analysis agent verifies whether code changes satisfy the plan requirements.

The analysis agent:

1. **Verifies code changes** against `.agent/PLAN.md`
2. **Explores the codebase** - the diff is a starting point, not a boundary
3. **May run verification commands** (build, tests, linters) when appropriate
4. **Submits** a typed `development_result` MCP artifact with an `AnalysisDecision` outcome:
   - `needs_more_work` — loop back to current phase
   - `needs_replanning` — route to Planning
   - `ready_for_review` — route to Review (post-commit checkpoint)
   - `ready_to_commit` — route to CommitMessage
   - `needs_another_review` — route back to Review after fix

## Beyond the Diff

The diff provided to the analysis agent is a **starting point**, not a boundary. The analysis agent may:

- Read related files that the changes depend on
- Check imports, dependencies, and integration points
- Verify the changes work correctly in the broader codebase context
- Look at test files even if they weren't changed
- Run verification commands when appropriate for the project

## Where It Sits in the Development Flow

Within `PipelinePhase::Development`, orchestration intentionally runs two distinct roles:

1. `InvokeDevelopmentAgent` (Developer role)
2. `InvokeAnalysisAgent` (Analysis role)
3. `ExtractDevelopmentResult` → `ApplyDevelopmentOutcome` (reads the submitted MCP artifact)

This ordering is derived in `ralph-workflow/src/reducer/orchestration/phase_effects.rs`.

## Key Invariants

- Analysis runs after *every* developer invocation (including continuation attempts).
- Analysis does not increment `state.iteration`.
- Continuations remain within the same `iteration`; they reset `analysis_agent_invoked_iteration` so analysis re-runs after the next developer attempt.

## Empty Diff Handling

Analysis still runs when the diff is empty. This prevents false positives:

- Empty diff + plan satisfied => `completed`
- Empty diff + plan requires changes => `failed`

## Artifact Submission and Agent Fallback

The analysis agent must submit a valid `development_result` MCP artifact. If artifact submission fails or the artifact is malformed, the reducer triggers same-agent retry (and eventually agent chain fallback) using the same reducer-visible retry machinery as other phases.

Retries apply only to the analysis agent; they must not re-run the developer agent.

## Primary Code Locations

- State field: `analysis_agent_invoked_iteration` in `ralph-workflow/src/reducer/state/pipeline.rs`
- Orchestration guard: `ralph-workflow/src/reducer/orchestration/phase_effects.rs`
- State reduction: `ralph-workflow/src/reducer/state_reduction/development.rs`
- Prompt template: `ralph-workflow/src/prompts/templates/analysis_system_prompt.txt`
- Handler: `ralph-workflow/src/reducer/handler/analysis.rs`

## See Also

- `pipeline-lifecycle.md`
- `event-loop-and-reducers.md`
- `effect-system.md`
