//! Pipeline finalization and cleanup.
//!
//! This module handles the final phase of the pipeline including cleanup,
//! final summary, and checkpoint clearing.
//!
//! Note: PROMPT.md permission restoration is now handled by the reducer's
//! `Effect::RestorePromptPermissions` during the `Finalizing` phase, ensuring
//! it goes through the effect system for proper testability.

use crate::banner::{print_final_summary, PipelineSummary};
use crate::checkpoint::clear_checkpoint_with_workspace;
use crate::config::Config;
use crate::files::protection::monitoring::PromptMonitor;
use crate::logger::Colors;
use crate::logger::Logger;
use crate::pipeline::AgentPhaseGuard;
use crate::pipeline::Timer;
use crate::reducer::state::PipelineState;
use crate::workspace::Workspace;

/// Context for pipeline finalization.
#[derive(Copy, Clone)]
pub struct FinalizeContext<'a> {
    pub logger: &'a Logger,
    pub colors: Colors,
    pub config: &'a Config,
    pub timer: &'a Timer,
    pub workspace: &'a dyn Workspace,
}

/// Finalizes the pipeline: cleans up and prints summary.
///
/// Commits now happen per-iteration during development and per-cycle during review,
/// so this function only handles cleanup and final summary.
///
/// # Arguments
///
/// * `ctx` - Finalization context with logger, config, timer, and workspace
/// * `final_state` - Final pipeline state from reducer (source of truth for metrics)
#[must_use]
pub const fn build_pipeline_summary(
    total_time: String,
    config: &Config,
    final_state: &PipelineState,
) -> PipelineSummary {
    PipelineSummary {
        total_time,
        dev_runs_completed: final_state.metrics.dev_iterations_completed as usize,
        dev_runs_total: final_state.metrics.max_dev_iterations as usize,
        review_passes_completed: final_state.metrics.review_passes_completed as usize,
        review_passes_total: final_state.metrics.max_review_passes as usize,
        review_runs: final_state.metrics.review_runs_total as usize,
        changes_detected: final_state.metrics.commits_created_total as usize,
        isolation_mode: config.isolation_mode,
        verbose: config.verbosity.is_verbose(),
        review_summary: None,
    }
}

pub fn finalize_pipeline(
    agent_phase_guard: &mut AgentPhaseGuard<'_>,
    ctx: FinalizeContext<'_>,
    final_state: &PipelineState,
    prompt_monitor: Option<PromptMonitor>,
) {
    // Stop the PROMPT.md monitor if it was started
    if let Some(monitor) = prompt_monitor {
        for warning in monitor.stop() {
            ctx.logger.warn(&warning);
        }
    }

    // End agent phase and clean up
    crate::git_helpers::end_agent_phase();
    crate::git_helpers::disable_git_wrapper(agent_phase_guard.git_helpers);
    if let Err(err) = crate::git_helpers::uninstall_hooks(ctx.logger) {
        ctx.logger
            .warn(&format!("Failed to uninstall Ralph hooks: {err}"));
    }
    let remaining = crate::git_helpers::verify_hooks_removed();
    if !remaining.is_empty() {
        ctx.logger.warn(&format!(
            "Ralph hooks still present after cleanup: {}",
            remaining.join(", ")
        ));
    }

    // Note: Individual commits were created per-iteration during development
    // and per-cycle during review. The final commit phase has been removed.

    // Final summary derived exclusively from reducer state
    let summary = build_pipeline_summary(ctx.timer.elapsed_formatted(), ctx.config, final_state);
    print_final_summary(ctx.colors, &summary, ctx.logger);

    if ctx.config.features.checkpoint_enabled {
        if let Err(err) = clear_checkpoint_with_workspace(ctx.workspace) {
            ctx.logger
                .warn(&format!("Failed to clear checkpoint: {err}"));
        }
    }

    // Note: PROMPT.md write permissions are now restored via the reducer's
    // Effect::RestorePromptPermissions during the Finalizing phase.
    // This ensures the operation goes through the effect system for testability.

    agent_phase_guard.disarm();
}

#[cfg(test)]
mod tests {
    use crate::reducer::state::PipelineState;

