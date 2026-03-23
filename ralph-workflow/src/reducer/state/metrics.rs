// Run-level execution metrics for the pipeline.
//
// This is the single source of truth for all iteration/attempt/retry/fallback statistics.
//
// # Where Metrics Are Updated
//
// Metrics are updated **only** in reducer code paths (`state_reduction/*.rs`):
//
// - `development.rs`: dev_iterations_started, dev_iterations_completed,
//                     dev_attempts_total, dev_continuation_attempt,
//                     analysis_attempts_*, xsd_retry_development
// - `review.rs`: review_passes_started, review_passes_completed, review_runs_total,
//                fix_runs_total, fix_continuations_total, fix_continuation_attempt,
//                current_review_pass, xsd_retry_review, xsd_retry_fix
// - `commit.rs`: commits_created_total, xsd_retry_commit
// - `planning.rs`: xsd_retry_planning
// - `agent.rs`: same_agent_retry_attempts_total, agent_fallbacks_total,
//               model_fallbacks_total, retry_cycles_started_total
//
// # Event-to-Metric Mapping
//
// | Metric                              | Incremented On Event                                      | Notes                                    |
// |-------------------------------------|-----------------------------------------------------------|------------------------------------------|
// | dev_iterations_started              | DevelopmentEvent::IterationStarted                        | Not incremented on continuations         |
// | dev_iterations_completed            | DevelopmentEvent::IterationCompleted { output_valid: true } | Advanced to commit phase                |
// |                                     | DevelopmentEvent::ContinuationSucceeded                   | Continuation advanced to commit phase    |
// | dev_attempts_total                  | DevelopmentEvent::AgentInvoked                            | Includes initial + continuations         |
// | dev_continuation_attempt            | DevelopmentEvent::ContinuationTriggered                   | Reset on IterationStarted                |
// | analysis_attempts_total             | DevelopmentEvent::AnalysisAgentInvoked                    | Total across all iterations              |
// | analysis_attempts_in_current_iteration | DevelopmentEvent::AnalysisAgentInvoked                 | Reset on IterationStarted                |
// | review_passes_started               | ReviewEvent::PassStarted                                  | Increments when pass != previous         |
// | review_passes_completed             | ReviewEvent::Completed { issues_found: false }            | Clean pass                               |
// |                                     | ReviewEvent::PassCompletedClean                           | Alternative event for clean pass         |
// |                                     | ReviewEvent::FixAttemptCompleted                          | Fix completed, pass advances             |
// | review_runs_total                   | ReviewEvent::AgentInvoked                                 | Total reviewer invocations               |
// | fix_runs_total                      | ReviewEvent::FixAgentInvoked                              | Total fix invocations                    |
// | fix_continuations_total             | ReviewEvent::FixContinuationTriggered                     | Fix continuation attempts                |
// | fix_continuation_attempt            | ReviewEvent::FixContinuationTriggered                     | Reset on PassStarted                     |
// | current_review_pass                 | ReviewEvent::PassStarted                                  | Tracks current pass number               |
// | xsd_retry_*                         | *Event::OutputValidationFailed (when will_retry == true)  | Only when retrying, not when exhausted   |
// | same_agent_retry_attempts_total     | AgentEvent::TimedOut / InternalError (when will_retry)    | Only when retrying same agent            |
// | timeout_no_output_agent_switches_total | AgentEvent::TimedOut { output_kind: NoOutput }         | NoOutput timeout triggered immediate switch |
// | agent_fallbacks_total               | AgentEvent::FallbackTriggered                             | Agent switched in chain                  |
// | model_fallbacks_total               | AgentEvent::ModelFallbackTriggered                        | Model switched for agent                 |
// | retry_cycles_started_total          | AgentEvent::RetryCycleStarted                             | Chain exhausted, restarting              |
// | commits_created_total               | CommitEvent::Created                                      | Actual git commit created                |
//
// # How to Add New Metrics
//
// 1. Add field to `RunMetrics` struct with `#[serde(default)]`
// 2. Update `RunMetrics::new()` if config-derived display field
// 3. Update appropriate reducer in `state_reduction/` to increment on event
// 4. Add unit test in `state_reduction/tests/metrics.rs`
// 5. Update `finalize_pipeline()` if displayed in final summary
// 6. Add checkpoint compatibility test
//
// # Checkpoint Compatibility
//
// All fields have `#[serde(default)]` to ensure old checkpoints can be loaded
// with new metrics fields defaulting to 0.

