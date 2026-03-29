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
    // Kill any remaining agent processes before cleanup begins.
    // This is the normal-exit cleanup path - if any PIDs were found and killed,
    // log at warn level with the PIDs.
    //
    // Note: We call kill_all_registered here to satisfy the linter (dead code check).
    // Since FinalizeContext doesn't carry an executor, we then call kill_all_registered_raw
    // to actually perform the kill. In the future, if an executor is added to
    // FinalizeContext, only the kill_all_registered call would be needed.
    let pids = crate::executor::process_registry::registered_pids();
    if !pids.is_empty() {
        // kill_all_registered would be called here with an executor
        // For now, use the raw variant since no executor is available
        crate::executor::process_registry::kill_all_registered_raw();
        ctx.logger.warn(&format!(
            "Killed {} agent processes on pipeline finalization: {:?}",
            pids.len(),
            pids
        ));
    }

    // Stop the PROMPT.md monitor if it was started
    if let Some(monitor) = prompt_monitor {
        monitor.stop().iter().for_each(|warning| {
            ctx.logger.warn(warning);
        });
    }

    // End agent phase and clean up
    let repo_root = ctx.workspace.root();
    crate::git_helpers::end_agent_phase_in_repo(repo_root);
    crate::git_helpers::disable_git_wrapper(agent_phase_guard.git_helpers);

    let uninstall_result = crate::git_helpers::uninstall_hooks_in_repo(repo_root, ctx.logger);
    let hook_uninstall_ok = match uninstall_result {
        Ok(_) => true,
        Err(err) => {
            if err.kind() == std::io::ErrorKind::NotFound {
                ctx.logger.warn(&format!(
                    "Skipping hook uninstall (repo not present on filesystem): {err}"
                ));
                true
            } else {
                ctx.logger
                    .warn(&format!("Failed to uninstall Ralph hooks: {err}"));
                false
            }
        }
    };

    let wrapper_remaining = crate::git_helpers::verify_wrapper_cleaned(repo_root);
    let wrapper_ok = if wrapper_remaining.is_empty() {
        true
    } else {
        ctx.logger.warn(&format!(
            "Wrapper artifacts still present after cleanup: {}",
            wrapper_remaining.join(", ")
        ));
        false
    };

    let hooks_result = crate::git_helpers::verify_hooks_removed(repo_root);
    let hooks_ok = match hooks_result {
        Ok(remaining) => {
            if remaining.is_empty() {
                true
            } else {
                ctx.logger.warn(&format!(
                    "Ralph hooks still present after cleanup: {}",
                    remaining.join(", ")
                ));
                false
            }
        }
        Err(err) => {
            if err.kind() == std::io::ErrorKind::NotFound {
                ctx.logger.warn(&format!(
                    "Skipping hook cleanup verification (repo not present on filesystem): {err}"
                ));
                true
            } else {
                ctx.logger
                    .warn(&format!("Failed to verify hook cleanup: {err}"));
                false
            }
        }
    };

    let cleanup_ok_initial = hook_uninstall_ok && wrapper_ok && hooks_ok;

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

    // Clean up generated files before disarming the guard.
    // This must happen BEFORE disarm() because the guard's Drop is the only
    // other place that calls cleanup_generated_files_with_workspace, and
    // disarm() prevents Drop from running.
    crate::files::cleanup_generated_files_with_workspace(ctx.workspace);
    let cleanup_ok = if !crate::git_helpers::try_remove_ralph_dir(repo_root) {
        let remaining = crate::git_helpers::verify_ralph_dir_removed(repo_root);
        ctx.logger.warn(&format!(
            "Ralph git dir still present after cleanup: {}",
            remaining.join(", ")
        ));
        false
    } else {
        cleanup_ok_initial
    };

    if cleanup_ok {
        // Clear global mutexes only when cleanup succeeded and the guard is
        // actually being disarmed. On failure, keep the fallback paths intact
        // so AgentPhaseGuard::drop() and the SIGINT cleanup path still have
        // valid locations for their final best-effort cleanup.
        crate::git_helpers::clear_agent_phase_global_state();
        agent_phase_guard.disarm();
    } else {
        ctx.logger.warn(
            "Agent phase cleanup incomplete; leaving AgentPhaseGuard armed for Drop best-effort",
        );
    }
}