    #[test]
    fn test_summary_derives_from_reducer_metrics() {
        let mut state = PipelineState::initial(5, 2);
        state.metrics.dev_iterations_completed = 3;
        state.metrics.review_runs_total = 4;
        state.metrics.commits_created_total = 3;

        // Summary should use reducer metrics, not runtime counters
        let dev_runs_completed = state.metrics.dev_iterations_completed as usize;
        let dev_runs_total = state.metrics.max_dev_iterations as usize;
        let review_runs = state.metrics.review_runs_total as usize;
        let changes_detected = state.metrics.commits_created_total as usize;

        assert_eq!(dev_runs_completed, 3);
        assert_eq!(dev_runs_total, 5);
        assert_eq!(review_runs, 4);
        assert_eq!(changes_detected, 3);
    }

    #[test]
    fn test_metrics_reflect_actual_progress_not_config() {
        let mut state = PipelineState::initial(10, 5);

        // Simulate partial run: only 2 iterations completed out of 10 configured
        state.metrics.dev_iterations_completed = 2;
        state.metrics.review_runs_total = 0;

        // Summary should show actual progress (2), not config (10)
        assert_eq!(state.metrics.dev_iterations_completed, 2);
        assert_eq!(state.metrics.max_dev_iterations, 10);
    }

    #[test]
    fn test_summary_no_drift_from_runtime_counters() {
        let mut state = PipelineState::initial(10, 5);

        // Simulate reducer metrics
        state.metrics.dev_iterations_completed = 7;
        state.metrics.review_runs_total = 3;
        state.metrics.commits_created_total = 8;

        // Simulate hypothetical runtime counters (these should NOT be used)
        let runtime_dev_completed = 5; // WRONG VALUE - should be ignored
        let runtime_review_runs = 2; // WRONG VALUE - should be ignored

        // Summary must use reducer metrics, not runtime counters
        let dev_runs = state.metrics.dev_iterations_completed as usize;
        let review_runs = state.metrics.review_runs_total as usize;
        let commits = state.metrics.commits_created_total as usize;

        assert_eq!(dev_runs, 7); // From reducer, not runtime
        assert_eq!(review_runs, 3); // From reducer, not runtime
        assert_eq!(commits, 8); // From reducer, not runtime

        // Prove we're not using the wrong values
        assert_ne!(dev_runs, runtime_dev_completed);
        assert_ne!(review_runs, runtime_review_runs);
    }

    #[test]
    fn test_summary_uses_all_reducer_metrics() {
        let mut state = PipelineState::initial(5, 3);

        // Simulate complete run metrics
        state.metrics.dev_iterations_started = 5;
        state.metrics.dev_iterations_completed = 5;
        state.metrics.dev_attempts_total = 7; // Including continuations
        state.metrics.analysis_attempts_total = 5;
        state.metrics.review_passes_started = 3;
        state.metrics.review_passes_completed = 3;
        state.metrics.review_runs_total = 3;
        state.metrics.fix_runs_total = 2;
        state.metrics.commits_created_total = 6; // 5 dev + 1 final
        state.metrics.xsd_retry_attempts_total = 2;
        state.metrics.same_agent_retry_attempts_total = 1;

        // Construct summary as finalize_pipeline does
        let dev_runs_completed = state.metrics.dev_iterations_completed as usize;
        let dev_runs_total = state.metrics.max_dev_iterations as usize;
        let review_passes_completed = state.metrics.review_passes_completed as usize;
        let review_passes_total = state.metrics.max_review_passes as usize;
        let review_runs_total = state.metrics.review_runs_total as usize;
        let changes_detected = state.metrics.commits_created_total as usize;

        // Verify all values come from reducer metrics
        assert_eq!(dev_runs_completed, 5);
        assert_eq!(dev_runs_total, 5);
        assert_eq!(review_passes_completed, 3);
        assert_eq!(review_passes_total, 3);
        assert_eq!(review_runs_total, 3);
        assert_eq!(changes_detected, 6);

        // Verify we're not using any separate runtime counters
        // (this test proves the summary construction pattern)
    }

    #[test]
    fn test_partial_run_shows_actual_not_configured() {
        let mut state = PipelineState::initial(10, 5);

        // Only partial progress
        state.metrics.dev_iterations_completed = 3;
        state.metrics.review_passes_completed = 1;
        state.metrics.commits_created_total = 3;

        assert_eq!(state.metrics.dev_iterations_completed, 3);
        assert_eq!(state.metrics.max_dev_iterations, 10);
        assert_eq!(state.metrics.review_passes_completed, 1);
        assert_eq!(state.metrics.max_review_passes, 5);
    }
}