/// Run-level execution metrics tracked by the reducer.
///
/// This struct provides a complete picture of pipeline execution progress,
/// including iteration counts, attempt counts, retry counts, and fallback events.
/// All fields are monotonic counters that only increment during a run.
///
/// # Checkpoint Compatibility
///
/// All fields have `#[serde(default)]` to ensure backward compatibility when
/// loading checkpoints created before metrics were added or when new fields
/// are introduced in future versions.
///
/// # Single Source of Truth
///
/// The reducer is the **only** code that mutates these metrics. They are
/// updated deterministically based on events, ensuring:
/// - Metrics survive checkpoint/resume
/// - No drift between runtime state and actual progress
/// - Final summary is always consistent with reducer state
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct RunMetrics {
    // Development iteration tracking
    /// Number of development iterations started.
    /// Incremented on `DevelopmentEvent::IterationStarted` (not on continuations).
    #[serde(default)]
    pub dev_iterations_started: u32,
    /// Number of development iterations completed (advanced to commit phase).
    /// A dev iteration is "completed" when the reducer transitions to `PipelinePhase::CommitMessage`
    /// after dev output is valid, regardless of whether an actual git commit is created.
    /// Incremented on `DevelopmentEvent::IterationCompleted { output_valid: true }` and
    /// `DevelopmentEvent::ContinuationSucceeded`.
    #[serde(default)]
    pub dev_iterations_completed: u32,
    /// Total number of developer agent invocations (includes continuations).
    #[serde(default)]
    pub dev_attempts_total: u32,
    /// Current continuation attempt within the current development iteration (0 = initial).
    /// Reset when starting a new iteration.
    #[serde(default)]
    pub dev_continuation_attempt: u32,

    // Analysis tracking
    /// Total number of analysis agent invocations across all iterations.
    #[serde(default)]
    pub analysis_attempts_total: u32,
    /// Analysis attempts in the current development iteration (reset per iteration).
    #[serde(default)]
    pub analysis_attempts_in_current_iteration: u32,

    // Review tracking
    /// Number of review passes started.
    /// Incremented on `ReviewEvent::PassStarted` when `pass != previous_pass`.
    #[serde(default)]
    pub review_passes_started: u32,
    /// Number of review passes completed (advanced past without issues or after fixes).
    /// A review pass is "completed" when it advances to the next pass or to commit phase,
    /// either because no issues were found or because fixes were successfully applied.
    /// Incremented on `ReviewEvent::Completed { issues_found: false }`,
    /// `ReviewEvent::PassCompletedClean`, and `ReviewEvent::FixAttemptCompleted`.
    #[serde(default)]
    pub review_passes_completed: u32,
    /// Total number of reviewer agent invocations.
    #[serde(default)]
    pub review_runs_total: u32,
    /// Total number of fix agent invocations.
    #[serde(default)]
    pub fix_runs_total: u32,
    /// Total number of fix analysis agent invocations.
    ///
    /// This tracks the independent verification step after every fix agent invocation.
    #[serde(default)]
    pub fix_analysis_runs_total: u32,
    /// Total number of fix continuation attempts.
    #[serde(default)]
    pub fix_continuations_total: u32,
    /// Current fix continuation attempt within the current review pass (0 = initial).
    ///
    /// Reset when starting a new review pass.
    /// Note: fix-attempt boundaries do not reset this counter; it is scoped to the review pass.
    #[serde(default)]
    pub fix_continuation_attempt: u32,
    /// Current review pass number (for X/Y display).
    #[serde(default)]
    pub current_review_pass: u32,

    // XSD retry tracking
    /// Total XSD retry attempts across all phases.
    #[serde(default)]
    pub xsd_retry_attempts_total: u32,
    /// XSD retry attempts in planning phase.
    #[serde(default)]
    pub xsd_retry_planning: u32,
    /// XSD retry attempts in development/analysis phase.
    #[serde(default)]
    pub xsd_retry_development: u32,
    /// XSD retry attempts in review phase.
    #[serde(default)]
    pub xsd_retry_review: u32,
    /// XSD retry attempts in fix phase.
    #[serde(default)]
    pub xsd_retry_fix: u32,
    /// XSD retry attempts in commit phase.
    #[serde(default)]
    pub xsd_retry_commit: u32,

    // Same-agent retry tracking
    /// Total same-agent retry attempts (for transient failures like timeout).
    #[serde(default)]
    pub same_agent_retry_attempts_total: u32,

    /// Agent switches caused by no-output timeouts.
    ///
    /// Incremented only in the reducer's `TimedOut { output_kind: NoOutput }` arm.
    /// Distinct from `agent_fallbacks_total` (which tracks `FallbackTriggered` events).
    /// Distinct from `same_agent_retry_attempts_total` (`NoOutput` does not retry same agent).
    #[serde(default)]
    pub timeout_no_output_agent_switches_total: u32,

    // Agent/model fallback tracking
    /// Total agent fallback events.
    #[serde(default)]
    pub agent_fallbacks_total: u32,
    /// Total model fallback events.
    #[serde(default)]
    pub model_fallbacks_total: u32,
    /// Total retry cycles started (agent chain exhaustion + restart).
    #[serde(default)]
    pub retry_cycles_started_total: u32,

    // Commit tracking
    /// Total commits created during the run.
    #[serde(default)]
    pub commits_created_total: u32,

    // Config-derived display fields (set once at init, not serialized from events)
    /// Maximum development iterations (from config, for X/Y display).
    #[serde(default)]
    pub max_dev_iterations: u32,
    /// Maximum review passes (from config, for X/Y display).
    #[serde(default)]
    pub max_review_passes: u32,
    /// Maximum XSD retry count (from config, for X/max display).
    #[serde(default)]
    pub max_xsd_retry_count: u32,
    /// Maximum development continuation count (from config, for X/max display).
    #[serde(default)]
    pub max_dev_continuation_count: u32,
    /// Maximum fix continuation count (from config, for X/max display).
    #[serde(default)]
    pub max_fix_continuation_count: u32,
    /// Maximum same-agent retry count (from config, for X/max display).
    #[serde(default)]
    pub max_same_agent_retry_count: u32,
}

