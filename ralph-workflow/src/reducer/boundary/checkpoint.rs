use super::MainEffectHandler;
use crate::checkpoint::{
    save_checkpoint_with_workspace, CheckpointBuilder, PipelinePhase as CheckpointPhase,
};
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{CheckpointTrigger, PipelineEvent, PipelinePhase};
use crate::reducer::state::PipelineState;
impl MainEffectHandler {
    pub(super) fn save_checkpoint(
        &self,
        ctx: &PhaseContext<'_>,
        trigger: CheckpointTrigger,
    ) -> EffectResult {
        if ctx.config.features.checkpoint_enabled {
            save_checkpoint_from_state(&self.state, ctx);
        }

        let result = EffectResult::event(PipelineEvent::checkpoint_saved(trigger));

        // If the pipeline reaches a phase boundary but checkpoint writing is disabled (or the
        // checkpoint file write is skipped), orchestration can repeatedly derive the
        // phase-transition checkpoint effect without making progress.
        //
        // Emit the phase completion event as a separate reducer event so the state machine
        // always advances past the boundary.
        let additional_event = if trigger == CheckpointTrigger::PhaseTransition {
            match self.state.phase {
                PipelinePhase::Development
                    if self.state.iteration >= self.state.total_iterations =>
                {
                    Some(PipelineEvent::development_phase_completed())
                }
                PipelinePhase::Review
                    if self.state.reviewer_pass >= self.state.total_reviewer_passes =>
                {
                    Some(PipelineEvent::review_phase_completed(
                        /* early_exit */ false,
                    ))
                }
                _ => None,
            }
        } else {
            None
        };

        additional_event
            .map(|ev| result.clone().with_additional_event(ev))
            .unwrap_or(result)
    }
}

fn save_checkpoint_from_state(state: &PipelineState, ctx: &PhaseContext<'_>) {
    // When the user pressed Ctrl+C, we must write a checkpoint for resume.
    //
    // RFC-007 requires deterministic prompt replay on resume, so `prompt_history`
    // is correctness-critical and must always be persisted.
    //
    // We may still omit other large optional fields on interrupt to reduce
    // serialization overhead in debug builds under CPU contention.
    //
    // We still write the full file_system_state because that is critical for
    // resume validation -- but capture_git_state already skips git commands
    // when user_interrupted_occurred(), so file capture is fast.
    let skip_large_fields = crate::interrupt::user_interrupted_occurred();

    let builder = CheckpointBuilder::new()
        .phase(
            map_to_checkpoint_phase(state.phase),
            state.iteration,
            state.total_iterations,
        )
        .reviewer_pass(state.reviewer_pass, state.total_reviewer_passes)
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
        .with_prompt_history(state.prompt_history.clone())
        .with_prompt_inputs(state.prompt_inputs.clone())
        .with_prompt_permissions(state.prompt_permissions.clone())
        .with_last_substitution_log(if skip_large_fields {
            None
        } else {
            state.last_substitution_log.clone()
        })
        .with_log_run_id(ctx.run_log_context.run_id().to_string());

    if let Some(checkpoint) = builder.build_with_workspace(ctx.workspace) {
        let checkpoint = checkpoint.with_recovery_state(state);

        let _ = save_checkpoint_with_workspace(ctx.workspace, &checkpoint);
    }
}

const fn map_to_checkpoint_phase(phase: crate::reducer::event::PipelinePhase) -> CheckpointPhase {
    match phase {
        crate::reducer::event::PipelinePhase::Planning => CheckpointPhase::Planning,
        crate::reducer::event::PipelinePhase::Development => CheckpointPhase::Development,
        crate::reducer::event::PipelinePhase::Review => CheckpointPhase::Review,
        crate::reducer::event::PipelinePhase::CommitMessage => CheckpointPhase::CommitMessage,
        crate::reducer::event::PipelinePhase::FinalValidation
        | crate::reducer::event::PipelinePhase::Finalizing => CheckpointPhase::FinalValidation,
        crate::reducer::event::PipelinePhase::Complete => CheckpointPhase::Complete,
        crate::reducer::event::PipelinePhase::AwaitingDevFix => CheckpointPhase::AwaitingDevFix,
        crate::reducer::event::PipelinePhase::Interrupted => CheckpointPhase::Interrupted,
    }
}

#[cfg(test)]
mod tests {
    use super::save_checkpoint_from_state;
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::{ExecutionHistory, ExecutionStep, StepOutcome};
    use crate::checkpoint::load_checkpoint_with_workspace;
    use crate::checkpoint::RunContext;
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::interrupt::{
        interrupt_test_lock, request_user_interrupt, reset_user_interrupted_occurred,
        take_user_interrupt_request,
    };
    use crate::logger::{Colors, Logger};
    use crate::logging::RunLogContext;
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::prompts::PromptHistoryEntry;
    use crate::reducer::state::PipelineState;
    use crate::workspace::MemoryWorkspace;
    use std::path::Path;
    use std::sync::Arc;

