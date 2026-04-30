# Pipeline Lifecycle

This document describes the end-to-end lifecycle of a Ralph Workflow run: how the pipeline moves
through planning, development, analysis, commit, review, and fix phases, and how policy-defined
orchestration drives every routing decision.

The maintained implementation is the Python package in `ralph-workflow/ralph/`. All module
references in this document point to that package.

If you are looking for the generic event-loop and reducer mechanics, see
`event-loop-and-reducers.md`.
If you are looking for checkpoint/resume persistence, see `checkpoint-and-resume.md`.
If you are looking for git baseline behavior, see `git-and-rebase.md`.

## Policy-Defined Orchestration

Ralph Workflow is a **policy-defined orchestration framework**. The workflow shape — phases,
routing decisions, retry budgets, analysis loops, commit semantics, recovery paths, and terminal
outcomes — is declared in `.agent/pipeline.toml`, `.agent/agents.toml`, and
`.agent/artifacts.toml`. The runtime in `ralph/pipeline/` validates and enforces those
declarations without any hardcoded knowledge of specific phase names or routing outcomes.

Every routing decision the reducer makes traces back to a policy field:

- Phase advancement follows `transitions.on_success` / `on_loopback` / `on_failure`
- Analysis loop targets come from `transitions.on_loopback` (not from decision-key literals)
- Review bypass routes come from `bypass_routes[phase.clean_outcome]`
- Commit post-routing comes from `post_commit_routes` guarded by budget counters
- Recovery terminal routing comes from `recovery.failed_route`

The phase graph defined in `pipeline.toml` determines what phases exist, how they connect,
and what each phase role means. The runtime has no secret phase-name recognition.

## The Big Picture

A standard run moves through these phases in order:

```
planning → development → development_analysis → development_commit
        → review → review_analysis → fix → review_commit → complete
```

This sequence comes from the active `pipeline.toml`, not from hardcoded control flow. To
inspect the active pipeline shape, run:

```bash
ralph --explain-policy
```

The ASCII diagram shows phases, happy-path routing, loopbacks, decision branches, and terminal
outcomes — all derived from the current policy.

## Event Loop

The pipeline is driven by an explicit event loop in `ralph/pipeline/runner.py`:

```
state --orchestrate--> effect --handle--> event --reduce--> next_state
```

- **`PipelineState`** (`ralph/pipeline/state.py`): immutable snapshot of pipeline progress;
  the checkpoint payload.
- **`Effect`** (`ralph/pipeline/effects.py`): an intention to perform I/O (invoke an agent,
  write a checkpoint, etc.). Effects have no side effects themselves.
- **`Event`** (`ralph/pipeline/events.py`): a fact about something that happened. The reducer
  consumes events.
- **`reduce()`** (`ralph/pipeline/reducer.py`): pure function `(state, event, policy) →
  (new_state, effects)`. No I/O; fully deterministic; dispatches through policy for all routing.
- **`Orchestrator`** (`ralph/pipeline/orchestrator.py`): pure function that derives the next
  effect from the current state.

The loop terminates when `state.phase` reaches the configured terminal phase (typically
`complete` or the `failed_route` declared in `recovery`).

## Phase Lifecycle

### Planning

The planning phase produces a structured plan artifact. The planning agent reads `PROMPT.md`
and writes `.agent/artifacts/plan.json` (machine-readable) and `.agent/PLAN.md`
(human/agent handoff).

Advancement: when the planning agent produces a valid plan artifact, the reducer emits
`AGENT_SUCCESS` and routes to the next phase declared in `transitions.on_success`.

### Development

The development agent edits the repository to implement the plan. In parallel mode, multiple
workers may each implement a subset of the work units declared by the plan.

The development phase can loop (via `transitions.on_loopback`) back to itself when the
analysis phase decides additional work is needed.

### Development Analysis

The analysis agent inspects the diff against the plan and emits a structured decision artifact.
The reducer handles two events:

- `ANALYSIS_SUCCESS`: routes via `transitions.on_success` (typically to `development_commit`)
- `ANALYSIS_LOOPBACK`: routes via `transitions.on_loopback` (back to `development`)

Routing comes exclusively from `transitions` — not from decision-key literals in the `decisions`
map. The `decisions` map is a vocabulary contract for artifact validation, not a routing table.

The loop counter declared in `[loop_counters.<name>]` bounds how many iterations are allowed
before the pipeline routes to the failure path.

### Development Commit

The commit agent generates a commit message and creates a commit. After a successful commit,
the reducer routes using `post_commit_routes` guarded by budget counters. The `iteration`
budget counter determines whether to continue with review or terminate.

If the diff is empty (nothing to commit), the commit is skipped and routing proceeds as if
the commit succeeded, without incrementing the commit counter.

### Review

The review agent inspects the committed diff and produces an issues artifact. Two events drive
review routing:

- `REVIEW_CLEAN`: the reviewer found no issues. The reducer reads `phase.clean_outcome` from
  policy to look up the bypass route in `bypass_routes`. Falls back to `transitions.on_success`
  when `clean_outcome` is not set.
- `REVIEW_ISSUES_FOUND`: issues were found. The `review_outcome` label is read from the
  phase's `issues_outcome` policy field. Routing follows `transitions.on_loopback`.

Both `clean_outcome` and `issues_outcome` are required policy fields for `role='review'`
phases (enforced by `validate_policy_completeness`).

### Review Analysis

Like development analysis, but for the review loop. The analysis agent decides whether the
reviewer's issues have been adequately addressed.

- `ANALYSIS_SUCCESS` → `transitions.on_success` (typically `review_commit`)
- `ANALYSIS_LOOPBACK` → `transitions.on_loopback` (back to `fix`)

The `review_analysis_iteration` loop counter bounds the review-fix cycles.

### Fix

The fix agent addresses the issues identified during review. After fix succeeds, routing
follows `transitions.on_success` (back to `review_analysis` or directly to `review`).

### Review Commit

After review issues are resolved, the review commit agent commits the fix. Post-commit routing
is guarded by the `reviewer_pass` budget counter, similar to the development commit.

### Complete

The `complete` phase is the terminal success outcome. Its `role='terminal'` declaration in
policy makes it terminal; the runtime enforces this through `validate_policy_completeness`.

## Recovery

When any phase exhausts its agent chain or reaches a non-recoverable failure, the reducer
transitions to the route declared in `recovery.failed_route`. This is the policy-declared
terminal failure path, not a hardcoded constant.

The recovery controller in `ralph/recovery/controller.py` handles failure classification,
budget management, and agent retry/fallback decisions within each phase. Workflow-level recovery
routing (which phase to go to on terminal failure) is always policy-owned.

## Where to Look in Code

| Concern | Location |
|---------|----------|
| State machine core | `ralph/pipeline/reducer.py` |
| Phase orchestration | `ralph/pipeline/orchestrator.py` |
| Pipeline state | `ralph/pipeline/state.py` |
| Events | `ralph/pipeline/events.py` |
| Effects | `ralph/pipeline/effects.py` |
| Runner / event loop | `ralph/pipeline/runner.py` |
| Policy models | `ralph/policy/models.py` |
| Policy validation | `ralph/policy/validation.py` |
| Policy loading | `ralph/policy/loader.py` |
| Policy explanation | `ralph/policy/explain.py`, `ralph/policy/render.py` |
| Phase handlers | `ralph/phases/` |
| Recovery | `ralph/recovery/` |
