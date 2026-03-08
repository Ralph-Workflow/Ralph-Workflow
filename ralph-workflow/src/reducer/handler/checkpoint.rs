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

        let mut result = EffectResult::event(PipelineEvent::checkpoint_saved(trigger));

        // If the pipeline reaches a phase boundary but checkpoint writing is disabled (or the
        // checkpoint file write is skipped), orchestration can repeatedly derive the
        // phase-transition checkpoint effect without making progress.
        //
        // Emit the phase completion event as a separate reducer event so the state machine
        // always advances past the boundary.
        if trigger == CheckpointTrigger::PhaseTransition {
            match self.state.phase {
                PipelinePhase::Development
                    if self.state.iteration >= self.state.total_iterations =>
                {
                    result =
                        result.with_additional_event(PipelineEvent::development_phase_completed());
                }
                PipelinePhase::Review
                    if self.state.reviewer_pass >= self.state.total_reviewer_passes =>
                {
                    result = result.with_additional_event(PipelineEvent::review_phase_completed(
                        /* early_exit */ false,
                    ));
                }
                _ => {}
            }
        }

        result
    }
}

fn save_checkpoint_from_state(state: &PipelineState, ctx: &PhaseContext<'_>) {
    // When the user pressed Ctrl+C, we must write a checkpoint for resume
    // support, but we skip large optional fields (execution_history,
    // prompt_history, last_substitution_log, env_snapshot) to avoid slow JSON
    // serialization in debug builds under CPU contention.
    //
    // These fields are "nice to have" for resume quality but are not required
    // for correctness: the pipeline can resume from the phase/iteration alone.
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
        .with_execution_history(if skip_large_fields {
            // Omit execution history to avoid slow serialization on interrupt.
            crate::checkpoint::ExecutionHistory::new()
        } else {
            ctx.execution_history.clone()
        })
        .with_prompt_history(if skip_large_fields {
            // Omit prompt history (can be very large) on interrupt.
            std::collections::HashMap::new()
        } else {
            state.prompt_history.clone()
        })
        .with_prompt_inputs(state.prompt_inputs.clone())
        .with_prompt_permissions(state.prompt_permissions.clone())
        .with_last_substitution_log(if skip_large_fields {
            None
        } else {
            state.last_substitution_log.clone()
        })
        .with_log_run_id(ctx.run_log_context.run_id().to_string());

    if let Some(checkpoint) = builder.build_with_workspace(ctx.workspace) {
        let mut checkpoint = checkpoint;
        checkpoint.dev_fix_attempt_count = state.dev_fix_attempt_count;
        checkpoint.recovery_escalation_level = state.recovery_escalation_level;
        checkpoint.failed_phase_for_recovery = state.failed_phase_for_recovery;
        checkpoint.interrupted_by_user = state.interrupted_by_user;

        if state.cloud.enabled {
            checkpoint.cloud_state =
                Some(crate::checkpoint::state::CloudCheckpointState::from_pipeline_state(state));
        }

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
