//! CLI argument state.
//!
//! This module defines the state structure that accumulates CLI argument values
//! as events are processed through the reducer.

use serde::{Deserialize, Serialize};

/// Preset type for iteration count defaults.
///
/// Each preset defines default values for developer iterations and reviewer reviews.
/// These can be overridden by explicit -D/-R flags.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PresetType {
    /// Quick mode: 1 dev iteration, 1 review pass (-Q)
    Quick,
    /// Rapid mode: 2 dev iterations, 1 review pass (-U)
    Rapid,
    /// Long mode: 15 dev iterations, 10 review passes (-L)
    Long,
    /// Standard mode: 5 dev iterations, 2 review passes (-S)
    Standard,
    /// Thorough mode: 10 dev iterations, 5 review passes (-T)
    Thorough,
}

impl PresetType {
    /// Get the (`developer_iters`, `reviewer_reviews`) counts for this preset.
    #[must_use]
    pub const fn iteration_counts(self) -> (u32, u32) {
        match self {
            Self::Quick => (1, 1),
            Self::Rapid => (2, 1),
            Self::Long => (15, 10),
            Self::Standard => (5, 2),
            Self::Thorough => (10, 5),
        }
    }

    /// Get the developer iterations for this preset.
    #[must_use]
    pub const fn developer_iters(self) -> u32 {
        self.iteration_counts().0
    }

    /// Get the reviewer reviews for this preset.
    #[must_use]
    pub const fn reviewer_reviews(self) -> u32 {
        self.iteration_counts().1
    }
}

/// CLI argument state (intermediate representation before Config).
///
/// This struct accumulates parsed CLI argument values as events are processed.
/// Values of `None` indicate the argument was not specified and should fall back
/// to config file or default values.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CliState {
    // ===== Verbosity =====
    /// Explicit verbosity level (0-4)
    pub verbosity: Option<u8>,
    /// Quiet mode flag (--quiet)
    pub quiet_mode: bool,
    /// Full mode flag (--full)
    pub full_mode: bool,
    /// Debug mode flag (--debug)
    pub debug_mode: bool,

    // ===== Preset =====
    /// Which preset was applied (last wins if multiple specified)
    pub preset_applied: Option<PresetType>,

    // ===== Iteration Counts =====
    /// Explicit developer iterations (-D/--developer-iters)
    pub developer_iters: Option<u32>,
    /// Explicit reviewer reviews (-R/--reviewer-reviews)
    pub reviewer_reviews: Option<u32>,

    // ===== Agent Selection =====
    /// Developer agent name
    pub developer_agent: Option<String>,
    /// Reviewer agent name
    pub reviewer_agent: Option<String>,
    /// Developer model override
    pub developer_model: Option<String>,
    /// Reviewer model override
    pub reviewer_model: Option<String>,
    /// Developer provider override
    pub developer_provider: Option<String>,
    /// Reviewer provider override
    pub reviewer_provider: Option<String>,
    /// Reviewer JSON parser override
    pub reviewer_json_parser: Option<String>,

    // ===== Configuration =====
    /// Isolation mode setting (None = use config default, Some(false) = disabled)
    pub isolation_mode: Option<bool>,
    /// Review depth level
    pub review_depth: Option<String>,
    /// Git user name for commits
    pub git_user_name: Option<String>,
    /// Git user email for commits
    pub git_user_email: Option<String>,
    /// Show streaming metrics flag
    pub streaming_metrics: bool,

    // ===== Agent Preset =====
    /// Named agent preset (default, opencode)
    pub agent_preset: Option<String>,

    // ===== Processing Status =====
    /// Whether CLI processing is complete
    pub complete: bool,
}

impl CliState {
    /// Create a new initial state.
    #[must_use]
    pub fn initial() -> Self {
        Self::default()
    }

    /// Resolve final developer iterations count.
    ///
    /// Priority order:
    /// 1. Explicit -D/--developer-iters flag
    /// 2. Preset default (if a preset was applied)
    /// 3. Config default (passed as argument)
    #[must_use]
    pub fn resolved_developer_iters(&self, config_default: u32) -> u32 {
        self.developer_iters.unwrap_or_else(|| {
            self.preset_applied
                .map_or(config_default, PresetType::developer_iters)
        })
    }

    /// Resolve final reviewer reviews count.
    ///
    /// Priority order:
    /// 1. Explicit -R/--reviewer-reviews flag
    /// 2. Preset default (if a preset was applied)
    /// 3. Config default (passed as argument)
    #[must_use]
    pub fn resolved_reviewer_reviews(&self, config_default: u32) -> u32 {
        self.reviewer_reviews.unwrap_or_else(|| {
            self.preset_applied
                .map_or(config_default, PresetType::reviewer_reviews)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_preset_iteration_counts() {
        assert_eq!(PresetType::Quick.iteration_counts(), (1, 1));
        assert_eq!(PresetType::Rapid.iteration_counts(), (2, 1));
        assert_eq!(PresetType::Long.iteration_counts(), (15, 10));
        assert_eq!(PresetType::Standard.iteration_counts(), (5, 2));
        assert_eq!(PresetType::Thorough.iteration_counts(), (10, 5));
    }

    #[test]
    fn test_preset_individual_accessors() {
        assert_eq!(PresetType::Quick.developer_iters(), 1);
        assert_eq!(PresetType::Quick.reviewer_reviews(), 1);
        assert_eq!(PresetType::Long.developer_iters(), 15);
        assert_eq!(PresetType::Long.reviewer_reviews(), 10);
    }

    #[test]
    fn test_initial_state() {
        let state = CliState::initial();
        assert!(!state.complete);
        assert!(state.preset_applied.is_none());
        assert!(state.developer_iters.is_none());
        assert!(state.reviewer_reviews.is_none());
        assert!(!state.quiet_mode);
        assert!(!state.streaming_metrics);
    }

    #[test]
    fn test_resolved_iters_explicit_override() {
        let state = CliState {
            preset_applied: Some(PresetType::Quick),
            developer_iters: Some(10),
            reviewer_reviews: Some(5),
            ..CliState::initial()
        };

        // Explicit values take precedence over preset
        assert_eq!(state.resolved_developer_iters(99), 10);
        assert_eq!(state.resolved_reviewer_reviews(99), 5);
    }

    #[test]
    fn test_resolved_iters_preset_fallback() {
        let state = CliState {
            preset_applied: Some(PresetType::Long),
            ..CliState::initial()
        };

        // Preset values used when no explicit override
        assert_eq!(state.resolved_developer_iters(99), 15);
        assert_eq!(state.resolved_reviewer_reviews(99), 10);
    }

    #[test]
    fn test_resolved_iters_config_fallback() {
        let state = CliState::initial();

        // Config defaults used when no preset or explicit override
        assert_eq!(state.resolved_developer_iters(5), 5);
        assert_eq!(state.resolved_reviewer_reviews(2), 2);
    }

    #[test]
    fn test_state_serialization() {
        let state = CliState {
            preset_applied: Some(PresetType::Thorough),
            developer_agent: Some("claude".to_string()),
            ..CliState::initial()
        };

        let json = serde_json::to_string(&state).unwrap();
        let deserialized: CliState = serde_json::from_str(&json).unwrap();

        assert_eq!(state.preset_applied, deserialized.preset_applied);
        assert_eq!(state.developer_agent, deserialized.developer_agent);
    }
}
