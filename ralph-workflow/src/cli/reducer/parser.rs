//! Parse Args into CLI events.
//!
//! This module converts the clap-parsed Args struct into a sequence
//! of `CliEvents` that can be processed by the reducer.

use super::event::CliEvent;

/// Convert CLI arguments into a sequence of events (functional pattern with iterator pipeline).
///
/// This function maps each relevant field in the Args struct to a
/// corresponding `CliEvent`. Events are generated in a deterministic order,
/// with later events taking precedence over earlier ones (last-wins semantics).
///
/// # Event Ordering
///
/// Events are generated in this order:
/// 1. Verbosity flags (--quiet, --full, --debug, -v)
/// 2. Preset flags (--quick, --rapid, --long, --standard, --thorough)
/// 3. Explicit iteration counts (-D, -R)
/// 4. Agent selection (-a, -r, model flags)
/// 5. Configuration flags (--no-isolation, --review-depth, etc.)
/// 6. Finalization event
///
/// This ordering ensures that:
/// - Explicit overrides (like -D) come after presets and override them
/// - Last-specified preset wins if multiple are given
///
/// # Arguments
///
/// * `args` - The parsed CLI arguments from clap
///
/// # Returns
///
/// A vector of `CliEvents` representing all specified CLI arguments.
#[must_use]
pub fn args_to_events(args: &super::super::Args) -> Vec<CliEvent> {
    let verbosity_events = std::iter::empty()
        .chain(
            args.verbosity_shorthand
                .quiet
                .then_some(CliEvent::QuietModeEnabled),
        )
        .chain(
            args.verbosity_shorthand
                .full
                .then_some(CliEvent::FullModeEnabled),
        )
        .chain(
            args.debug_verbosity
                .debug
                .then_some(CliEvent::DebugModeEnabled),
        )
        .chain(args.verbosity.map(|level| CliEvent::VerbositySet { level }));

    let preset_events = std::iter::empty()
        .chain(
            args.quick_presets
                .quick
                .then_some(CliEvent::QuickPresetApplied),
        )
        .chain(
            args.quick_presets
                .rapid
                .then_some(CliEvent::RapidPresetApplied),
        )
        .chain(
            args.quick_presets
                .long
                .then_some(CliEvent::LongPresetApplied),
        )
        .chain(
            args.standard_presets
                .standard
                .then_some(CliEvent::StandardPresetApplied),
        )
        .chain(
            args.standard_presets
                .thorough
                .then_some(CliEvent::ThoroughPresetApplied),
        );

    let iteration_events = std::iter::empty()
        .chain(
            args.developer_iters
                .map(|v| CliEvent::DeveloperItersSet { value: v }),
        )
        .chain(
            args.reviewer_reviews
                .map(|v| CliEvent::ReviewerReviewsSet { value: v }),
        );

    let agent_events = std::iter::empty()
        .chain(
            args.developer_agent
                .clone()
                .map(|a| CliEvent::DeveloperAgentSet { agent: a }),
        )
        .chain(
            args.reviewer_agent
                .clone()
                .map(|a| CliEvent::ReviewerAgentSet { agent: a }),
        )
        .chain(
            args.developer_model
                .clone()
                .map(|m| CliEvent::DeveloperModelSet { model: m }),
        )
        .chain(
            args.reviewer_model
                .clone()
                .map(|m| CliEvent::ReviewerModelSet { model: m }),
        )
        .chain(
            args.developer_provider
                .clone()
                .map(|p| CliEvent::DeveloperProviderSet { provider: p }),
        )
        .chain(
            args.reviewer_provider
                .clone()
                .map(|p| CliEvent::ReviewerProviderSet { provider: p }),
        )
        .chain(
            args.reviewer_json_parser
                .clone()
                .map(|p| CliEvent::ReviewerJsonParserSet { parser: p }),
        );

    let preset_selection_events = args
        .preset
        .as_ref()
        .map(|p| CliEvent::AgentPresetSet {
            preset: format!("{p:?}"),
        })
        .into_iter();

    let config_events = std::iter::empty()
        .chain(args.no_isolation.then_some(CliEvent::IsolationModeDisabled))
        .chain(
            args.review_depth
                .clone()
                .map(|d| CliEvent::ReviewDepthSet { depth: d }),
        )
        .chain(
            args.git_user_name
                .as_ref()
                .map(|n| CliEvent::GitUserNameSet {
                    name: n.trim().to_string(),
                }),
        )
        .chain(
            args.git_user_email
                .as_ref()
                .map(|e| CliEvent::GitUserEmailSet {
                    email: e.trim().to_string(),
                }),
        )
        .chain(
            args.show_streaming_metrics
                .then_some(CliEvent::StreamingMetricsEnabled),
        );

    verbosity_events
        .into_iter()
        .chain(preset_events)
        .chain(iteration_events)
        .chain(agent_events)
        .chain(preset_selection_events)
        .chain(config_events)
        .chain(std::iter::once(CliEvent::CliProcessingComplete))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cli::Args;
    use clap::Parser;

    #[test]
    fn test_args_to_events_empty() {
        let args = Args::parse_from(["ralph"]);
        let events = args_to_events(&args);

        // Should have at least the completion event
        assert!(
            events.contains(&CliEvent::CliProcessingComplete),
            "Should always have completion event"
        );

        // Should not have any other events
        assert!(
            !events.iter().any(|e| e != &CliEvent::CliProcessingComplete),
            "Should have no other events for empty args"
        );
    }

    #[test]
    fn test_args_to_events_quick_preset() {
        let args = Args::parse_from(["ralph", "-Q"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::QuickPresetApplied),
            "Should have quick preset event"
        );
        assert!(events.contains(&CliEvent::CliProcessingComplete));
    }

    #[test]
    fn test_args_to_events_rapid_preset() {
        let args = Args::parse_from(["ralph", "-U"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::RapidPresetApplied),
            "Should have rapid preset event"
        );
        assert!(events.contains(&CliEvent::CliProcessingComplete));
    }

    #[test]
    fn test_args_to_events_long_preset() {
        let args = Args::parse_from(["ralph", "-L"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::LongPresetApplied),
            "Should have long preset event"
        );
        assert!(events.contains(&CliEvent::CliProcessingComplete));
    }

    #[test]
    fn test_args_to_events_standard_preset() {
        let args = Args::parse_from(["ralph", "-S"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::StandardPresetApplied),
            "Should have standard preset event"
        );
        assert!(events.contains(&CliEvent::CliProcessingComplete));
    }

    #[test]
    fn test_args_to_events_thorough_preset() {
        let args = Args::parse_from(["ralph", "-T"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::ThoroughPresetApplied),
            "Should have thorough preset event"
        );
        assert!(events.contains(&CliEvent::CliProcessingComplete));
    }

    #[test]
    fn test_args_to_events_explicit_iters() {
        let args = Args::parse_from(["ralph", "-D", "7", "-R", "3"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::DeveloperItersSet { value: 7 }),
            "Should have developer iters event"
        );
        assert!(
            events.contains(&CliEvent::ReviewerReviewsSet { value: 3 }),
            "Should have reviewer reviews event"
        );
    }

    #[test]
    fn test_args_to_events_preset_plus_explicit_override() {
        let args = Args::parse_from(["ralph", "-Q", "-D", "10", "-R", "5"]);
        let events = args_to_events(&args);

        // Should have both preset and explicit values
        assert!(events.contains(&CliEvent::QuickPresetApplied));
        assert!(events.contains(&CliEvent::DeveloperItersSet { value: 10 }));
        assert!(events.contains(&CliEvent::ReviewerReviewsSet { value: 5 }));

        // Verify order: preset comes before explicit override
        let preset_idx = events
            .iter()
            .position(|e| e == &CliEvent::QuickPresetApplied)
            .expect("Should have quick preset");
        let iters_idx = events
            .iter()
            .position(|e| e == &CliEvent::DeveloperItersSet { value: 10 })
            .expect("Should have developer iters");

        assert!(
            preset_idx < iters_idx,
            "Preset should come before explicit override"
        );
    }

    #[test]
    fn test_args_to_events_agent_selection() {
        let args = Args::parse_from(["ralph", "-a", "claude", "-r", "gpt"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::DeveloperAgentSet {
                agent: "claude".to_string()
            }),
            "Should have developer agent event"
        );
        assert!(
            events.contains(&CliEvent::ReviewerAgentSet {
                agent: "gpt".to_string()
            }),
            "Should have reviewer agent event"
        );
    }

    #[test]
    fn test_args_to_events_verbose_mode() {
        let args = Args::parse_from(["ralph", "-v", "3"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::VerbositySet { level: 3 }),
            "Should have verbosity set event"
        );
    }

    #[test]
    fn test_args_to_events_debug_mode() {
        let args = Args::parse_from(["ralph", "--debug"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::DebugModeEnabled),
            "Should have debug mode event"
        );
    }

    #[test]
    fn test_args_to_events_no_isolation() {
        let args = Args::parse_from(["ralph", "--no-isolation"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::IsolationModeDisabled),
            "Should have isolation mode disabled event"
        );
    }

    #[test]
    fn test_args_to_events_git_identity() {
        let args = Args::parse_from([
            "ralph",
            "--git-user-name",
            "John Doe",
            "--git-user-email",
            "john@example.com",
        ]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::GitUserNameSet {
                name: "John Doe".to_string()
            }),
            "Should have git user name event"
        );
        assert!(
            events.contains(&CliEvent::GitUserEmailSet {
                email: "john@example.com".to_string()
            }),
            "Should have git user email event"
        );
    }

    #[test]
    fn test_args_to_events_streaming_metrics() {
        let args = Args::parse_from(["ralph", "--show-streaming-metrics"]);
        let events = args_to_events(&args);

        assert!(
            events.contains(&CliEvent::StreamingMetricsEnabled),
            "Should have streaming metrics event"
        );
    }

    #[test]
    fn test_args_parses_pause_on_exit_mode() {
        let args = Args::try_parse_from(["ralph", "--pause-on-exit", "always"])
            .expect("pause-on-exit should parse");

        assert_eq!(args.pause_on_exit, crate::cli::PauseOnExitMode::Always);
    }
}
