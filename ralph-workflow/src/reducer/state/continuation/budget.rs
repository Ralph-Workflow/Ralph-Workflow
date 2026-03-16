//! Budget tracking logic for continuation attempts.
//!
//! Provides methods for tracking and checking budget exhaustion for:
//! - XSD retries
//! - Same-agent retries
//! - Development continuations
//! - Fix continuations

use super::super::{ArtifactType, DevelopmentStatus, FixStatus, SameAgentRetryReason};
use super::state::ContinuationState;

impl ContinuationState {
    /// Set the current artifact type being processed.
    #[must_use]
    pub fn with_artifact(mut self, artifact: ArtifactType) -> Self {
        // Reset XSD retry state when switching artifacts, preserve everything else
        self.current_artifact = Some(artifact);
        self.xsd_retry_count = 0;
        self.xsd_retry_pending = false;
        self.xsd_retry_session_reuse_pending = false;
        self.last_xsd_error = None;
        self.last_review_xsd_error = None;
        self.last_fix_xsd_error = None;
        self
    }

    /// Mark XSD validation as failed, triggering a retry.
    ///
    /// For XSD retry, we want to re-invoke the same agent in the same session when possible,
    /// to keep retries deterministic and to preserve provider-side context.
    #[must_use]
    pub const fn trigger_xsd_retry(mut self) -> Self {
        self.xsd_retry_pending = true;
        self.xsd_retry_count = self.xsd_retry_count.saturating_add(1);
        self.xsd_retry_session_reuse_pending = true;
        self
    }

    /// Clear XSD retry pending flag after starting retry.
    #[must_use]
    pub fn clear_xsd_retry_pending(mut self) -> Self {
        self.xsd_retry_pending = false;
        self.last_xsd_error = None;
        self.last_review_xsd_error = None;
        self.last_fix_xsd_error = None;
        self
    }

    /// Check if XSD retries are exhausted.
    #[must_use]
    pub const fn xsd_retries_exhausted(&self) -> bool {
        self.xsd_retry_count >= self.max_xsd_retry_count
    }

    /// Mark a same-agent retry as pending for a transient invocation failure.
    #[must_use]
    pub const fn trigger_same_agent_retry(mut self, reason: SameAgentRetryReason) -> Self {
        self.same_agent_retry_pending = true;
        self.same_agent_retry_count = self.same_agent_retry_count.saturating_add(1);
        self.same_agent_retry_reason = Some(reason);
        self
    }

    /// Clear same-agent retry pending flag after starting retry.
    #[must_use]
    pub const fn clear_same_agent_retry_pending(mut self) -> Self {
        self.same_agent_retry_pending = false;
        self.same_agent_retry_reason = None;
        self
    }

    /// Check if same-agent retries are exhausted.
    #[must_use]
    pub const fn same_agent_retries_exhausted(&self) -> bool {
        self.same_agent_retry_count >= self.max_same_agent_retry_count
    }

    /// Mark continuation as pending (output valid but work incomplete).
    #[must_use]
    pub const fn trigger_continue(mut self) -> Self {
        self.continue_pending = true;
        self
    }

    /// Clear continue pending flag after starting continuation.
    #[must_use]
    pub const fn clear_continue_pending(mut self) -> Self {
        self.continue_pending = false;
        self
    }

    /// Check if continuation attempts are exhausted.
    ///
    /// Returns `true` when `continuation_attempt >= max_continue_count`.
    ///
    /// # Semantics
    ///
    /// The `continuation_attempt` counter tracks how many times work has been attempted:
    /// - 0: Initial attempt (before any continuation)
    /// - 1: After first continuation
    /// - 2: After second continuation
    /// - etc.
    ///
    /// With `max_continue_count = 3`:
    /// - Attempts 0, 1, 2 are allowed (3 total)
    /// - Attempt 3+ triggers exhaustion
    ///
    /// # Exhaustion Behavior
    ///
    /// When continuation budget is exhausted (`ContinuationBudgetExhausted` event):
    /// - If all agents exhausted AND status is Failed/Partial → transition to `AwaitingDevFix`
    /// - Otherwise → complete current iteration (via `IterationCompleted`) and advance to next iteration
    ///
    /// This ensures bounded execution: the system never restarts the continuation cycle
    /// with a fresh agent within the same iteration, preventing infinite loops when work
    /// remains incomplete despite exhausting the continuation budget.
    ///
    /// # Naming Note
    ///
    /// The field is named `max_continue_count` rather than `max_total_attempts` because
    /// it historically represented the maximum number of continuations. The actual
    /// semantics are "maximum total attempts including initial".
    #[must_use]
    pub const fn continuations_exhausted(&self) -> bool {
        self.continuation_attempt >= self.max_continue_count
    }

