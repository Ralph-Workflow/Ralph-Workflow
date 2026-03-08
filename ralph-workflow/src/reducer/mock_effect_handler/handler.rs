//! `EffectHandler` and `StatefulHandler` trait implementations for `MockEffectHandler`.
//!
//! This module implements the standard handler traits, allowing `MockEffectHandler`
//! to be used as a drop-in replacement for `MainEffectHandler` in tests.
//!
//! ## Trait Implementations
//!
//! ### `EffectHandler`
//!
//! The `execute()` method handles effects that require workspace access:
//! - `SaveCheckpoint` - Actually saves checkpoint for resume tests
//! - `TriggerDevFixFlow` - Dispatch dev-fix flow (no termination marker)
//! - `EmitCompletionMarkerAndTerminate` - Writes completion marker for termination tests
//! - All other effects delegate to `execute_mock()` (see [`super::effect_mapping`])
//!
//! ### `StatefulHandler`
//!
//! The `update_state()` method synchronizes the mock's internal state after each
//! event is processed. This allows effect mapping to depend on current pipeline
//! state (e.g., phase transitions).
//!
//! ## Design Rationale
//!
//! Most effects can be mocked without workspace access - they're pure effect-to-event
//! mappings. Only a few effects genuinely need to interact with the workspace:
//!
//! - **`SaveCheckpoint`**: Integration tests verify checkpoint/resume behavior, so
//!   the mock actually writes checkpoint files to the test workspace.
//!
//! - **`EmitCompletionMarkerAndTerminate`**: Tests verify completion marker file creation,
//!   so the mock writes the marker file before emitting events.
//!
//! This separation keeps most mock logic pure (in `effect_mapping`) while handling
//! workspace-dependent cases here.
//!
//! ## See Also
//!
//! - [`super::effect_mapping`] - Pure effect-to-event mapping logic
//! - [`super::core`] - `MockEffectHandler` struct and builder methods

use super::{
    Effect, EffectHandler, EffectResult, MockEffectHandler, PhaseContext, PipelineEvent,
    PipelineState, Result,
};

