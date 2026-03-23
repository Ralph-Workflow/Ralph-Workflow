use crate::config::types::{Config, ReviewDepth, Verbosity};
use crate::config::unified::UnifiedConfig;
use std::path::PathBuf;

use crate::config::cloud::load_cloud_config_from_env;

/// Result of converting UnifiedConfig to Config, including any warnings.
pub struct ConfigConversionResult {
    pub config: Config,
    pub warnings: Vec<String>,
}

impl ConfigConversionResult {
    pub fn new(config: Config) -> Self {
        Self {
            config,
            warnings: Vec::new(),
        }
    }

    pub fn with_warnings(config: Config, warnings: Vec<String>) -> Self {
        Self { config, warnings }
    }
}

/// Create a Config from `UnifiedConfig`.
pub(super) fn config_from_unified(unified: &UnifiedConfig) -> ConfigConversionResult {
    use crate::config::types::{BehavioralFlags, FeatureFlags};

    let general = &unified.general;
    let max_dev_continuations = general.max_dev_continuations;
    let max_xsd_retries = general.max_xsd_retries;
    let max_same_agent_retries = general.max_same_agent_retries;
    let max_commit_residual_retries = general.max_commit_residual_retries;

    let review_depth = ReviewDepth::from_str(&general.review_depth).unwrap_or_default();

    let warnings = if ReviewDepth::from_str(&general.review_depth).is_none() {
        vec![format!(
            "Invalid review_depth '{}' in config; falling back to 'standard'.",
            general.review_depth
        )]
    } else {
        Vec::new()
    };

    let config = Config {
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
        show_streaming_metrics: false,
        review_format_retries: 5,
        max_dev_continuations: Some(max_dev_continuations),
        max_xsd_retries: Some(max_xsd_retries),
        max_same_agent_retries: Some(max_same_agent_retries),
        max_commit_residual_retries: Some(max_commit_residual_retries),
        execution_history_limit: general.execution_history_limit,
        cloud: load_cloud_config_from_env(),
    };

    ConfigConversionResult::with_warnings(config, warnings)
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
        max_dev_continuations: Some(2),
        max_xsd_retries: Some(10),
        max_same_agent_retries: Some(2),
        max_commit_residual_retries: Some(10),
        execution_history_limit: 1000,
        cloud: load_cloud_config_from_env(),
    }
}
