//! Apply CLI state to Config.
//!
//! This module handles the final step of the CLI processing pipeline:
//! taking a `CliState` and applying its values to the actual Config struct.

use super::state::CliState;
use crate::config::{Config, ReviewDepth, Verbosity};

/// Apply CLI state to configuration (functional pattern - returns new Config).
///
/// This function takes the accumulated CLI state and applies all values
/// to a new Config struct, respecting priority rules:
///
/// - Verbosity: debug > full > quiet > explicit > base
/// - Iterations: explicit -D/-R > preset > config default
/// - Agent settings: CLI > config > defaults
///
/// # Arguments
///
/// * `cli_state` - The CLI state after processing all events
/// * `config` - The base configuration to apply state to
///
/// # Returns
///
/// A new Config with CLI state applied
#[must_use]
pub fn apply_cli_state_to_config(cli_state: &CliState, config: &Config) -> Config {
    // ===== Verbosity =====
    // Priority: debug > full > quiet > explicit > base
    let verbosity = if cli_state.debug_mode {
        Verbosity::Debug
    } else if cli_state.full_mode {
        Verbosity::Full
    } else if cli_state.quiet_mode {
        Verbosity::Quiet
    } else {
        cli_state
            .verbosity
            .map(Verbosity::from)
            .unwrap_or(config.verbosity)
    };

    // ===== Iteration Counts =====
    let current_developer_iters = config.developer_iters;
    let current_reviewer_reviews = config.reviewer_reviews;
    let developer_iters = cli_state.resolved_developer_iters(current_developer_iters);
    let reviewer_reviews = cli_state.resolved_reviewer_reviews(current_reviewer_reviews);

    // ===== Agent Selection =====
    let developer_agent = cli_state
        .developer_agent
        .clone()
        .or_else(|| config.developer_agent.clone());
    let reviewer_agent = cli_state
        .reviewer_agent
        .clone()
        .or_else(|| config.reviewer_agent.clone());

    // ===== Model and Provider Overrides =====
    let developer_model = cli_state
        .developer_model
        .clone()
        .or_else(|| config.developer_model.clone());
    let reviewer_model = cli_state
        .reviewer_model
        .clone()
        .or_else(|| config.reviewer_model.clone());
    let developer_provider = cli_state
        .developer_provider
        .clone()
        .or_else(|| config.developer_provider.clone());
    let reviewer_provider = cli_state
        .reviewer_provider
        .clone()
        .or_else(|| config.reviewer_provider.clone());
    let reviewer_json_parser = cli_state
        .reviewer_json_parser
        .clone()
        .or_else(|| config.reviewer_json_parser.clone());

    // ===== Configuration Flags =====
    // Isolation mode: explicit CLI flag > config default
    let isolation_mode = cli_state.isolation_mode.unwrap_or(config.isolation_mode);

    // Review depth
    let review_depth = cli_state
        .review_depth
        .as_ref()
        .and_then(|d| ReviewDepth::from_str(d))
        .unwrap_or(config.review_depth);

    // Git identity (highest priority in resolution chain)
    let git_user_name = cli_state
        .git_user_name
        .clone()
        .or_else(|| config.git_user_name.clone());
    let git_user_email = cli_state
        .git_user_email
        .clone()
        .or_else(|| config.git_user_email.clone());

    // Streaming metrics
    let show_streaming_metrics = config.show_streaming_metrics || cli_state.streaming_metrics;

    // ===== Agent Presets =====
    // Handle named presets (default, opencode)
    let (developer_agent, reviewer_agent) = if let Some(ref preset) = cli_state.agent_preset {
        if preset.as_str() == "opencode" {
            (Some("opencode".to_string()), Some("opencode".to_string()))
        } else {
            (developer_agent, reviewer_agent)
        }
    } else {
        (developer_agent, reviewer_agent)
    };

    // Build new config using struct update syntax (functional pattern)
    Config {
        verbosity,
        developer_iters,
        reviewer_reviews,
        developer_agent,
        reviewer_agent,
        developer_model,
        reviewer_model,
        developer_provider,
        reviewer_provider,
        reviewer_json_parser,
        isolation_mode,
        review_depth,
        git_user_name,
        git_user_email,
        show_streaming_metrics,
        ..config.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::types::{BehavioralFlags, FeatureFlags};

    fn create_test_config() -> Config {
        Config {
            developer_agent: None,
            reviewer_agent: None,
            developer_cmd: None,
            reviewer_cmd: None,
            commit_cmd: None,
            developer_model: None,
            reviewer_model: None,
            developer_provider: None,
            reviewer_provider: None,
            reviewer_json_parser: None,
            features: FeatureFlags {
                checkpoint_enabled: true,
                force_universal_prompt: false,
            },
            developer_iters: 5,
            reviewer_reviews: 2,
            fast_check_cmd: None,
            full_check_cmd: None,
            behavior: BehavioralFlags {
                interactive: true,
                auto_detect_stack: true,
                strict_validation: false,
            },
            prompt_path: std::path::PathBuf::from(".agent/last_prompt.txt"),
            user_templates_dir: None,
            developer_context: 1,
            reviewer_context: 0,
            verbosity: Verbosity::Verbose,
            review_depth: ReviewDepth::Standard,
            isolation_mode: true,
            git_user_name: None,
            git_user_email: None,
            show_streaming_metrics: false,
            review_format_retries: 5,
            max_dev_continuations: Some(2),
            max_xsd_retries: Some(10),
            max_same_agent_retries: Some(2),
            max_commit_residual_retries: Some(10),
            execution_history_limit: 1000,
            cloud: crate::config::types::CloudConfig::disabled(),
        }
    }

    #[test]
    fn test_apply_verbosity_debug() {
        let cli_state = CliState {
            debug_mode: true,
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.verbosity, Verbosity::Debug);
    }

    #[test]
    fn test_apply_verbosity_full() {
        let cli_state = CliState {
            full_mode: true,
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.verbosity, Verbosity::Full);
    }

    #[test]
    fn test_apply_verbosity_quiet() {
        let cli_state = CliState {
            quiet_mode: true,
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.verbosity, Verbosity::Quiet);
    }

    #[test]
    fn test_apply_verbosity_explicit() {
        let cli_state = CliState {
            verbosity: Some(3),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.verbosity, Verbosity::Full); // level 3 = Full
    }

    #[test]
    fn test_apply_iters_from_preset() {
        use super::super::state::PresetType;

        let cli_state = CliState {
            preset_applied: Some(PresetType::Long),
            ..Default::default()
        };

        let config = Config {
            developer_iters: 5,
            reviewer_reviews: 2,
            ..create_test_config()
        };

        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.developer_iters, 15);
        assert_eq!(result.reviewer_reviews, 10);
    }

    #[test]
    fn test_apply_iters_explicit_override_preset() {
        use super::super::state::PresetType;

        let cli_state = CliState {
            preset_applied: Some(PresetType::Quick), // Would give 1, 1
            developer_iters: Some(7),                // Explicit override
            reviewer_reviews: Some(3),               // Explicit override
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        // Explicit values should override preset
        assert_eq!(result.developer_iters, 7);
        assert_eq!(result.reviewer_reviews, 3);
    }

    #[test]
    fn test_apply_developer_agent() {
        let cli_state = CliState {
            developer_agent: Some("claude".to_string()),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.developer_agent, Some("claude".to_string()));
    }

    #[test]
    fn test_apply_reviewer_agent() {
        let cli_state = CliState {
            reviewer_agent: Some("gpt".to_string()),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.reviewer_agent, Some("gpt".to_string()));
    }

    #[test]
    fn test_apply_isolation_mode_disabled() {
        let cli_state = CliState {
            isolation_mode: Some(false),
            ..Default::default()
        };

        let config = Config {
            isolation_mode: true,
            ..create_test_config()
        };

        let result = apply_cli_state_to_config(&cli_state, &config);

        assert!(!result.isolation_mode);
    }

    #[test]
    fn test_apply_review_depth() {
        let cli_state = CliState {
            review_depth: Some("comprehensive".to_string()),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.review_depth, ReviewDepth::Comprehensive);
    }

    #[test]
    fn test_apply_git_identity() {
        let cli_state = CliState {
            git_user_name: Some("John Doe".to_string()),
            git_user_email: Some("john@example.com".to_string()),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.git_user_name, Some("John Doe".to_string()));
        assert_eq!(result.git_user_email, Some("john@example.com".to_string()));
    }

    #[test]
    fn test_apply_streaming_metrics() {
        let cli_state = CliState {
            streaming_metrics: true,
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert!(result.show_streaming_metrics);
    }

    #[test]
    fn test_apply_agent_preset_opencode() {
        let cli_state = CliState {
            agent_preset: Some("opencode".to_string()),
            ..Default::default()
        };

        let config = create_test_config();
        let result = apply_cli_state_to_config(&cli_state, &config);

        assert_eq!(result.developer_agent, Some("opencode".to_string()));
        assert_eq!(result.reviewer_agent, Some("opencode".to_string()));
    }

    #[test]
    fn test_apply_agent_preset_default() {
        let cli_state = CliState {
            agent_preset: Some("default".to_string()),
            ..Default::default()
        };

        let config = Config {
            developer_agent: Some("existing-dev".to_string()),
            reviewer_agent: Some("existing-rev".to_string()),
            ..create_test_config()
        };

        let result = apply_cli_state_to_config(&cli_state, &config);

        // Default preset should not change existing agents
        assert_eq!(result.developer_agent, Some("existing-dev".to_string()));
        assert_eq!(result.reviewer_agent, Some("existing-rev".to_string()));
    }

    #[test]
    fn test_apply_preserves_unrelated_config_fields() {
        let cli_state = CliState {
            developer_agent: Some("new-agent".to_string()),
            ..Default::default()
        };

        let config = Config {
            isolation_mode: true,
            review_depth: ReviewDepth::Comprehensive,
            ..create_test_config()
        };

        let result = apply_cli_state_to_config(&cli_state, &config);

        // Should only change developer_agent
        assert_eq!(result.developer_agent, Some("new-agent".to_string()));
        assert!(result.isolation_mode);
        assert_eq!(result.review_depth, ReviewDepth::Comprehensive);
    }
}