impl RunMetrics {
    /// Create metrics with config-derived display fields.
    #[must_use]
    pub fn new(
        max_dev_iterations: u32,
        max_review_passes: u32,
        continuation: &ContinuationState,
    ) -> Self {
        Self {
            max_dev_iterations,
            max_review_passes,
            max_xsd_retry_count: continuation.max_xsd_retry_count,
            max_dev_continuation_count: continuation.max_continue_count,
            max_fix_continuation_count: continuation.max_fix_continue_count,
            max_same_agent_retry_count: continuation.max_same_agent_retry_count,
            ..Self::default()
        }
    }

    // =========================================================================
    // Builder methods for updating metrics without full struct clones
    // =========================================================================
    // These methods consume self and return a new instance with the updated field.
    // This eliminates the need to clone all 40+ fields when updating a single metric.

    // Development metrics
    #[must_use]
    pub const fn increment_dev_iterations_started(self) -> Self {
        Self { dev_iterations_started: self.dev_iterations_started.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_dev_iterations_completed(self) -> Self {
        Self { dev_iterations_completed: self.dev_iterations_completed.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_dev_attempts_total(self) -> Self {
        Self { dev_attempts_total: self.dev_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_dev_continuation_attempt(self) -> Self {
        Self { dev_continuation_attempt: self.dev_continuation_attempt.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn reset_dev_continuation_attempt(self) -> Self {
        Self { dev_continuation_attempt: 0, ..self }
    }

    // Analysis metrics
    #[must_use]
    pub const fn increment_analysis_attempts_total(self) -> Self {
        Self { analysis_attempts_total: self.analysis_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_analysis_attempts_in_current_iteration(self) -> Self {
        Self { analysis_attempts_in_current_iteration: self.analysis_attempts_in_current_iteration.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn reset_analysis_attempts_in_current_iteration(self) -> Self {
        Self { analysis_attempts_in_current_iteration: 0, ..self }
    }

    // Review metrics
    #[must_use]
    pub const fn increment_review_passes_started(self) -> Self {
        Self { review_passes_started: self.review_passes_started.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_review_passes_completed(self) -> Self {
        Self { review_passes_completed: self.review_passes_completed.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_review_runs_total(self) -> Self {
        Self { review_runs_total: self.review_runs_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_fix_runs_total(self) -> Self {
        Self { fix_runs_total: self.fix_runs_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_fix_analysis_runs_total(self) -> Self {
        Self { fix_analysis_runs_total: self.fix_analysis_runs_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_fix_continuations_total(self) -> Self {
        Self { fix_continuations_total: self.fix_continuations_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_fix_continuation_attempt(self) -> Self {
        Self { fix_continuation_attempt: self.fix_continuation_attempt.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn reset_fix_continuation_attempt(self) -> Self {
        Self { fix_continuation_attempt: 0, ..self }
    }

    #[must_use]
    pub const fn set_current_review_pass(self, pass: u32) -> Self {
        Self { current_review_pass: pass, ..self }
    }

    // XSD retry metrics
    #[must_use]
    pub const fn increment_xsd_retry_attempts_total(self) -> Self {
        Self { xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_xsd_retry_planning(self) -> Self {
        Self { xsd_retry_planning: self.xsd_retry_planning.saturating_add(1), xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_xsd_retry_development(self) -> Self {
        Self { xsd_retry_development: self.xsd_retry_development.saturating_add(1), xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_xsd_retry_review(self) -> Self {
        Self { xsd_retry_review: self.xsd_retry_review.saturating_add(1), xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_xsd_retry_fix(self) -> Self {
        Self { xsd_retry_fix: self.xsd_retry_fix.saturating_add(1), xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_xsd_retry_commit(self) -> Self {
        Self { xsd_retry_commit: self.xsd_retry_commit.saturating_add(1), xsd_retry_attempts_total: self.xsd_retry_attempts_total.saturating_add(1), ..self }
    }

    // Same-agent retry metrics
    #[must_use]
    pub const fn increment_same_agent_retry_attempts_total(self) -> Self {
        Self { same_agent_retry_attempts_total: self.same_agent_retry_attempts_total.saturating_add(1), ..self }
    }

    /// Increment `timeout_no_output_agent_switches_total` counter.
    ///
    /// Called only when a `TimedOut { output_kind: NoOutput }` event triggers
    /// an immediate agent switch.
    #[must_use]
    pub const fn increment_timeout_no_output_agent_switches_total(self) -> Self {
        Self { timeout_no_output_agent_switches_total: self.timeout_no_output_agent_switches_total.saturating_add(1), ..self }
    }

    // Agent/model fallback metrics
    #[must_use]
    pub const fn increment_agent_fallbacks_total(self) -> Self {
        Self { agent_fallbacks_total: self.agent_fallbacks_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_model_fallbacks_total(self) -> Self {
        Self { model_fallbacks_total: self.model_fallbacks_total.saturating_add(1), ..self }
    }

    #[must_use]
    pub const fn increment_retry_cycles_started_total(self) -> Self {
        Self { retry_cycles_started_total: self.retry_cycles_started_total.saturating_add(1), ..self }
    }

    // Commit metrics
    #[must_use]
    pub const fn increment_commits_created_total(self) -> Self {
        Self { commits_created_total: self.commits_created_total.saturating_add(1), ..self }
    }
}
