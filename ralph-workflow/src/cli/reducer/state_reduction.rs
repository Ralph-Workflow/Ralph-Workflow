//! Pure reducer for CLI argument processing.
//!
//! This module contains the pure `reduce` function that transforms CLI state
//! based on events. It follows the same pattern as the pipeline reducer.

use super::event::CliEvent;
use super::state::{CliState, PresetType};

/// Pure reducer function for CLI argument processing.
///
/// This function takes the current state and an event, returning a new state.
/// It is a pure function with no side effects, making it easy to test.
///
/// # Arguments
///
/// * `state` - The current CLI state
/// * `event` - The event to process
///
/// # Returns
///
/// A new CLI state with the event applied.
///
/// # Event Processing Order
///
/// Events are processed in the order they are received. For conflicting options
/// (like multiple presets), the last one wins. This allows users to combine
/// flags where the later flag takes precedence.
#[must_use]
pub fn reduce(state: CliState, event: CliEvent) -> CliState {
    match event {
        // ===== Verbosity Events =====
        CliEvent::VerbositySet { level } => CliState {
            verbosity: Some(level),
            ..state
        },
        CliEvent::QuietModeEnabled => CliState {
            quiet_mode: true,
            ..state
        },
        CliEvent::FullModeEnabled => CliState {
            full_mode: true,
            ..state
        },
        CliEvent::DebugModeEnabled => CliState {
            debug_mode: true,
            ..state
        },

        // ===== Preset Events (last wins) =====
        CliEvent::QuickPresetApplied => CliState {
            preset_applied: Some(PresetType::Quick),
            ..state
        },
        CliEvent::RapidPresetApplied => CliState {
            preset_applied: Some(PresetType::Rapid),
            ..state
        },
        CliEvent::LongPresetApplied => CliState {
            preset_applied: Some(PresetType::Long),
            ..state
        },
        CliEvent::StandardPresetApplied => CliState {
            preset_applied: Some(PresetType::Standard),
            ..state
        },
        CliEvent::ThoroughPresetApplied => CliState {
            preset_applied: Some(PresetType::Thorough),
            ..state
        },

        // ===== Iteration Count Events =====
        CliEvent::DeveloperItersSet { value } => CliState {
            developer_iters: Some(value),
            ..state
        },
        CliEvent::ReviewerReviewsSet { value } => CliState {
            reviewer_reviews: Some(value),
            ..state
        },

        // ===== Agent Selection Events =====
        CliEvent::DeveloperAgentSet { agent } => CliState {
            developer_agent: Some(agent),
            ..state
        },
        CliEvent::ReviewerAgentSet { agent } => CliState {
            reviewer_agent: Some(agent),
            ..state
        },
        CliEvent::DeveloperModelSet { model } => CliState {
            developer_model: Some(model),
            ..state
        },
        CliEvent::ReviewerModelSet { model } => CliState {
            reviewer_model: Some(model),
            ..state
        },
        CliEvent::DeveloperProviderSet { provider } => CliState {
            developer_provider: Some(provider),
            ..state
        },
        CliEvent::ReviewerProviderSet { provider } => CliState {
            reviewer_provider: Some(provider),
            ..state
        },
        CliEvent::ReviewerJsonParserSet { parser } => CliState {
            reviewer_json_parser: Some(parser),
            ..state
        },

        // ===== Configuration Events =====
        CliEvent::IsolationModeDisabled => CliState {
            isolation_mode: Some(false),
            ..state
        },
        CliEvent::ReviewDepthSet { depth } => CliState {
            review_depth: Some(depth),
            ..state
        },
        CliEvent::GitUserNameSet { name } => CliState {
            git_user_name: Some(name),
            ..state
        },
        CliEvent::GitUserEmailSet { email } => CliState {
            git_user_email: Some(email),
            ..state
        },
        CliEvent::StreamingMetricsEnabled => CliState {
            streaming_metrics: true,
            ..state
        },

        // ===== Agent Preset Events =====
        CliEvent::AgentPresetSet { preset } => CliState {
            agent_preset: Some(preset),
            ..state
        },

        // ===== Finalization =====
        CliEvent::CliProcessingComplete => CliState {
            complete: true,
            ..state
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_reduce_verbosity_set() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::VerbositySet { level: 3 });
        assert_eq!(new_state.verbosity, Some(3));
    }

    #[test]
    fn test_reduce_quiet_mode() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::QuietModeEnabled);
        assert!(new_state.quiet_mode);
    }

    #[test]
    fn test_reduce_preset_quick() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::QuickPresetApplied);
        assert_eq!(new_state.preset_applied, Some(PresetType::Quick));
        assert_eq!(new_state.resolved_developer_iters(99), 1);
        assert_eq!(new_state.resolved_reviewer_reviews(99), 1);
    }

    #[test]
    fn test_reduce_preset_long() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::LongPresetApplied);
        assert_eq!(new_state.preset_applied, Some(PresetType::Long));
        assert_eq!(new_state.resolved_developer_iters(99), 15);
        assert_eq!(new_state.resolved_reviewer_reviews(99), 10);
    }

    #[test]
    fn test_reduce_preset_standard() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::StandardPresetApplied);
        assert_eq!(new_state.preset_applied, Some(PresetType::Standard));
        assert_eq!(new_state.resolved_developer_iters(99), 5);
        assert_eq!(new_state.resolved_reviewer_reviews(99), 2);
    }

    #[test]
    fn test_reduce_preset_thorough() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::ThoroughPresetApplied);
        assert_eq!(new_state.preset_applied, Some(PresetType::Thorough));
        assert_eq!(new_state.resolved_developer_iters(99), 10);
        assert_eq!(new_state.resolved_reviewer_reviews(99), 5);
    }

    #[test]
    fn test_reduce_preset_last_wins() {
        let state = CliState::initial();
        // Apply quick first, then long
        let state = reduce(state, CliEvent::QuickPresetApplied);
        let state = reduce(state, CliEvent::LongPresetApplied);

        // Long should win (last applied)
        assert_eq!(state.preset_applied, Some(PresetType::Long));
        assert_eq!(state.resolved_developer_iters(99), 15);
    }

    #[test]
    fn test_reduce_explicit_iters_override_preset() {
        let state = CliState::initial();
        // Apply preset first
        let state = reduce(state, CliEvent::QuickPresetApplied);
        // Then explicit override
        let state = reduce(state, CliEvent::DeveloperItersSet { value: 7 });
        let state = reduce(state, CliEvent::ReviewerReviewsSet { value: 3 });

        // Explicit values should be used
        assert_eq!(state.resolved_developer_iters(99), 7);
        assert_eq!(state.resolved_reviewer_reviews(99), 3);
    }

    #[test]
    fn test_reduce_developer_agent() {
        let state = CliState::initial();
        let new_state = reduce(
            state,
            CliEvent::DeveloperAgentSet {
                agent: "claude".to_string(),
            },
        );
        assert_eq!(new_state.developer_agent, Some("claude".to_string()));
    }

    #[test]
    fn test_reduce_isolation_mode_disabled() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::IsolationModeDisabled);
        assert_eq!(new_state.isolation_mode, Some(false));
    }

    #[test]
    fn test_reduce_streaming_metrics() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::StreamingMetricsEnabled);
        assert!(new_state.streaming_metrics);
    }

    #[test]
    fn test_reduce_complete() {
        let state = CliState::initial();
        let new_state = reduce(state, CliEvent::CliProcessingComplete);
        assert!(new_state.complete);
    }

    #[test]
    fn test_reduce_preserves_unrelated_fields() {
        let mut state = CliState::initial();
        state.developer_agent = Some("existing".to_string());

        // Applying unrelated event should preserve developer_agent
        let new_state = reduce(state, CliEvent::QuietModeEnabled);

        assert!(new_state.quiet_mode);
        assert_eq!(new_state.developer_agent, Some("existing".to_string()));
    }

    #[test]
    fn test_full_event_sequence() {
        let events = vec![
            CliEvent::ThoroughPresetApplied,
            CliEvent::DeveloperAgentSet {
                agent: "opencode".to_string(),
            },
            CliEvent::ReviewerAgentSet {
                agent: "claude".to_string(),
            },
            CliEvent::DebugModeEnabled,
            CliEvent::StreamingMetricsEnabled,
            CliEvent::CliProcessingComplete,
        ];

        let mut state = CliState::initial();
        for event in events {
            state = reduce(state, event);
        }

        assert!(state.complete);
        assert!(state.debug_mode);
        assert!(state.streaming_metrics);
        assert_eq!(state.preset_applied, Some(PresetType::Thorough));
        assert_eq!(state.developer_agent, Some("opencode".to_string()));
        assert_eq!(state.reviewer_agent, Some("claude".to_string()));
        assert_eq!(state.resolved_developer_iters(5), 10);
        assert_eq!(state.resolved_reviewer_reviews(2), 5);
    }
}
