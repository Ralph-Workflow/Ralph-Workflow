# Git and Rebase Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document explains how Ralph interacts with git repositories and why it avoids shelling out to `git`.

## libgit2 (No git CLI Dependency)

Ralph performs git operations via libgit2 through `git2`:

- Module: `ralph-workflow/src/git_helpers/`

This keeps behavior consistent across environments and makes unit/integration testing easier.

## Baselines: "Diff Since Start" vs "Diff for Review"

Ralph tracks a few baselines to keep prompts and reviews scoped:

- **Start commit baseline**: the commit OID at the start of a run.
  - Stored/loaded by: `ralph-workflow/src/git_helpers/start_commit.rs`
  - Used for: cumulative diffs for reviewer context (and "what did we change overall").

- **Review baseline**: a baseline per review pass.
  - Stored/loaded by: `ralph-workflow/src/git_helpers/review_baseline.rs`
  - Used for: incremental review depth modes and review prompts.

Diff helpers:

- `get_git_diff_from_start`: `ralph-workflow/src/git_helpers/repo.rs`
- `get_git_diff_for_review_with_workspace`: `ralph-workflow/src/git_helpers/repo.rs`

## Commit Creation

Commits are created deterministically by the pipeline (not by agents):

- Add all changes: `git_add_all*` in `ralph-workflow/src/git_helpers/repo.rs`
- Commit: `git_commit*` in `ralph-workflow/src/git_helpers/repo.rs`
- Identity resolution: `ralph-workflow/src/git_helpers/identity.rs`

Commit-message text is generated as part of the pipeline lifecycle (see `pipeline-lifecycle.md`).

## Git Wrapper and Hooks (Safety)

During agent phases, Ralph may install/enable mechanisms that prevent accidental commits by the agent itself and ensure consistent sequencing:

- Wrapper/marker management: `ralph-workflow/src/git_helpers/wrapper.rs`
- Hook management: `ralph-workflow/src/git_helpers/hooks.rs`

This is part of "keep decisions in reducers; keep I/O in handlers": agents should not mutate pipeline control flow by committing directly.

## Rebase Flow

Ralph can rebase onto the repo's default branch as part of unattended operation.

Primary code:

- Default branch detection: `ralph-workflow/src/git_helpers/branch.rs`
- Rebase engine + recovery: `ralph-workflow/src/git_helpers/rebase.rs`
- App-layer pre/post rebase orchestration: `ralph-workflow/src/app/rebase.rs`
- Checkpointed rebase state: `checkpoint::RebaseState` in `ralph-workflow/src/checkpoint/state/types/snapshots_and_phases.rs`

When conflicts occur, prompt building for conflict resolution lives in:

- `ralph-workflow/src/prompts/rebase.rs`

## Debugging Tips

- If a rebase is in progress, resume validation may refuse to proceed until the rebase state is consistent.
- If git wrapper markers/hooks are left behind, `git_helpers::wrapper` contains cleanup utilities.
