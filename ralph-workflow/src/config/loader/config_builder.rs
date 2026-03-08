use crate::config::types::{Config, ReviewDepth, Verbosity};
use crate::config::unified::UnifiedConfig;
use std::path::PathBuf;

/// Create a Config from `UnifiedConfig`.
pub(super) fn config_from_unified(unified: &UnifiedConfig, warnings: &mut Vec<String>) -> Config {
    use crate::config::types::{BehavioralFlags, FeatureFlags};

    let general = &unified.general;
    // max_dev_continuations of 0 is valid and means "no continuations" (total attempts = 1).
    // Any non-negative value is accepted; max_dev_continuations comes from a u32 so can't be negative.
    // When omitted from config file, serde applies default_max_dev_continuations() -> 2.
    let max_dev_continuations = general.max_dev_continuations;
    // max_xsd_retries of 0 is valid and means "disable XSD retries" (immediate agent fallback).
    // Any non-negative value is accepted; max_xsd_retries comes from a u32 so can't be negative.
    // When omitted from config file, serde applies default_max_xsd_retries() -> 10.
    let max_xsd_retries = general.max_xsd_retries;
    // max_same_agent_retries of 0 is valid and means "disable same-agent retries"
    // (immediate fallback to next agent on timeout/internal error).
    // When omitted from config file, serde applies default_max_same_agent_retries() -> 2.
    let max_same_agent_retries = general.max_same_agent_retries;

    let review_depth = ReviewDepth::from_str(&general.review_depth).unwrap_or_else(|| {
        warnings.push(format!(
            "Invalid review_depth '{}' in config; falling back to 'standard'.",
            general.review_depth
        ));
        ReviewDepth::default()
    });

    Config {
        developer_agent: None, // Set from agent_chain or CLI
        reviewer_agent: None,  // Set from agent_chain or CLI
        developer_cmd: None,
        reviewer_cmd: None,
        commit_cmd: None,
        developer_model: None,
        reviewer_model: None,
        developer_provider: None,
        reviewer_provider: None,
        reviewer_json_parser: None, // Set from env var or CLI
        features: FeatureFlags {
            checkpoint_enabled: general.workflow.checkpoint_enabled,
            force_universal_prompt: general.execution.force_universal_prompt,
        },
        developer_iters: general.developer_iters,
        reviewer_reviews: general.reviewer_reviews,
        fast_check_cmd: None,
        full_check_cmd: None,
        behavior: BehavioralFlags {
            interactive: general.behavior.interactive,
            auto_detect_stack: general.behavior.auto_detect_stack,
            strict_validation: general.behavior.strict_validation,
        },
        prompt_path: general
            .prompt_path
            .as_ref()
            .map_or_else(|| PathBuf::from(".agent/last_prompt.txt"), PathBuf::from),
        user_templates_dir: general.templates_dir.as_ref().map(PathBuf::from),
        developer_context: general.developer_context,
        reviewer_context: general.reviewer_context,
        verbosity: Verbosity::from(general.verbosity),
        review_depth,
        isolation_mode: general.execution.isolation_mode,
        git_user_name: general.git_user_name.clone(),
        git_user_email: general.git_user_email.clone(),
        show_streaming_metrics: false, // Default to false; can be enabled via CLI flag or config file
        review_format_retries: 5,      // Default to 5 retries for format correction
        // CRITICAL: Always wrap in Some(). The serde default ensures these fields are never
        // missing from UnifiedConfig, so Config always has a value. The Option<u32> type in
        // Config is for backward compatibility with direct Config construction (e.g., tests).
        max_dev_continuations: Some(max_dev_continuations),
        max_xsd_retries: Some(max_xsd_retries),
        max_same_agent_retries: Some(max_same_agent_retries),
        execution_history_limit: general.execution_history_limit,
        cloud: crate::config::types::CloudConfig::from_env(),
    }
}

/// Default configuration when no config file is found.
pub fn default_config() -> Config {
    use crate::config::types::{BehavioralFlags, FeatureFlags};

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
        prompt_path: PathBuf::from(".agent/last_prompt.txt"),
        user_templates_dir: None,
        developer_context: 1,
        reviewer_context: 0,
        verbosity: Verbosity::Verbose,
        review_depth: ReviewDepth::default(),
        isolation_mode: true,
        git_user_name: None,
        git_user_email: None,
        show_streaming_metrics: false,
        review_format_retries: 5,
        // Semantics: max_dev_continuations counts continuations beyond the initial attempt.
        // Default to 2 continuations (3 total attempts).
        max_dev_continuations: Some(2),
        max_xsd_retries: Some(10), // Default to 10 retries before agent fallback
        max_same_agent_retries: Some(2), // Default to 2 failures (initial + 1 retry) before agent fallback
        execution_history_limit: 1000,   // Default to 1000 entries (ring buffer)
        cloud: crate::config::types::CloudConfig::from_env(),
    }
}
