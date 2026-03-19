//! CLI processing events.
//!
//! This module defines all possible events that can occur during CLI argument processing.
//! Events are data carriers that contain only the information needed to compute new state.

use serde::{Deserialize, Serialize};

/// CLI processing events.
///
/// Each event represents a discrete CLI argument or flag being processed.
/// Events are processed in order, with later events taking precedence
/// (last-wins semantics for conflicting options like presets).
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum CliEvent {
    // ===== Verbosity Events =====
    /// Explicit verbosity level set via -v/--verbosity
    VerbositySet {
        /// Verbosity level (0-4)
        level: u8,
    },
    /// Quiet mode enabled via --quiet
    QuietModeEnabled,
    /// Full mode enabled via --full
    FullModeEnabled,
    /// Debug mode enabled via --debug
    DebugModeEnabled,

    // ===== Preset Events =====
    /// Quick preset applied via -Q/--quick (1 dev, 1 review)
    QuickPresetApplied,
    /// Rapid preset applied via -U/--rapid (2 dev, 1 review)
    RapidPresetApplied,
    /// Long preset applied via -L/--long (15 dev, 10 review)
    LongPresetApplied,
    /// Standard preset applied via -S/--standard (5 dev, 2 review)
    StandardPresetApplied,
    /// Thorough preset applied via -T/--thorough (10 dev, 5 review)
    ThoroughPresetApplied,

    // ===== Iteration Count Events =====
    /// Developer iterations explicitly set via -D/--developer-iters
    DeveloperItersSet {
        /// Number of developer iterations
        value: u32,
    },
    /// Reviewer reviews explicitly set via -R/--reviewer-reviews
    ReviewerReviewsSet {
        /// Number of reviewer review passes
        value: u32,
    },

    // ===== Agent Selection Events =====
    /// Developer agent set via -a/--developer-agent
    DeveloperAgentSet {
        /// Agent name
        agent: String,
    },
    /// Reviewer agent set via -r/--reviewer-agent
    ReviewerAgentSet {
        /// Agent name
        agent: String,
    },
    /// Developer model set via --developer-model
    DeveloperModelSet {
        /// Model identifier
        model: String,
    },
    /// Reviewer model set via --reviewer-model
    ReviewerModelSet {
        /// Model identifier
        model: String,
    },
    /// Developer provider set via --developer-provider
    DeveloperProviderSet {
        /// Provider name
        provider: String,
    },
    /// Reviewer provider set via --reviewer-provider
    ReviewerProviderSet {
        /// Provider name
        provider: String,
    },
    /// Reviewer JSON parser set via --reviewer-json-parser
    ReviewerJsonParserSet {
        /// Parser name
        parser: String,
    },

    // ===== Configuration Events =====
    /// Isolation mode disabled via --no-isolation
    IsolationModeDisabled,
    /// Review depth set via --review-depth
    ReviewDepthSet {
        /// Depth level (standard, comprehensive, security, incremental)
        depth: String,
    },
    /// Git user name set via --git-user-name
    GitUserNameSet {
        /// User name for commits
        name: String,
    },
    /// Git user email set via --git-user-email
    GitUserEmailSet {
        /// User email for commits
        email: String,
    },
    /// Streaming metrics enabled via --show-streaming-metrics
    StreamingMetricsEnabled,

    // ===== Agent Preset Events =====
    /// Named preset for agent combinations (default, opencode)
    AgentPresetSet {
        /// Preset name
        preset: String,
    },

    // ===== Finalization =====
    /// CLI processing complete
    CliProcessingComplete,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_event_serialization() {
        let event = CliEvent::QuickPresetApplied;
        let json = serde_json::to_string(&event).unwrap();
        let deserialized: CliEvent = serde_json::from_str(&json).unwrap();
        assert_eq!(event, deserialized);
    }

    #[test]
    fn test_event_with_data_serialization() {
        let event = CliEvent::DeveloperItersSet { value: 10 };
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("10"));
        let deserialized: CliEvent = serde_json::from_str(&json).unwrap();
        assert_eq!(event, deserialized);
    }

    #[test]
    fn test_all_preset_events_distinct() {
        let presets = [
            CliEvent::QuickPresetApplied,
            CliEvent::RapidPresetApplied,
            CliEvent::LongPresetApplied,
            CliEvent::StandardPresetApplied,
            CliEvent::ThoroughPresetApplied,
        ];

        // Ensure all presets are distinct
        assert!(presets
            .iter()
            .enumerate()
            .all(|(i, p1)| { presets.iter().position(|p| p == p1) == Some(i) }));
    }
}
