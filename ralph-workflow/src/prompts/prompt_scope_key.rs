//! Typed prompt scope key for replay identity.
//!
//! RFC-007 Short-term corrective action #1 and #5: Replace ad-hoc `format!()` string keys
//! with a typed `PromptScopeKey` struct that makes missing identity dimensions impossible
//! at compile time.
//!
//! # Design
//!
//! Each prompt key has a phase-specific set of identity dimensions:
//! - **Planning**: iteration + `retry_mode`
//! - **Development**: iteration + optional continuation + `retry_mode`
//! - **Commit**: iteration + attempt + `retry_mode`
//! - **Review**: pass + `retry_mode`
//! - **Fix**: pass + `retry_mode`
//!
//! `recovery_epoch` is carried for auditing/future isolation but is NOT included
//! in the `Display` string to preserve checkpoint backward-compatibility.
//! Level-3/4 resets already change the iteration counter, which changes the key
//! string, so stale history entries are naturally bypassed.
//!
//! # Backward Compatibility
//!
//! The `Display` implementation produces strings identical to the `format!()` calls
//! it replaces. Existing checkpoint `prompt_history` maps remain compatible.

use std::fmt;

/// The pipeline phase that a prompt belongs to.
///
/// Used as a discriminant in `PromptScopeKey` to ensure callers construct
/// keys with the correct phase-specific constructor.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PromptPhase {
    /// Planning phase (iteration-scoped).
    Planning,
    /// Development phase (iteration-scoped, optional continuation).
    Development,
    /// Commit message phase (iteration + attempt-scoped).
    Commit,
    /// Review phase (pass-scoped).
    Review,
    /// Fix phase (pass-scoped).
    Fix,
    /// Rebase conflict resolution phase (rebase-phase-name-scoped).
    ///
    /// `phase` is the lowercase rebase phase name (e.g., "planning", "development")
    /// derived from git rebase context, not the main pipeline phase.
    ConflictResolution {
        /// The rebase phase name (lowercase).
        phase: String,
    },
}

/// The retry mode for a prompt invocation.
///
/// Included in the scope key to distinguish fresh prompts from retry variants.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RetryMode {
    /// Normal (first attempt or continuation) — no retry suffix.
    Normal,
    /// Same-agent retry — appends `_same_agent_retry_{count}` suffix.
    SameAgent {
        /// Retry count (1-based).
        count: u32,
    },
    /// XSD validation retry — appends `_xsd_retry_{count}` suffix.
    Xsd {
        /// Retry count (1-based).
        count: u32,
    },
}

/// Typed prompt scope key.
///
/// Uniquely identifies a prompt for replay from checkpoint history.
/// Constructed via phase-specific factory methods to enforce required dimensions.
///
/// # Backward Compatibility
///
/// `Display` output exactly matches the `format!()` strings previously used in handlers.
/// The `recovery_epoch` field is NOT part of `Display` — it is a future-proofing hook
/// and an audit dimension only.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PromptScopeKey {
    /// Pipeline phase this prompt belongs to.
    pub phase: PromptPhase,
    /// Development iteration (1-based). Used by Planning, Development, Commit phases.
    pub iteration: u32,
    /// Review/fix pass number (1-based). Used by Review and Fix phases.
    pub pass: Option<u32>,
    /// Commit attempt number within the iteration. Used by Commit phase.
    pub attempt: Option<u32>,
    /// Continuation attempt within a development iteration. Used by Development phase.
    pub continuation: Option<u32>,
    /// Retry mode for this invocation.
    pub retry_mode: RetryMode,
    /// Recovery epoch counter — number of epoch-resetting recoveries (level-3/4) that have
    /// occurred. NOT included in `Display` but carried for auditing and future isolation.
    pub recovery_epoch: u32,
}

impl PromptScopeKey {
    /// Construct a key for the Planning phase.
    #[must_use]
    pub const fn for_planning(iteration: u32, retry_mode: RetryMode, recovery_epoch: u32) -> Self {
        Self {
            phase: PromptPhase::Planning,
            iteration,
            pass: None,
            attempt: None,
            continuation: None,
            retry_mode,
            recovery_epoch,
        }
    }

