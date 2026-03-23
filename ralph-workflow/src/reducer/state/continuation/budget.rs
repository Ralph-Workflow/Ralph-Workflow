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
    pub fn with_artifact(self, artifact: ArtifactType) -> Self {
        Self {
            current_artifact: Some(artifact),
            xsd_retry_count: 0,
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: false,
            last_xsd_error: None,
            last_review_xsd_error: None,
            last_fix_xsd_error: None,
            ..self
        }
    }

    /// Mark XSD validation as failed, triggering a retry.
    ///
    /// For XSD retry, we want to re-invoke the same agent in the same session when possible,
    /// to keep retries deterministic and to preserve provider-side context.
    #[must_use]
    pub fn trigger_xsd_retry(self) -> Self {
        Self {
            xsd_retry_pending: true,
            xsd_retry_count: self.xsd_retry_count.saturating_add(1),
            xsd_retry_session_reuse_pending: true,
            ..self
        }
    }

    /// Clear XSD retry pending flag after starting retry.
    #[must_use]
    pub fn clear_xsd_retry_pending(self) -> Self {
        Self {
            xsd_retry_pending: false,
            last_xsd_error: None,
            last_review_xsd_error: None,
            last_fix_xsd_error: None,
            ..self
        }
    }

    /// Check if XSD retries are exhausted.
    #[must_use]
    pub const fn xsd_retries_exhausted(&self) -> bool {
        self.xsd_retry_count >= self.max_xsd_retry_count
    }

    /// Mark a same-agent retry as pending for a transient invocation failure.
    #[must_use]
    pub fn trigger_same_agent_retry(self, reason: SameAgentRetryReason) -> Self {
        Self {
            same_agent_retry_pending: true,
            same_agent_retry_count: self.same_agent_retry_count.saturating_add(1),
            same_agent_retry_reason: Some(reason),
            ..self
        }
    }

    /// Clear same-agent retry pending flag after starting retry.
    #[must_use]
    pub fn clear_same_agent_retry_pending(self) -> Self {
        Self {
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..self
        }
    }

    /// Check if same-agent retries are exhausted.
    #[must_use]
    pub const fn same_agent_retries_exhausted(&self) -> bool {
        self.same_agent_retry_count >= self.max_same_agent_retry_count
    }

    /// Mark continuation as pending (output valid but work incomplete).
    #[must_use]
    pub fn trigger_continue(self) -> Self {
        Self {
            continue_pending: true,
            ..self
        }
    }

    /// Clear continue pending flag after starting continuation.
    #[must_use]
    pub fn clear_continue_pending(self) -> Self {
        Self {
            continue_pending: false,
            ..self
        }
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
        self,
        status: DevelopmentStatus,
        summary: String,
        files_changed: Option<Vec<String>>,
        next_steps: Option<String>,
    ) -> Self {
        let next_attempt = self.continuation_attempt.saturating_add(1);

        if next_attempt >= self.max_continue_count {
            return Self {
                continue_pending: false,
                context_write_pending: false,
                context_cleanup_pending: false,
                ..self
            };
        }

        Self {
            previous_status: Some(status),
            previous_summary: Some(summary),
            previous_files_changed: files_changed.map(std::vec::Vec::into_boxed_slice),
            previous_next_steps: next_steps,
            continuation_attempt: next_attempt,
            invalid_output_attempts: 0,
            context_write_pending: true,
            context_cleanup_pending: false,
            xsd_retry_count: 0,
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: false,
            last_xsd_error: None,
            last_review_xsd_error: None,
            last_fix_xsd_error: None,
            same_agent_retry_count: 0,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            continue_pending: true,
            ..self
        }
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
    pub fn trigger_fix_continuation(self, status: FixStatus, summary: Option<String>) -> Self {
        Self {
            fix_status: Some(status),
            fix_previous_summary: summary,
            fix_continuation_attempt: self.fix_continuation_attempt.saturating_add(1),
            fix_continue_pending: true,
            xsd_retry_count: 0,
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: false,
            last_xsd_error: None,
            last_review_xsd_error: None,
            last_fix_xsd_error: None,
            invalid_output_attempts: 0,
            context_write_pending: false,
            context_cleanup_pending: false,
            continue_pending: false,
            ..self
        }
    }

    /// Clear fix continuation pending flag after starting continuation.
    #[must_use]
    pub fn clear_fix_continue_pending(self) -> Self {
        Self {
            fix_continue_pending: false,
            ..self
        }
    }

    /// Reset fix continuation state (e.g., when entering a new review pass).
    #[must_use]
    pub fn reset_fix_continuation(self) -> Self {
        Self {
            fix_status: None,
            fix_previous_summary: None,
            fix_continuation_attempt: 0,
            fix_continue_pending: false,
            ..self
        }
    }
}
