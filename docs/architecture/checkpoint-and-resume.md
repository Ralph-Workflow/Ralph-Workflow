# Checkpoints and Resume Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document explains how Ralph persists run state and how `--resume` restores it.

## What a Checkpoint Is

A checkpoint is a serialized snapshot of "enough state to resume" an unattended run without guessing.

The checkpoint is stored at:

- `.agent/checkpoint.json`

Core code:

- Types: `ralph-workflow/src/checkpoint/state/types/checkpoint.rs`
- Serialization: `ralph-workflow/src/checkpoint/state/serialization.rs`
- Validation: `ralph-workflow/src/checkpoint/validation.rs`
- Resume UX + validation plumbing: `ralph-workflow/src/app/resume.rs`

## What the Checkpoint Contains

`PipelineCheckpoint` intentionally captures both progress and "reconstruction" data:

- **Progress**: `phase`, `iteration/total_iterations`, `reviewer_pass/total_reviewer_passes`.
- **Run identity**: `run_id`, optional `parent_run_id`, and `resume_count`.
- **CLI args snapshot**: `cli_args` (so resume uses the same iteration counts, isolation mode, verbosity, etc.).
- **Agent snapshots**: `developer_agent_config`, `reviewer_agent_config`.
- **Rebase state**: `rebase_state` (pre/post rebase progress and conflict status).
- **Validation fingerprints**: working dir, config path/checksum (if any), `PROMPT.md` checksum.
- **Hardened resume (v3+)**: optional `execution_history` and `file_system_state` used to validate/repair resumption.
- **Reducer prompt input state**: `prompt_inputs` for idempotent re-materialization of oversize inputs.
- **Prompt history**: `prompt_history` (`Option<HashMap<String, PromptHistoryEntry>>`) — stores the generated prompt text and optional SHA-256 content-ID for each scope key; enables deterministic prompt replay on resume without regenerating prompts.
- **Recovery epoch**: `recovery_epoch: u32` — incremented on Level-3 (iteration rewind) and Level-4 (full reset) recovery; carried in `PromptScopeKey` for auditing; `prompt_history` is cleared atomically when `recovery_epoch` increments.
- **Replay metadata version**: `replay_metadata_version: u32` — version field for backward-compatible migration of `prompt_history` format. Version 0 = legacy bare string values (`HashMap<String, String>`); version 1 = `PromptHistoryEntry` objects with optional `content_id`.

The format is versioned via `CHECKPOINT_VERSION` in `ralph-workflow/src/checkpoint/state/types/snapshots_and_phases.rs`.

## Prompt Replay and Resume Determinism

When resuming from a checkpoint, the pipeline must not regenerate prompts that were already used in the interrupted run. Regenerating prompts can change agent behavior because the underlying context (plan, code, diff) may have evolved. Deterministic resume requires replaying the exact prompts from the previous run.

### How Prompt Replay Works

1. **Prompt history is reducer-owned**: `PipelineState.prompt_history` (`HashMap<String, PromptHistoryEntry>`) is the canonical store. The checkpoint serializes and restores this map.

2. **Typed scope keys**: All prompt history lookups use `PromptScopeKey` typed constructors (not raw `format!()` strings). Constructors enforce required identity dimensions at compile time:
   - `PromptScopeKey::for_planning(iteration, retry_mode, recovery_epoch)`
   - `PromptScopeKey::for_development(iteration, continuation, retry_mode, recovery_epoch)`
   - `PromptScopeKey::for_commit(iteration, attempt, retry_mode, recovery_epoch)`
   - `PromptScopeKey::for_review(pass, retry_mode, recovery_epoch)`
   - `PromptScopeKey::for_fix(pass, retry_mode, recovery_epoch)`
   - `PromptScopeKey::for_conflict_resolution(phase, recovery_epoch)`

3. **Central dispatch**: `get_stored_or_generate_prompt` checks `prompt_history` for an entry matching the scope key's `Display` string. On hit, it validates the optional `content_id` and returns the stored prompt. On miss, it calls the generator closure and returns the fresh prompt. The caller is responsible for inserting the generated prompt into history.

4. **Content-ID validation**: `PromptHistoryEntry` carries an optional SHA-256 hex digest of the materialized inputs at generation time. If both the stored and current `content_id` are `Some` and differ, the stored entry is treated as a cache miss and a fresh prompt is generated. This prevents stale-content replay when inputs have changed.