    /// Trigger a continuation with context from the previous attempt.
    ///
    /// Sets both `context_write_pending` (to write continuation context) and
    /// `continue_pending` (to trigger the continuation flow in orchestration).
    #[must_use]
    pub fn trigger_continuation(
        mut self,
        status: DevelopmentStatus,
        summary: String,
        files_changed: Option<Vec<String>>,
        next_steps: Option<String>,
    ) -> Self {
        self.previous_status = Some(status);
        self.previous_summary = Some(summary);
        self.previous_files_changed = files_changed.map(std::vec::Vec::into_boxed_slice);
        self.previous_next_steps = next_steps;

        // CRITICAL FIX: Check boundary BEFORE incrementing counter.
        // With max_continue_count = 3:
        // - At attempt 0: next_attempt = 1, 1 < 3 → continue (correct)
        // - At attempt 1: next_attempt = 2, 2 < 3 → continue (correct)
        // - At attempt 2: next_attempt = 3, 3 >= 3 → exhaust WITHOUT incrementing (correct)
        //
        // BEFORE FIX: Line 128 set continuation_attempt = next_attempt even when the defensive
        // check triggered, allowing the counter to reach the boundary value (3) instead of
        // staying below it (2). This caused the off-by-one bug where the system allowed
        // one extra attempt beyond the configured limit.
        //
        // AFTER FIX: When the defensive check triggers, we return immediately WITHOUT updating
        // continuation_attempt. The counter stays at 2, preventing the off-by-one bug.
        let next_attempt = self.continuation_attempt.saturating_add(1);
        if next_attempt >= self.max_continue_count {
            // At boundary: do not schedule another continuation AND do not increment counter.
            // The exhaustion check in OutcomeApplied (iteration_reducer.rs:169-171)
            // already emitted ContinuationBudgetExhausted, which will reset this counter.
            // Clearing these flags ensures orchestration doesn't try to continue.
            self.continue_pending = false;
            self.context_write_pending = false;
            self.context_cleanup_pending = false;
            return self;
        }

        self.continuation_attempt = next_attempt;
        self.invalid_output_attempts = 0;
        self.context_write_pending = true;
        self.context_cleanup_pending = false;
        // Reset XSD retry count for new continuation attempt
        self.xsd_retry_count = 0;
        self.xsd_retry_pending = false;
        self.xsd_retry_session_reuse_pending = false;
        self.last_xsd_error = None;
        self.last_review_xsd_error = None;
        self.last_fix_xsd_error = None;
        // Reset same-agent retry state for new continuation attempt
        self.same_agent_retry_count = 0;
        self.same_agent_retry_pending = false;
        self.same_agent_retry_reason = None;
        // Set continue_pending to trigger continuation in orchestration
        self.continue_pending = true;
        // Fix continuation fields and loop detection already preserved
        self
    }

    // =========================================================================
    // Fix continuation methods
    // =========================================================================

    /// Check if fix continuations are exhausted.
    ///
    /// Semantics match `continuations_exhausted()`: with default `max_fix_continue_count`
    /// of 10, attempts 0 through 9 are allowed (10 total), attempt 10+ is exhausted.
    #[must_use]
    pub const fn fix_continuations_exhausted(&self) -> bool {
        self.fix_continuation_attempt >= self.max_fix_continue_count
    }

    /// Trigger a fix continuation with status context.
    #[must_use]
    pub fn trigger_fix_continuation(mut self, status: FixStatus, summary: Option<String>) -> Self {
        self.fix_status = Some(status);
        self.fix_previous_summary = summary;
        self.fix_continuation_attempt = self.fix_continuation_attempt.saturating_add(1);
        self.fix_continue_pending = true;
        // Reset XSD retry state for new continuation
        self.xsd_retry_count = 0;
        self.xsd_retry_pending = false;
        self.xsd_retry_session_reuse_pending = false;
        self.last_xsd_error = None;
        self.last_review_xsd_error = None;
        self.last_fix_xsd_error = None;
        // Reset invalid output attempts for new continuation
        self.invalid_output_attempts = 0;
        // Clear other pending flags
        self.context_write_pending = false;
        self.context_cleanup_pending = false;
        self.continue_pending = false;
        self
    }

    /// Clear fix continuation pending flag after starting continuation.
    #[must_use]
    pub const fn clear_fix_continue_pending(mut self) -> Self {
        self.fix_continue_pending = false;
        self
    }

    /// Reset fix continuation state (e.g., when entering a new review pass).
    #[must_use]
    pub fn reset_fix_continuation(mut self) -> Self {
        self.fix_status = None;
        self.fix_previous_summary = None;
        self.fix_continuation_attempt = 0;
        self.fix_continue_pending = false;
        self
    }
}
