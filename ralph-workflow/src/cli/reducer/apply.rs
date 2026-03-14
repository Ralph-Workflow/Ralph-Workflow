//! Apply CLI state to Config.
//!
//! This module handles the final step of the CLI processing pipeline:
//! taking a `CliState` and applying its values to the actual Config struct.

use super::state::CliState;
use crate::config::{Config, ReviewDepth, Verbosity};

/// Apply CLI state to configuration.
///
/// This function takes the accumulated CLI state and applies all values
/// to the Config struct, respecting priority rules:
///
/// - Verbosity: debug > full > quiet > explicit > base
/// - Iterations: explicit -D/-R > preset > config default
/// - Agent settings: CLI > config > defaults
///
/// # Arguments
///
/// * `cli_state` - The CLI state after processing all events
/// * `config` - The configuration to modify (will be updated in-place)
pub fn apply_cli_state_to_config(cli_state: &CliState, config: &mut Config) {
    // ===== Verbosity =====
    // Priority: debug > full > quiet > explicit > base
    if cli_state.debug_mode {
        config.verbosity = Verbosity::Debug;
    } else if cli_state.full_mode {
        config.verbosity = Verbosity::Full;
    } else if cli_state.quiet_mode {
        config.verbosity = Verbosity::Quiet;
    } else if let Some(level) = cli_state.verbosity {
        config.verbosity = Verbosity::from(level);
    }

    // ===== Iteration Counts =====
    // Resolve using CliState's built-in logic (explicit > preset > default)
    let current_developer_iters = config.developer_iters;
    let current_reviewer_reviews = config.reviewer_reviews;

    config.developer_iters = cli_state.resolved_developer_iters(current_developer_iters);
    config.reviewer_reviews = cli_state.resolved_reviewer_reviews(current_reviewer_reviews);

    // ===== Agent Selection =====
    if let Some(ref agent) = cli_state.developer_agent {
        config.developer_agent = Some(agent.clone());
    }
    if let Some(ref agent) = cli_state.reviewer_agent {
        config.reviewer_agent = Some(agent.clone());
    }

    // ===== Model and Provider Overrides =====
    if let Some(ref model) = cli_state.developer_model {
        config.developer_model = Some(model.clone());
    }
    if let Some(ref model) = cli_state.reviewer_model {
        config.reviewer_model = Some(model.clone());
    }
    if let Some(ref provider) = cli_state.developer_provider {
        config.developer_provider = Some(provider.clone());
    }
    if let Some(ref provider) = cli_state.reviewer_provider {
        config.reviewer_provider = Some(provider.clone());
    }
    if let Some(ref parser) = cli_state.reviewer_json_parser {
        config.reviewer_json_parser = Some(parser.clone());
    }

    // ===== Configuration Flags =====
    // Isolation mode: explicit CLI flag > config default
    if let Some(isolation_mode) = cli_state.isolation_mode {
        config.isolation_mode = isolation_mode;
    }

    // Review depth
    if let Some(ref depth) = cli_state.review_depth {
        if let Some(parsed) = ReviewDepth::from_str(depth) {
            config.review_depth = parsed;
        }
        // Invalid depth values are silently ignored (will use config default)
    }

    // Git identity (highest priority in resolution chain)
    if let Some(ref name) = cli_state.git_user_name {
        config.git_user_name = Some(name.clone());
    }
    if let Some(ref email) = cli_state.git_user_email {
        config.git_user_email = Some(email.clone());
    }

    // Streaming metrics
    if cli_state.streaming_metrics {
        config.show_streaming_metrics = true;
    }

    // ===== Agent Presets =====
    // Handle named presets (default, opencode)
    if let Some(ref preset) = cli_state.agent_preset {
        if preset.as_str() == "opencode" {
            config.developer_agent = Some("opencode".to_string());
            config.reviewer_agent = Some("opencode".to_string());
        } else {
            // No override - use agent_chain defaults from config, or ignore unknown preset
        }
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

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.verbosity, Verbosity::Debug);
    }

    #[test]
    fn test_apply_verbosity_full() {
        let cli_state = CliState {
            full_mode: true,
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.verbosity, Verbosity::Full);
    }

    #[test]
    fn test_apply_verbosity_quiet() {
        let cli_state = CliState {
            quiet_mode: true,
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.verbosity, Verbosity::Quiet);
    }

    #[test]
    fn test_apply_verbosity_explicit() {
        let cli_state = CliState {
            verbosity: Some(3),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.verbosity, Verbosity::Full); // level 3 = Full
    }

    #[test]
    fn test_apply_iters_from_preset() {
        use super::super::state::PresetType;

        let cli_state = CliState {
            preset_applied: Some(PresetType::Long),
            ..Default::default()
        };

        let mut config = create_test_config();
        config.developer_iters = 5;
        config.reviewer_reviews = 2;

        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.developer_iters, 15);
        assert_eq!(config.reviewer_reviews, 10);
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

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        // Explicit values should override preset
        assert_eq!(config.developer_iters, 7);
        assert_eq!(config.reviewer_reviews, 3);
    }

    #[test]
    fn test_apply_developer_agent() {
        let cli_state = CliState {
            developer_agent: Some("claude".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.developer_agent, Some("claude".to_string()));
    }

    #[test]
    fn test_apply_reviewer_agent() {
        let cli_state = CliState {
            reviewer_agent: Some("gpt".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.reviewer_agent, Some("gpt".to_string()));
    }

    #[test]
    fn test_apply_isolation_mode_disabled() {
        let cli_state = CliState {
            isolation_mode: Some(false),
            ..Default::default()
        };

        let mut config = create_test_config();
        config.isolation_mode = true;

        apply_cli_state_to_config(&cli_state, &mut config);

        assert!(!config.isolation_mode);
    }

    #[test]
    fn test_apply_review_depth() {
        let cli_state = CliState {
            review_depth: Some("comprehensive".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.review_depth, ReviewDepth::Comprehensive);
    }

    #[test]
    fn test_apply_git_identity() {
        let cli_state = CliState {
            git_user_name: Some("John Doe".to_string()),
            git_user_email: Some("john@example.com".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.git_user_name, Some("John Doe".to_string()));
        assert_eq!(config.git_user_email, Some("john@example.com".to_string()));
    }

    #[test]
    fn test_apply_streaming_metrics() {
        let cli_state = CliState {
            streaming_metrics: true,
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert!(config.show_streaming_metrics);
    }

    #[test]
    fn test_apply_agent_preset_opencode() {
        let cli_state = CliState {
            agent_preset: Some("opencode".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        apply_cli_state_to_config(&cli_state, &mut config);

        assert_eq!(config.developer_agent, Some("opencode".to_string()));
        assert_eq!(config.reviewer_agent, Some("opencode".to_string()));
    }

    #[test]
    fn test_apply_agent_preset_default() {
        let cli_state = CliState {
            agent_preset: Some("default".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        config.developer_agent = Some("existing-dev".to_string());
        config.reviewer_agent = Some("existing-rev".to_string());

        apply_cli_state_to_config(&cli_state, &mut config);

        // Default preset should not change existing agents
        assert_eq!(config.developer_agent, Some("existing-dev".to_string()));
        assert_eq!(config.reviewer_agent, Some("existing-rev".to_string()));
    }

    #[test]
    fn test_apply_preserves_unrelated_config_fields() {
        let cli_state = CliState {
            developer_agent: Some("new-agent".to_string()),
            ..Default::default()
        };

        let mut config = create_test_config();
        config.isolation_mode = true;
        config.review_depth = ReviewDepth::Comprehensive;

        apply_cli_state_to_config(&cli_state, &mut config);

        // Should only change developer_agent
        assert_eq!(config.developer_agent, Some("new-agent".to_string()));
        assert!(config.isolation_mode);
        assert_eq!(config.review_depth, ReviewDepth::Comprehensive);
    }
}
