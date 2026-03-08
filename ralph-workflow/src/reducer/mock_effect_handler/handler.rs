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
//! - `CheckCommitDiff` - Writes simulated diff to workspace, reads staged diff sequence
//! - `MaterializeCommitInputs` - Reads diff from workspace and computes real byte sizes
//! - `CheckUncommittedChangesBeforeTermination` - Returns mocked safety check results
//! - `ReportAgentChainExhausted` - Returns error event for exhausted agent chains
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
/// Special cases that require workspace access (handled in `execute()` before delegating):
/// - `CheckCommitDiff` - Writes simulated diff; reads from staged diff sequence
/// - `MaterializeCommitInputs` - Reads diff from workspace, computes real byte sizes
/// - `CheckUncommittedChangesBeforeTermination` - Returns mocked pre-termination check
/// - `ReportAgentChainExhausted` - Returns error for exhausted agent chains
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

            Effect::MaterializeCommitInputs { attempt } => {
                use crate::reducer::prompt_inputs::sha256_hex_str;
                use crate::reducer::state::{
                    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
                    PromptMaterializationReason,
                };
                use std::path::Path;

                self.captured_effects
                    .borrow_mut()
                    .push(Effect::MaterializeCommitInputs { attempt });

                let diff_path = Path::new(".agent/tmp/commit_diff.txt");
                let content = match ctx.workspace.read(diff_path) {
                    Ok(content) => content,
                    Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                        // Match real handler semantics: invalidate when the diff file is missing.
                        let event = PipelineEvent::commit_diff_invalidated(
                            "Missing commit diff at .agent/tmp/commit_diff.txt".to_string(),
                        );
                        self.captured_events.borrow_mut().push(event.clone());
                        return Ok(EffectResult::event(event));
                    }
                    Err(err) => {
                        return Err(anyhow::anyhow!(err).context(
                            "Failed to read .agent/tmp/commit_diff.txt while materializing commit inputs",
                        ));
                    }
                };

                let original_bytes = content.len() as u64;
                let content_id_sha256 = sha256_hex_str(&content);
                let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();

                let input = MaterializedPromptInput {
                    kind: PromptInputKind::Diff,
                    content_id_sha256,
                    consumer_signature_sha256,
                    original_bytes,
                    final_bytes: original_bytes,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: PromptInputRepresentation::Inline,
                    reason: PromptMaterializationReason::WithinBudgets,
                };

                let event = PipelineEvent::commit_inputs_materialized(attempt, input);
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
                    .with_prompt_history(self.state.prompt_history.clone())
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::checkpoint::RunContext;
    use crate::config::Config;
    use crate::executor::{MockProcessExecutor, ProcessExecutor};
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::io;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    #[derive(Debug)]
    struct ReadErrorWorkspace {
        inner: MemoryWorkspace,
        deny_path: PathBuf,
        deny_kind: io::ErrorKind,
        deny_message: String,
    }

    impl ReadErrorWorkspace {
        fn new(inner: MemoryWorkspace, deny_path: impl Into<PathBuf>, kind: io::ErrorKind) -> Self {
            Self {
                inner,
                deny_path: deny_path.into(),
                deny_kind: kind,
                deny_message: "injected read error".to_string(),
            }
        }

        fn should_deny(&self, relative: &Path) -> bool {
            relative == self.deny_path.as_path()
        }
    }

    impl Workspace for ReadErrorWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            if self.should_deny(relative) {
                return Err(io::Error::new(self.deny_kind, self.deny_message.clone()));
            }
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            if self.should_deny(relative) {
                return Err(io::Error::new(self.deny_kind, self.deny_message.clone()));
            }
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        fn exists(&self, relative: &Path) -> bool {
            self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        fn is_dir(&self, relative: &Path) -> bool {
            self.inner.is_dir(relative)
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
        }
    }

    #[test]
    fn test_materialize_commit_inputs_propagates_non_not_found_workspace_read_errors() {
        let inner_ws = MemoryWorkspace::new_test().with_file(".agent/tmp/commit_diff.txt", "x");
        let deny_path = PathBuf::from(".agent/tmp/commit_diff.txt");
        let ws = ReadErrorWorkspace::new(inner_ws, deny_path, io::ErrorKind::PermissionDenied);

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();

        let config = Config::default();
        let registry = AgentRegistry::new().unwrap();
        let template_context = TemplateContext::default();

        let executor = Arc::new(MockProcessExecutor::new());
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let repo_root = PathBuf::from("/mock/repo");
        let run_log_context = crate::logging::RunLogContext::new(&ws).unwrap();
        let cloud = crate::config::types::CloudConfig::disabled();

        let ws_arc: Arc<dyn Workspace> = Arc::new(ws);
        let workspace_arc = Arc::clone(&ws_arc);

        let mut ctx = crate::phases::PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root: repo_root.as_path(),
            workspace: ws_arc.as_ref(),
            workspace_arc,
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
        };

        let mut handler = MockEffectHandler::new(PipelineState::initial(1, 0));
        let result = handler.execute(Effect::MaterializeCommitInputs { attempt: 1 }, &mut ctx);
        assert!(
            result.is_err(),
            "expected non-NotFound workspace read errors to propagate"
        );
    }
}