    #[test]
    fn interrupt_checkpoint_from_reducer_state_persists_prompt_history_and_recovery_epoch() {
        // Arrange
        // The interrupt flags are process-global; coordinate test access.
        let _lock = interrupt_test_lock();
        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();

        request_user_interrupt();

        let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
        let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
        let run_log_context = RunLogContext::new(&workspace).expect("run log context");
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let config = Config::default();
        let registry = AgentRegistry::new().expect("registry");
        let template_context = TemplateContext::default();
        let executor = Arc::new(MockProcessExecutor::new());
        let mut timer = Timer::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

        // `CheckpointBuilder::capture_from_context` requires the agent configs to exist in the
        // registry, otherwise checkpoint build returns None.
        let developer_agent = "codex";
        let reviewer_agent = "codex";

        let ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent,
            reviewer_agent,
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor.as_ref(),
            executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
            repo_root: Path::new("/test/repo"),
            workspace: &workspace,
            workspace_arc: Arc::clone(&workspace_arc),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &config.cloud,
            env: &git_env,
        };

        let mut state = PipelineState::initial(1, 0);
        state.recovery_epoch = 7;
        state.prompt_history.insert(
            "planning_0".to_string(),
            PromptHistoryEntry::from_string("prompt".to_string()),
        );

        // Act
        save_checkpoint_from_state(&state, &ctx);

        // Assert
        let checkpoint = load_checkpoint_with_workspace(&workspace)
            .expect("checkpoint load should succeed")
            .expect("checkpoint should exist");

        assert_eq!(
            checkpoint.recovery_epoch, 7,
            "checkpoint must preserve reducer recovery_epoch for deterministic resume"
        );
        assert!(
            checkpoint
                .prompt_history
                .as_ref()
                .is_some_and(|history| history.contains_key("planning_0")),
            "checkpoint must preserve prompt_history on interrupt so resume does not regenerate prompts"
        );

        // Cleanup: restore interrupt flags for other tests.
        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();
    }

    #[test]
    fn interrupt_checkpoint_from_reducer_state_persists_execution_history_for_diagnostics() {
        // Arrange
        // The interrupt flags are process-global; coordinate test access.
        let _lock = interrupt_test_lock();
        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();

        request_user_interrupt();

        let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
        let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
        let run_log_context = RunLogContext::new(&workspace).expect("run log context");
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let config = Config::default();
        let registry = AgentRegistry::new().expect("registry");
        let template_context = TemplateContext::default();
        let executor = Arc::new(MockProcessExecutor::new());
        let mut timer = Timer::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

        let mut execution_history = ExecutionHistory::new();
        let _ = execution_history.add_step_bounded(
            ExecutionStep::new(
                "planning",
                0,
                "checkpoint",
                StepOutcome::success(None, Vec::new()),
            ),
            100,
        );

        let ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "codex",
            reviewer_agent: "codex",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history,
            executor: executor.as_ref(),
            executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
            repo_root: Path::new("/test/repo"),
            workspace: &workspace,
            workspace_arc: Arc::clone(&workspace_arc),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &config.cloud,
            env: &git_env,
        };

        let state = PipelineState::initial(1, 0);

        // Act
        save_checkpoint_from_state(&state, &ctx);

        // Assert
        let checkpoint = load_checkpoint_with_workspace(&workspace)
            .expect("checkpoint load should succeed")
            .expect("checkpoint should exist");

        let history = checkpoint
            .execution_history
            .as_ref()
            .expect("checkpoint must include execution_history");
        assert_eq!(
            history.steps.len(),
            1,
            "interrupt-time reducer checkpoint should retain execution history for debugging"
        );
        assert_eq!(
            history
                .steps
                .front()
                .map(|step| step.phase.as_ref())
                .unwrap_or_default(),
            "planning",
            "execution history should preserve step content"
        );

        // Cleanup
        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();
    }

    #[test]
    fn checkpoint_from_reducer_state_persists_commit_residual_state_for_resume() {
        // Arrange
        let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");
        let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
        let run_log_context = RunLogContext::new(&workspace).expect("run log context");
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let config = Config::default();
        let registry = AgentRegistry::new().expect("registry");
        let template_context = TemplateContext::default();
        let executor = Arc::new(MockProcessExecutor::new());
        let mut timer = Timer::new();
        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

        let ctx = PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "codex",
            reviewer_agent: "codex",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor.as_ref(),
            executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
            repo_root: Path::new("/test/repo"),
            workspace: &workspace,
            workspace_arc: Arc::clone(&workspace_arc),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &config.cloud,
            env: &git_env,
        };

        let mut state = PipelineState::initial(1, 0);
        state.commit_residual_retry_pass = 2;
        state.commit_residual_files = vec!["src/leftover.rs".to_string()];

        // Act
        save_checkpoint_from_state(&state, &ctx);

        // Assert
        let checkpoint = load_checkpoint_with_workspace(&workspace)
            .expect("checkpoint load should succeed")
            .expect("checkpoint should exist");

        assert_eq!(
            checkpoint.commit_residual_retry_pass, 2,
            "commit_residual_retry_pass must be persisted for deterministic retry resume"
        );
        assert_eq!(
            checkpoint.commit_residual_files,
            vec!["src/leftover.rs".to_string()],
            "commit_residual_files must be persisted for unattended carry-forward"
        );
    }
}
