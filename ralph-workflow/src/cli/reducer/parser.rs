//! Parse Args into CLI events.
//!
//! This module converts the clap-parsed Args struct into a sequence
//! of `CliEvents` that can be processed by the reducer.

use super::event::CliEvent;

/// Convert CLI arguments into a sequence of events.
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
    let mut events = Vec::new();

    // ===== Verbosity Events =====
    if args.verbosity_shorthand.quiet {
        events.push(CliEvent::QuietModeEnabled);
    }
    if args.verbosity_shorthand.full {
        events.push(CliEvent::FullModeEnabled);
    }
    if args.debug_verbosity.debug {
        events.push(CliEvent::DebugModeEnabled);
    }
    if let Some(level) = args.verbosity {
        events.push(CliEvent::VerbositySet { level });
    }

    // ===== Preset Events =====
    // Order matters: later presets override earlier ones
    if args.quick_presets.quick {
        events.push(CliEvent::QuickPresetApplied);
    }
    if args.quick_presets.rapid {
        events.push(CliEvent::RapidPresetApplied);
    }
    // THE FIX: These three preset flags were missing!
    if args.quick_presets.long {
        events.push(CliEvent::LongPresetApplied);
    }
    if args.standard_presets.standard {
        events.push(CliEvent::StandardPresetApplied);
    }
    if args.standard_presets.thorough {
        events.push(CliEvent::ThoroughPresetApplied);
    }

    // ===== Iteration Count Events =====
    // Explicit iteration counts come after presets so they override preset defaults
    if let Some(iters) = args.developer_iters {
        events.push(CliEvent::DeveloperItersSet { value: iters });
    }
    if let Some(reviews) = args.reviewer_reviews {
        events.push(CliEvent::ReviewerReviewsSet { value: reviews });
    }

    // ===== Agent Selection Events =====
    if let Some(ref agent) = args.developer_agent {
        events.push(CliEvent::DeveloperAgentSet {
            agent: agent.clone(),
        });
    }
    if let Some(ref agent) = args.reviewer_agent {
        events.push(CliEvent::ReviewerAgentSet {
            agent: agent.clone(),
        });
    }
    if let Some(ref model) = args.developer_model {
        events.push(CliEvent::DeveloperModelSet {
            model: model.clone(),
        });
    }
    if let Some(ref model) = args.reviewer_model {
        events.push(CliEvent::ReviewerModelSet {
            model: model.clone(),
        });
    }
    if let Some(ref provider) = args.developer_provider {
        events.push(CliEvent::DeveloperProviderSet {
            provider: provider.clone(),
        });
    }
    if let Some(ref provider) = args.reviewer_provider {
        events.push(CliEvent::ReviewerProviderSet {
            provider: provider.clone(),
        });
    }
    if let Some(ref parser) = args.reviewer_json_parser {
        events.push(CliEvent::ReviewerJsonParserSet {
            parser: parser.clone(),
        });
    }

    // ===== Agent Preset Events =====
    if let Some(ref preset) = args.preset {
        events.push(CliEvent::AgentPresetSet {
            preset: format!("{preset:?}"),
        });
    }

    // ===== Configuration Events =====
    if args.no_isolation {
        events.push(CliEvent::IsolationModeDisabled);
    }
    if let Some(ref depth) = args.review_depth {
        events.push(CliEvent::ReviewDepthSet {
            depth: depth.clone(),
        });
    }
    if let Some(ref name) = args.git_user_name {
        events.push(CliEvent::GitUserNameSet {
            name: name.trim().to_string(),
        });
    }
    if let Some(ref email) = args.git_user_email {
        events.push(CliEvent::GitUserEmailSet {
            email: email.trim().to_string(),
        });
    }
    if args.show_streaming_metrics {
        events.push(CliEvent::StreamingMetricsEnabled);
    }

    // ===== Finalization =====
    events.push(CliEvent::CliProcessingComplete);

    events
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