5. **Replay observability**: `UIEvent::PromptReplayHit { key, was_replayed }` is emitted after each prompt lookup for observability and auditing.

### Epoch Boundaries and History Clearing

`recovery_epoch` is incremented on Level-3 (iteration rewind) and Level-4 (full reset) recovery. `prompt_history` is cleared atomically alongside the `recovery_epoch` increment, so stale replay candidates cannot survive an epoch boundary. The epoch value is carried in `PromptScopeKey` for auditing but is intentionally excluded from the `Display` string — `recovery_epoch` changes naturally invalidate history by clearing the map, not by changing key strings.

### Backward Compatibility

The `Display` output of `PromptScopeKey` is byte-identical to the legacy `format!()` strings it replaces. Existing checkpoint `prompt_history` maps (written before the typed key migration) remain compatible. `PromptHistoryEntry` uses a custom `Deserialize` implementation that accepts both v0 (bare string) and v1 (object with `content_id`) formats.

Code locations:
- `ralph-workflow/src/prompts/prompt_scope_key.rs` — `PromptScopeKey`, `PromptPhase`, `RetryMode`
- `ralph-workflow/src/prompts/prompt_history_entry.rs` — `PromptHistoryEntry` with backward-compat serde
- `ralph-workflow/src/prompts/prompt_dispatch.rs` — `get_stored_or_generate_prompt`
- `ralph-workflow/src/reducer/state/pipeline/core_state.rs` — `PipelineState.prompt_history` and `recovery_epoch`
- `ralph-workflow/src/checkpoint/state/types/checkpoint.rs` — `PipelineCheckpoint.prompt_history`, `replay_metadata_version`, `recovery_epoch`

## Checkpoint Phase vs Reducer Phase

There are two related-but-not-identical "phase" concepts:

- `checkpoint::PipelinePhase` is a coarse, user-facing phase used in checkpoint summaries.
  - Defined in `ralph-workflow/src/checkpoint/state/types/snapshots_and_phases.rs`.
- `reducer::event::PipelinePhase` is the reducer state machine phase used by orchestration.

When documenting behavior or debugging reducer logic, treat the reducer phase as authoritative.
When resuming or printing status for users, the checkpoint phase is the artifact you will see.

## When Checkpoints Are Written

Checkpoints are written from the app layer when the pipeline reaches states where resuming must be possible.

In particular:

- The `AwaitingDevFix -> Interrupted` flow is designed to emit a completion marker and then persist a checkpoint so a human (or a later run) can resume.
- Interrupt handling (Ctrl+C) saves a checkpoint so the run can continue later.

Reducer code requests checkpoint writes via pipeline effects/events:

- Trigger type: `reducer::CheckpointTrigger` (`ralph-workflow/src/reducer/event/`)
- Effect: `Effect::SaveCheckpoint { trigger: ... }` (`ralph-workflow/src/reducer/effect/types.rs`)

## Resume Flow (CLI)

`--resume` is handled before pipeline execution:

1. Load `.agent/checkpoint.json` from the repo root workspace.
2. Validate the checkpoint against current reality (config/prompt checksums, rebase-in-progress, hardened file state when enabled).
3. If validation succeeds, restore config and context, then re-enter the pipeline.

Resume entrypoint:

- `ralph-workflow/src/app/resume.rs`

## Hardened Resume (FileSystemState)

When enabled (see crate features; default includes `hardened-resume`), the checkpoint may include file state:

- `checkpoint::FileSystemState` (`ralph-workflow/src/checkpoint/file_state/`)

The intent is to detect (and sometimes repair) unsafe divergence between the saved checkpoint and the current working tree, instead of blindly continuing.

## Operational Debugging Tips

- Checkpoint format support is intentionally strict:
  - Supported: v3 (current) and a limited v2 -> v3 in-memory migration when the v2 JSON still matches the current struct shape.
  - Not supported: v1 and pre-v1 formats/phases. These cannot be upgraded automatically.
- If a checkpoint cannot be deserialized due to a version/format mismatch, the CLI intentionally guides you to "start fresh" by backing up and removing `.agent/checkpoint.json`.
- If resume keeps failing validation, start by checking whether a rebase is in progress and whether `.agent/` artifacts were modified externally.