    /// Construct a key for the Development phase.
    ///
    /// Set `continuation` to `Some(attempt)` for continuation mode,
    /// or `None` for normal and retry modes.
    #[must_use]
    pub const fn for_development(
        iteration: u32,
        continuation: Option<u32>,
        retry_mode: RetryMode,
        recovery_epoch: u32,
    ) -> Self {
        Self {
            phase: PromptPhase::Development,
            iteration,
            pass: None,
            attempt: None,
            continuation,
            retry_mode,
            recovery_epoch,
        }
    }

    /// Construct a key for the Commit phase.
    #[must_use]
    pub const fn for_commit(
        iteration: u32,
        attempt: u32,
        retry_mode: RetryMode,
        recovery_epoch: u32,
    ) -> Self {
        Self {
            phase: PromptPhase::Commit,
            iteration,
            pass: None,
            attempt: Some(attempt),
            continuation: None,
            retry_mode,
            recovery_epoch,
        }
    }

    /// Construct a key for the Review phase.
    #[must_use]
    pub const fn for_review(pass: u32, retry_mode: RetryMode, recovery_epoch: u32) -> Self {
        Self {
            phase: PromptPhase::Review,
            iteration: 0,
            pass: Some(pass),
            attempt: None,
            continuation: None,
            retry_mode,
            recovery_epoch,
        }
    }

    /// Construct a key for the Fix phase.
    #[must_use]
    pub const fn for_fix(pass: u32, retry_mode: RetryMode, recovery_epoch: u32) -> Self {
        Self {
            phase: PromptPhase::Fix,
            iteration: 0,
            pass: Some(pass),
            attempt: None,
            continuation: None,
            retry_mode,
            recovery_epoch,
        }
    }

    /// Construct a key for a rebase conflict resolution prompt.
    ///
    /// The `phase` argument is the rebase phase name (lowercase), e.g. `"planning"`
    /// or `"development"`, derived from the git rebase context. It is NOT a main
    /// pipeline phase — it identifies which rebase phase triggered the conflict.
    ///
    /// `recovery_epoch` is carried for auditing but the rebase handler owns epoch
    /// semantics via `PromptCaptured` events. Pass `0` from effectful helpers.
    ///
    /// The `Display` output (`"{phase}_conflict_resolution"`) is byte-identical to
    /// the former `format!("{}_conflict_resolution", phase.to_lowercase())` calls,
    /// preserving backward-compatibility with existing checkpoint `prompt_history` maps.
    #[must_use]
    pub fn for_conflict_resolution(phase: &str, recovery_epoch: u32) -> Self {
        Self {
            phase: PromptPhase::ConflictResolution {
                phase: phase.to_lowercase(),
            },
            iteration: 0,
            pass: None,
            attempt: None,
            continuation: None,
            retry_mode: RetryMode::Normal,
            recovery_epoch,
        }
    }
}

