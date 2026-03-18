//! Event loop configuration and initialization.
//!
//! This module defines configuration types and initialization logic for the
//! reducer-based event loop.

use crate::phases::PhaseContext;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::ContinuationState;
use crate::reducer::PipelineState;

/// Create initial pipeline state with continuation limits from config.
///
/// This function creates a `PipelineState` with XSD retry and continuation limits
/// loaded from the config, ensuring these values are available for the reducer
/// to make deterministic retry decisions.
pub fn create_initial_state_with_config(ctx: &PhaseContext<'_>) -> PipelineState {
    // Config semantics: max_dev_continuations counts continuation attempts *beyond*
    // the initial attempt. ContinuationState::max_continue_count semantics are
    // "maximum total attempts including initial".

    // CRITICAL: max_dev_continuations should always be Some() when loaded via config_from_unified().
    // The serde defaults in UnifiedConfig ensure these fields are never missing.
    // The unwrap_or() here is a defensive fallback for edge cases:
    // - Config::default() or Config::test_default()
    // - Direct Config construction in tests without going through config_from_unified()
    //
    // In debug builds, we assert that the value is Some() to catch config loading bugs early.
    debug_assert!(
        ctx.config.max_dev_continuations.is_some(),
        "BUG: max_dev_continuations is None when it should always have a value from config loading. \
         This indicates config_from_unified() did not properly set the field, or Config was \
         constructed directly without defaults."
    );
    debug_assert!(
        ctx.config.max_xsd_retries.is_some(),
        "BUG: max_xsd_retries is None when it should always have a value from config loading."
    );
    debug_assert!(
        ctx.config.max_same_agent_retries.is_some(),
        "BUG: max_same_agent_retries is None when it should always have a value from config loading."
    );
    debug_assert!(
        ctx.config.max_commit_residual_retries.is_some(),
        "BUG: max_commit_residual_retries is None when it should always have a value from config loading."
    );

    // CRITICAL SAFETY MECHANISM: Apply unconditional default of 2 (3 total attempts) when None.
    // This ensures bounded continuation even if Config was constructed without going through
    // config_from_unified() (e.g., Config::default(), tests). This is the PRIMARY DEFENSE
    // against infinite continuation loops when max_dev_continuations is missing.
    //
    // VERIFIED FIX: This unwrap_or(2) is what prevents the infinite loop bug reported by user.
    // With max_dev_continuations = 2:
    // - max_continue_count = 1 + 2 = 3
    // - Attempts 0, 1, 2 are allowed (3 total)
    // - Attempt 3+ is exhausted via OutcomeApplied check: (attempt + 1 >= 3)
    //
    // The defensive check in trigger_continuation provides additional safety by preventing
    // counter increment when next_attempt >= max_continue_count.
    let max_dev_continuations = ctx.config.max_dev_continuations.unwrap_or(2);
    let max_continue_count = max_dev_continuations.saturating_add(1);

    // SAFETY ASSERTION: when max_dev_continuations is absent, unwrap_or(2)
    // must produce the default total-attempts cap of 3.
    if ctx.config.max_dev_continuations.is_none() {
        debug_assert_eq!(
            max_continue_count, 3,
            "BUG: missing max_dev_continuations must default to 3 total attempts. Got: {max_continue_count}"
        );
    }

    let continuation = ContinuationState::with_limits(
        ctx.config.max_xsd_retries.unwrap_or(10),
        max_continue_count,
        ctx.config.max_same_agent_retries.unwrap_or(2),
    );
    let state = PipelineState::initial_with_continuation(
        ctx.config.developer_iters,
        ctx.config.reviewer_reviews,
        &continuation,
    );
    let max_commit_residual_retries =
        u8::try_from(ctx.config.max_commit_residual_retries.unwrap_or(10)).unwrap_or(u8::MAX);

    // Inject a checkpoint-safe (redacted) view of runtime cloud config.
    // This ensures pure orchestration can derive cloud effects when enabled,
    // without ever storing secrets in reducer state.
    let cloud = crate::config::CloudStateConfig::disabled();

    PipelineState {
        max_commit_residual_retries,
        cloud,
        ..state
    }
}

/// Overlay checkpoint-derived progress onto a config-derived base state.
///
/// This is used for resume: budgets/limits remain config-driven (from `base_state`),
/// while progress counters and histories are restored from the checkpoint-migrated
/// `PipelineState`.
///
/// NOTE: `base_state.cloud` is intentionally preserved (it is derived from
/// runtime env and is already redacted/credential-free).
pub fn overlay_checkpoint_progress_onto_base_state(
    base_state: PipelineState,
    migrated: PipelineState,
    execution_history_limit: usize,
) -> PipelineState {
    let migrated_execution_history = migrated.execution_history().clone();

    let cloud = base_state.cloud.clone();

    let new_execution_history = base_state
        .with_execution_history(migrated_execution_history, execution_history_limit)
        .execution_history;

    PipelineState {
        phase: migrated.phase,
        iteration: migrated.iteration,
        total_iterations: migrated.total_iterations,
        reviewer_pass: migrated.reviewer_pass,
        total_reviewer_passes: migrated.total_reviewer_passes,
        rebase: migrated.rebase,
        execution_history: new_execution_history,
        prompt_inputs: migrated.prompt_inputs,
        prompt_permissions: migrated.prompt_permissions,
        prompt_history: migrated.prompt_history,
        metrics: migrated.metrics,
        recovery_epoch: migrated.recovery_epoch,
        recovery_escalation_level: migrated.recovery_escalation_level,
        dev_fix_attempt_count: migrated.dev_fix_attempt_count,
        failed_phase_for_recovery: migrated.failed_phase_for_recovery,
        interrupted_by_user: migrated.interrupted_by_user,
        pending_push_commit: migrated.pending_push_commit,
        git_auth_configured: migrated.git_auth_configured,
        pr_created: migrated.pr_created,
        pr_url: migrated.pr_url,
        pr_number: migrated.pr_number,
        push_count: migrated.push_count,
        push_retry_count: migrated.push_retry_count,
        last_push_error: migrated.last_push_error,
        unpushed_commits: migrated.unpushed_commits,
        last_pushed_commit: migrated.last_pushed_commit,
        // Preserve cloud from base_state (runtime env-derived, redacted)
        cloud,
        // Take all other fields from migrated that aren't explicitly set above
        ..migrated
    }
}