/// Implement the `EffectHandler` trait for `MockEffectHandler`.
///
/// This allows `MockEffectHandler` to be used as a drop-in replacement for
/// `MainEffectHandler` in tests. The `PhaseContext` is ignored for most effects -
/// the mock simply captures the effect and returns an appropriate mock event.
///
/// Special cases that require workspace access:
/// - `SaveCheckpoint` - Actually saves checkpoint for resume tests
/// - `EmitCompletionMarkerAndTerminate` - Writes completion marker file
impl EffectHandler<'_> for MockEffectHandler {
    fn execute(&mut self, effect: Effect, ctx: &mut PhaseContext<'_>) -> Result<EffectResult> {
        if self.panic_on_next_execute {
            self.panic_on_next_execute = false;
            panic!("MockEffectHandler panic injected by test");
        }

        match effect {
            Effect::CheckCommitDiff => {
                use crate::reducer::prompt_inputs::sha256_hex_str;
                use std::path::Path;

                // Write the simulated diff content to the workspace so tests can assert on it.
                let tmp_dir = Path::new(".agent/tmp");
                if !ctx.workspace.exists(tmp_dir) {
                    // In MemoryWorkspace this is in-memory, not real I/O.
                    ctx.workspace
                        .create_dir_all(tmp_dir)
                        .map_err(|e| anyhow::anyhow!(e))?;
                }

                let content = if let Some(staged) = self.staged_diff_contents.pop_front() {
                    staged
                } else if let Some(ref err) = self.simulate_commit_diff_error {
                    format!(
                        r"## DIFF UNAVAILABLE - INVESTIGATION REQUIRED

The `git diff` command failed with error: {err}

You must investigate what changed by:

1. Run `git status` to see which files are modified/staged
2. Examine the content of modified files to understand what changed
3. Compare with recent git history if available (`git log -1 --stat`)
4. Based on your investigation, generate an appropriate commit message

If you determine there are NO actual changes to commit, respond with:
<ralph-commit><ralph-skip>Your reason why no commit is needed</ralph-skip></ralph-commit>
"
                    )
                } else if let Some(ref content) = self.simulate_commit_diff_content {
                    content.clone()
                } else if self.simulate_empty_diff {
                    String::new()
                } else {
                    "+ mock diff\n".to_string()
                };

                ctx.workspace
                    .write(Path::new(".agent/tmp/commit_diff.txt"), &content)
                    .map_err(|e| anyhow::anyhow!(e))?;

                self.captured_effects
                    .borrow_mut()
                    .push(Effect::CheckCommitDiff);

                let event = PipelineEvent::commit_diff_prepared(
                    content.trim().is_empty(),
                    sha256_hex_str(&content),
                );
                self.captured_events.borrow_mut().push(event.clone());
                Ok(EffectResult::event(event))
            }

            Effect::CheckUncommittedChangesBeforeTermination => {
                use crate::reducer::event::ErrorEvent;

                self.captured_effects
                    .borrow_mut()
                    .push(Effect::CheckUncommittedChangesBeforeTermination);

                match self.pre_termination_snapshot.clone() {
                    super::core::PreTerminationSnapshotMock::Clean => {
                        let event = PipelineEvent::pre_termination_safety_check_passed();
                        self.captured_events.borrow_mut().push(event.clone());
                        Ok(EffectResult::event(event))
                    }
                    super::core::PreTerminationSnapshotMock::Dirty { file_count } => {
                        let event =
                            PipelineEvent::pre_termination_uncommitted_changes_detected(file_count);
                        self.captured_events.borrow_mut().push(event.clone());
                        Ok(EffectResult::event(event))
                    }
                    super::core::PreTerminationSnapshotMock::Error { kind } => {
                        Err(ErrorEvent::GitStatusFailed { kind }.into())
                    }
                }
            }

            Effect::ReportAgentChainExhausted { role, phase, cycle } => {
                use crate::reducer::event::ErrorEvent;
                Err(ErrorEvent::AgentChainExhausted { role, phase, cycle }.into())
            }
            Effect::SaveCheckpoint { trigger } => {
                // Actually save checkpoint to workspace for resume tests
                use crate::checkpoint::{
                    save_checkpoint_with_workspace, CheckpointBuilder, PipelinePhase,
                };

                // Map reducer phase to checkpoint phase
                let checkpoint_phase = match self.state.phase {
                    crate::reducer::event::PipelinePhase::Planning => PipelinePhase::Planning,
                    crate::reducer::event::PipelinePhase::Development => PipelinePhase::Development,
                    crate::reducer::event::PipelinePhase::Review => PipelinePhase::Review,
                    crate::reducer::event::PipelinePhase::CommitMessage => {
                        PipelinePhase::CommitMessage
                    }
                    crate::reducer::event::PipelinePhase::FinalValidation
                    | crate::reducer::event::PipelinePhase::Finalizing => {
                        PipelinePhase::FinalValidation
                    }
                    crate::reducer::event::PipelinePhase::Complete => PipelinePhase::Complete,
                    crate::reducer::event::PipelinePhase::AwaitingDevFix => {
                        PipelinePhase::AwaitingDevFix
                    }
                    crate::reducer::event::PipelinePhase::Interrupted => PipelinePhase::Interrupted,
                };

                // Build checkpoint using CheckpointBuilder
                let builder = CheckpointBuilder::new()
                    .phase(
                        checkpoint_phase,
                        self.state.iteration,
                        self.state.total_iterations,
                    )
                    .reviewer_pass(self.state.reviewer_pass, self.state.total_reviewer_passes)
                    .capture_from_context(
                        ctx.config,
                        ctx.registry,
                        ctx.developer_agent,
                        ctx.reviewer_agent,
                        ctx.logger,
                        &ctx.run_context,
                    )
                    .with_executor_from_context(std::sync::Arc::clone(&ctx.executor_arc))
                    .with_execution_history(ctx.execution_history.clone())
                    .with_prompt_history(ctx.clone_prompt_history())
                    .with_prompt_inputs(self.state.prompt_inputs.clone())
                    .with_prompt_permissions(self.state.prompt_permissions.clone())
                    .with_log_run_id(ctx.run_log_context.run_id().to_string());

                if let Some(checkpoint) = builder.build_with_workspace(ctx.workspace) {
                    let mut checkpoint = checkpoint;
                    checkpoint.dev_fix_attempt_count = self.state.dev_fix_attempt_count;
                    checkpoint.recovery_escalation_level = self.state.recovery_escalation_level;
                    checkpoint.failed_phase_for_recovery = self.state.failed_phase_for_recovery;
                    checkpoint.interrupted_by_user = self.state.interrupted_by_user;

                    if let Err(err) = save_checkpoint_with_workspace(ctx.workspace, &checkpoint) {
                        ctx.logger
                            .warn(&format!("Failed to save checkpoint in mock: {err}"));
                    }
                }

                // Delegate to execute_mock for effect capture + mock event emission.
                Ok(self.execute_mock(&Effect::SaveCheckpoint { trigger }))
            }
            Effect::TriggerDevFixFlow {
                failed_phase,
                failed_role,
                retry_cycle,
            } => {
                // Capture the effect for test verification
                self.captured_effects
                    .borrow_mut()
                    .push(Effect::TriggerDevFixFlow {
                        failed_phase,
                        failed_role,
                        retry_cycle,
                    });

                // Emit trigger and completion events (NO CompletionMarkerEmitted).
                // Completion markers are only emitted on actual termination
                // (Effect::EmitCompletionMarkerAndTerminate).
                Ok(EffectResult::event(PipelineEvent::AwaitingDevFix(
                    crate::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
                        failed_phase,
                        failed_role,
                    },
                ))
                .with_additional_event(PipelineEvent::AwaitingDevFix(
                    crate::reducer::event::AwaitingDevFixEvent::DevFixCompleted {
                        success: false,
                        summary: Some("Mock dev-fix flow".to_string()),
                    },
                )))
            }
            Effect::EmitCompletionMarkerAndTerminate { is_failure, reason } => {
                // Mock writes completion marker to match real handler semantics.
                let reason_for_record = reason.clone();
                let marker_dir = std::path::Path::new(".agent/tmp");
                if let Err(err) = ctx.workspace.create_dir_all(marker_dir) {
                    ctx.logger.warn(&format!(
                        "Failed to create completion marker directory in mock: {err}"
                    ));
                }
                let marker_path = std::path::Path::new(".agent/tmp/completion_marker");
                let content = if is_failure {
                    format!(
                        "failure\n{}",
                        reason.unwrap_or_else(|| "unknown".to_string())
                    )
                } else {
                    "success\n".to_string()
                };
                if let Err(err) = ctx.workspace.write(marker_path, &content) {
                    ctx.logger
                        .warn(&format!("Failed to write completion marker in mock: {err}"));
                }

                // Delegate to execute_mock for effect capture + mock event emission.
                Ok(
                    self.execute_mock(&Effect::EmitCompletionMarkerAndTerminate {
                        is_failure,
                        reason: reason_for_record,
                    }),
                )
            }
            _ => Ok(self.execute_mock(&effect)),
        }
    }
}

/// Implement `StatefulHandler` for `MockEffectHandler`.
///
/// This allows the event loop to update the mock's internal state after
/// each event is processed. The mock maintains synchronized state to support
/// effects that depend on current pipeline state (e.g., phase transitions).
impl crate::app::event_loop::StatefulHandler for MockEffectHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}