/// Display implementation producing strings backward-compatible with existing checkpoint data.
///
/// Output format per phase:
/// - Planning: `planning_{iter}[_{retry_suffix}]`
/// - Development: `development_{iter}[_continuation_{n}][_{retry_suffix}]`
/// - Commit: `commit_message_attempt_iter{iter}_{attempt}[_{retry_suffix}]`
/// - Review: `review_{pass}[_{retry_suffix}]`
/// - Fix: `fix_{pass}[_{retry_suffix}]`
///
/// Retry suffixes:
/// - `SameAgent { count }` → `_same_agent_retry_{count}`
/// - `Xsd { count }` → `_xsd_retry_{count}`
///
/// NOTE: `recovery_epoch` is intentionally excluded from Display to preserve
/// backward-compatibility with existing checkpoint `prompt_history` entries.
impl fmt::Display for PromptScopeKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let base = match &self.phase {
            PromptPhase::Planning => format!("planning_{}", self.iteration),
            PromptPhase::Development => self.continuation.map_or_else(
                || format!("development_{}", self.iteration),
                |c| format!("development_{}_continuation_{}", self.iteration, c),
            ),
            PromptPhase::Commit => format!(
                "commit_message_attempt_iter{}_{}",
                self.iteration,
                self.attempt.unwrap_or(1)
            ),
            PromptPhase::Review => format!("review_{}", self.pass.unwrap_or(1)),
            PromptPhase::Fix => format!("fix_{}", self.pass.unwrap_or(1)),
            PromptPhase::ConflictResolution { phase } => {
                format!("{phase}_conflict_resolution")
            }
        };
        match &self.retry_mode {
            RetryMode::Normal => write!(f, "{base}"),
            RetryMode::SameAgent { count } => write!(f, "{base}_same_agent_retry_{count}"),
            RetryMode::Xsd { count } => write!(f, "{base}_xsd_retry_{count}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // Planning phase key tests
    // =========================================================================

    #[test]
    fn planning_normal_key_matches_legacy_format() {
        let key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "planning_1");
    }

    #[test]
    fn planning_normal_key_iteration_2() {
        let key = PromptScopeKey::for_planning(2, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "planning_2");
    }

    #[test]
    fn planning_same_agent_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_planning(1, RetryMode::SameAgent { count: 2 }, 0);
        assert_eq!(key.to_string(), "planning_1_same_agent_retry_2");
    }

    // =========================================================================
    // Development phase key tests
    // =========================================================================

    #[test]
    fn development_normal_key_matches_legacy_format() {
        let key = PromptScopeKey::for_development(1, None, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "development_1");
    }

    #[test]
    fn development_continuation_key_matches_legacy_format() {
        let key = PromptScopeKey::for_development(1, Some(3), RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "development_1_continuation_3");
    }

    #[test]
    fn development_same_agent_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_development(2, None, RetryMode::SameAgent { count: 1 }, 0);
        assert_eq!(key.to_string(), "development_2_same_agent_retry_1");
    }

    // =========================================================================
    // Commit phase key tests
    // =========================================================================

    #[test]
    fn commit_normal_key_matches_legacy_format() {
        let key = PromptScopeKey::for_commit(2, 1, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "commit_message_attempt_iter2_1");
    }

    #[test]
    fn commit_same_agent_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_commit(2, 1, RetryMode::SameAgent { count: 1 }, 0);
        assert_eq!(
            key.to_string(),
            "commit_message_attempt_iter2_1_same_agent_retry_1"
        );
    }

    #[test]
    fn commit_xsd_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_commit(2, 1, RetryMode::Xsd { count: 1 }, 0);
        assert_eq!(
            key.to_string(),
            "commit_message_attempt_iter2_1_xsd_retry_1"
        );
    }

    // =========================================================================
    // Review phase key tests
    // =========================================================================

    #[test]
    fn review_normal_key_matches_legacy_format() {
        let key = PromptScopeKey::for_review(2, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "review_2");
    }

    #[test]
    fn review_xsd_retry_key_matches_legacy_format() {
        // invalid_output_attempts is the XSD retry count for review
        let key = PromptScopeKey::for_review(1, RetryMode::Xsd { count: 3 }, 0);
        assert_eq!(key.to_string(), "review_1_xsd_retry_3");
    }

    #[test]
    fn review_same_agent_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_review(1, RetryMode::SameAgent { count: 2 }, 0);
        assert_eq!(key.to_string(), "review_1_same_agent_retry_2");
    }

    // =========================================================================
    // Fix phase key tests
    // =========================================================================

    #[test]
    fn fix_normal_key_matches_legacy_format() {
        let key = PromptScopeKey::for_fix(1, RetryMode::Normal, 0);
        assert_eq!(key.to_string(), "fix_1");
    }

    #[test]
    fn fix_same_agent_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_fix(1, RetryMode::SameAgent { count: 1 }, 0);
        assert_eq!(key.to_string(), "fix_1_same_agent_retry_1");
    }

    #[test]
    fn fix_xsd_retry_key_matches_legacy_format() {
        let key = PromptScopeKey::for_fix(1, RetryMode::Xsd { count: 2 }, 0);
        assert_eq!(key.to_string(), "fix_1_xsd_retry_2");
    }

    // =========================================================================
    // recovery_epoch isolation tests
    // =========================================================================

    #[test]
    fn recovery_epoch_not_in_display_string() {
        // Two keys with same phase/iteration/retry but different epochs
        // must produce the same Display string (epoch not in key string)
        let key_epoch_0 = PromptScopeKey::for_planning(1, RetryMode::Normal, 0);
        let key_epoch_1 = PromptScopeKey::for_planning(1, RetryMode::Normal, 1);
        assert_eq!(
            key_epoch_0.to_string(),
            key_epoch_1.to_string(),
            "recovery_epoch must not affect Display string for checkpoint compat"
        );
    }

    #[test]
    fn keys_are_unique_across_phases() {
        let planning = PromptScopeKey::for_planning(1, RetryMode::Normal, 0).to_string();
        let development =
            PromptScopeKey::for_development(1, None, RetryMode::Normal, 0).to_string();
        let commit = PromptScopeKey::for_commit(1, 1, RetryMode::Normal, 0).to_string();
        let review = PromptScopeKey::for_review(1, RetryMode::Normal, 0).to_string();
        let fix = PromptScopeKey::for_fix(1, RetryMode::Normal, 0).to_string();

        let all = [&planning, &development, &commit, &review, &fix];
        for (i, k1) in all.iter().enumerate() {
            for (j, k2) in all.iter().enumerate() {
                if i != j {
                    assert_ne!(k1, k2, "Keys for different phases must be unique");
                }
            }
        }
    }

    #[test]
    fn keys_are_unique_across_retry_modes() {
        let normal = PromptScopeKey::for_planning(1, RetryMode::Normal, 0).to_string();
        let same_agent =
            PromptScopeKey::for_planning(1, RetryMode::SameAgent { count: 1 }, 0).to_string();
        assert_ne!(normal, same_agent);
    }

    #[test]
    fn keys_are_unique_across_iterations() {
        let iter1 = PromptScopeKey::for_planning(1, RetryMode::Normal, 0).to_string();
        let iter2 = PromptScopeKey::for_planning(2, RetryMode::Normal, 0).to_string();
        assert_ne!(iter1, iter2);
    }

    // =========================================================================
    // ConflictResolution phase key tests
    // =========================================================================

    #[test]
    fn test_conflict_resolution_key_format_matches_legacy_raw_string() {
        // Verifies byte-identical output to the former:
        //   format!("{}_conflict_resolution", "planning".to_lowercase())
        let key = PromptScopeKey::for_conflict_resolution("planning", 0);
        assert_eq!(key.to_string(), "planning_conflict_resolution");
    }

    #[test]
    fn test_conflict_resolution_key_for_different_phases() {
        assert_eq!(
            PromptScopeKey::for_conflict_resolution("development", 0).to_string(),
            "development_conflict_resolution"
        );
        assert_eq!(
            PromptScopeKey::for_conflict_resolution("RebaseOnly", 0).to_string(),
            "rebaseonly_conflict_resolution"
        );
    }

    #[test]
    fn test_conflict_resolution_key_lowercases_phase() {
        let upper = PromptScopeKey::for_conflict_resolution("PLANNING", 0).to_string();
        let lower = PromptScopeKey::for_conflict_resolution("planning", 0).to_string();
        assert_eq!(upper, lower);
    }

    #[test]
    fn test_conflict_resolution_key_recovery_epoch_not_in_display() {
        let key_epoch0 = PromptScopeKey::for_conflict_resolution("planning", 0);
        let key_epoch1 = PromptScopeKey::for_conflict_resolution("planning", 1);
        assert_eq!(
            key_epoch0.to_string(),
            key_epoch1.to_string(),
            "recovery_epoch must not affect Display string for checkpoint compat"
        );
    }

    #[test]
    fn test_conflict_resolution_key_is_unique_from_pipeline_phase_keys() {
        let conflict_key = PromptScopeKey::for_conflict_resolution("planning", 0).to_string();
        let planning_key = PromptScopeKey::for_planning(1, RetryMode::Normal, 0).to_string();
        let development_key =
            PromptScopeKey::for_development(1, None, RetryMode::Normal, 0).to_string();
        // Conflict key contains "_conflict_resolution" suffix, which pipeline keys do not.
        assert_ne!(conflict_key, planning_key);
        assert_ne!(conflict_key, development_key);
        assert!(conflict_key.ends_with("_conflict_resolution"));
    }
}