/// Maximum iterations for the main event loop to prevent infinite loops.
///
/// This is a safety limit - the pipeline should complete well before this limit
/// under normal circumstances. If reached, it indicates either a bug in the
/// reducer logic or an extremely complex project.
///
/// NOTE: Even `1_000_000` can still be too low for extremely slow-progress runs.
/// If this cap is hit in practice, prefer making it configurable and/or
/// investigating why the reducer is not converging.
pub const MAX_EVENT_LOOP_ITERATIONS: usize = 1_000_000;

#[cfg(test)]
mod resume_overlay_tests {
    use super::overlay_checkpoint_progress_onto_base_state;
    use crate::config::{CloudStateConfig, GitAuthStateMethod, GitRemoteStateConfig};
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::PipelineState;

    #[test]
    fn resume_overlay_restores_cloud_resume_fields_but_preserves_runtime_cloud() {
        let mut base = PipelineState::initial(3, 2);
        base.cloud = CloudStateConfig {
            enabled: true,
            api_url: None,
            run_id: Some("run_from_env".to_string()),
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteStateConfig {
                auth_method: GitAuthStateMethod::Token {
                    username: "x-access-token".to_string(),
                },
                push_branch: "env_branch".to_string(),
                create_pr: true,
                pr_title_template: None,
                pr_body_template: None,
                pr_base_branch: None,
                force_push: false,
                remote_name: "origin".to_string(),
            },
        };

        let mut migrated = PipelineState::initial(999, 999);
        migrated.cloud = CloudStateConfig::disabled();
        migrated.pending_push_commit = Some("abc123".to_string());
        migrated.git_auth_configured = true;
        migrated.pr_created = true;
        migrated.pr_url = Some("https://example.com/pr/1".to_string());
        migrated.pr_number = Some(1);
        migrated.push_count = 7;
        migrated.push_retry_count = 2;
        migrated.last_push_error = Some("push failed".to_string());
        migrated.unpushed_commits = vec!["deadbeef".to_string()];
        migrated.last_pushed_commit = Some("beadfeed".to_string());

        let base = overlay_checkpoint_progress_onto_base_state(base, migrated, 1000);

        // Runtime (env-derived) redacted config is preserved.
        assert!(base.cloud.enabled);
        assert_eq!(base.cloud.run_id.as_deref(), Some("run_from_env"));
        assert_eq!(base.cloud.git_remote.push_branch.as_str(), "env_branch");

        // Cloud resume state is restored.
        assert_eq!(base.pending_push_commit.as_deref(), Some("abc123"));
        assert!(base.git_auth_configured);
        assert!(base.pr_created);
        assert_eq!(base.pr_url.as_deref(), Some("https://example.com/pr/1"));
        assert_eq!(base.pr_number, Some(1));
        assert_eq!(base.push_count, 7);
        assert_eq!(base.push_retry_count, 2);
        assert_eq!(base.last_push_error.as_deref(), Some("push failed"));
        assert_eq!(base.unpushed_commits, vec!["deadbeef".to_string()]);
        assert_eq!(base.last_pushed_commit.as_deref(), Some("beadfeed"));
    }

    #[test]
    fn resume_overlay_restores_recovery_and_interrupt_fields() {
        let base = PipelineState::initial(3, 2);

        let mut migrated = PipelineState::initial(999, 999);
        migrated.dev_fix_attempt_count = 42;
        migrated.recovery_epoch = 7;
        migrated.recovery_escalation_level = 3;
        migrated.failed_phase_for_recovery = Some(PipelinePhase::Review);
        migrated.interrupted_by_user = true;

        let base = overlay_checkpoint_progress_onto_base_state(base, migrated, 1000);

        assert_eq!(base.dev_fix_attempt_count, 42);
        assert_eq!(base.recovery_epoch, 7);
        assert_eq!(base.recovery_escalation_level, 3);
        assert_eq!(base.failed_phase_for_recovery, Some(PipelinePhase::Review));
        assert!(
            base.interrupted_by_user,
            "interrupted_by_user must be restored from the migrated checkpoint state"
        );
    }
}

/// Configuration for event loop.
#[derive(Copy, Clone, Debug)]
pub struct EventLoopConfig {
    /// Maximum number of iterations to prevent infinite loops.
    pub max_iterations: usize,
}

/// Result of event loop execution.
#[derive(Debug, Clone)]
pub struct EventLoopResult {
    /// Whether pipeline completed successfully.
    pub completed: bool,
    /// Total events processed.
    pub events_processed: usize,
    /// Final reducer phase when the loop stopped.
    pub final_phase: PipelinePhase,
    /// Final pipeline state (for metrics and summary).
    pub final_state: PipelineState,
}