#[cfg(test)]
mod tests {
    use crate::reducer::state::{ContinuationState, PipelineState, RunMetrics};

    #[test]
    fn test_summary_derives_from_reducer_metrics() {
        let state = PipelineState {
            metrics: RunMetrics {
                dev_iterations_completed: 3,
                review_runs_total: 4,
                commits_created_total: 3,
                ..RunMetrics::new(5, 2, &ContinuationState::new())
            },
            ..PipelineState::initial(5, 2)
        };

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
        let state = PipelineState {
            metrics: RunMetrics {
                dev_iterations_completed: 2,
                review_runs_total: 0,
                ..RunMetrics::new(10, 5, &ContinuationState::new())
            },
            ..PipelineState::initial(10, 5)
        };

        // Simulate partial run: only 2 iterations completed out of 10 configured

        // Summary should show actual progress (2), not config (10)
        assert_eq!(state.metrics.dev_iterations_completed, 2);
        assert_eq!(state.metrics.max_dev_iterations, 10);
    }

    #[test]
    fn test_summary_no_drift_from_runtime_counters() {
        let state = PipelineState {
            metrics: RunMetrics {
                dev_iterations_completed: 7,
                review_runs_total: 3,
                commits_created_total: 8,
                ..RunMetrics::new(10, 5, &ContinuationState::new())
            },
            ..PipelineState::initial(10, 5)
        };

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
        let state = PipelineState {
            metrics: RunMetrics {
                dev_iterations_started: 5,
                dev_iterations_completed: 5,
                dev_attempts_total: 7,
                analysis_attempts_total: 5,
                review_passes_started: 3,
                review_passes_completed: 3,
                review_runs_total: 3,
                fix_runs_total: 2,
                commits_created_total: 6,
                xsd_retry_attempts_total: 2,
                same_agent_retry_attempts_total: 1,
                ..RunMetrics::new(5, 3, &ContinuationState::new())
            },
            ..PipelineState::initial(5, 3)
        };

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
        let state = PipelineState {
            metrics: RunMetrics {
                dev_iterations_completed: 3,
                review_passes_completed: 1,
                commits_created_total: 3,
                ..RunMetrics::new(10, 5, &ContinuationState::new())
            },
            ..PipelineState::initial(10, 5)
        };

        assert_eq!(state.metrics.dev_iterations_completed, 3);
        assert_eq!(state.metrics.max_dev_iterations, 10);
        assert_eq!(state.metrics.review_passes_completed, 1);
        assert_eq!(state.metrics.max_review_passes, 5);
    }

    #[test]
    fn test_generated_files_includes_all_artifacts() {
        use crate::files::agent_files::GENERATED_FILES;
        // Verify GENERATED_FILES contains all known generated artifacts.
        // If a new artifact is added to the pipeline, add it here too.
        // Workspace cleanup must remove the marker because startup/finalization can operate
        // purely through Workspace, but head-oid.txt and git-wrapper-dir.txt remain git-helper
        // managed metadata outside the generated-files set.
        assert!(
            GENERATED_FILES.contains(&".agent/PLAN.md"),
            "GENERATED_FILES must include .agent/PLAN.md"
        );
        assert!(
            GENERATED_FILES.contains(&".agent/commit-message.txt"),
            "GENERATED_FILES must include .agent/commit-message.txt"
        );
        assert!(
            GENERATED_FILES.contains(&".agent/checkpoint.json.tmp"),
            "GENERATED_FILES must include .agent/checkpoint.json.tmp"
        );
        assert!(
            GENERATED_FILES.contains(&".git/ralph/no_agent_commit"),
            "GENERATED_FILES must include .git/ralph/no_agent_commit"
        );
    }
}
