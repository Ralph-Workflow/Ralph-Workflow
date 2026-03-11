//! Valid configuration keys and deprecation tracking.
//!
//! This module defines the schema of valid configuration keys across all sections.
//! It's used for detecting unknown keys and suggesting corrections.

/// Deprecated keys for the [general] section.
/// These keys are accepted for backward compatibility but their use should trigger warnings.
pub const DEPRECATED_GENERAL_KEYS: &[&str] = &[
    "auto_rebase",           // Never implemented, removed in favor of manual git control
    "max_recovery_attempts", // Never implemented, superseded by retry mechanisms
];

/// Valid keys for the [general] section.
pub const VALID_GENERAL_KEYS: &[&str] = &[
    "verbosity",
    "interactive",
    "auto_detect_stack",
    "strict_validation",
    "checkpoint_enabled",
    "force_universal_prompt",
    "isolation_mode",
    "developer_iters",
    "reviewer_reviews",
    "developer_context",
    "reviewer_context",
    "review_depth",
    "prompt_path",
    "templates_dir",
    "git_user_name",
    "git_user_email",
    "max_dev_continuations",
    "max_xsd_retries",
    "max_same_agent_retries",
    "behavior",
    "workflow",
    "execution",
    // Note: Deprecated keys (auto_rebase, max_recovery_attempts) are included here
    // to avoid breaking existing configs. They trigger warnings, not errors.
    "auto_rebase",
    "max_recovery_attempts",
];

/// Valid keys for the [ccs] section.
pub const VALID_CCS_KEYS: &[&str] = &[
    "output_flag",
    "yolo_flag",
    "verbose_flag",
    "print_flag",
    "streaming_flag",
    "json_parser",
    "session_flag",
    "can_commit",
];

/// Valid keys for agent configurations (within [agents.<name>]).
pub const VALID_AGENT_CONFIG_KEYS: &[&str] = &[
    "cmd",
    "output_flag",
    "yolo_flag",
    "verbose_flag",
    "print_flag",
    "streaming_flag",
    "session_flag",
    "can_commit",
    "json_parser",
    "model_flag",
    "display_name",
];

/// Valid keys for CCS alias configurations (within [`ccs_aliases`.<name>]).
pub const VALID_CCS_ALIAS_CONFIG_KEYS: &[&str] = &[
    "cmd",
    "output_flag",
    "yolo_flag",
    "verbose_flag",
    "print_flag",
    "streaming_flag",
    "json_parser",
    "session_flag",
    "can_commit",
    "model_flag",
];

/// Valid keys for the [`agent_chain`] section.
///
/// This must match all fields in `FallbackConfig` from agents/fallback.rs.
pub const VALID_AGENT_CHAIN_KEYS: &[&str] = &[
    "developer",
    "reviewer",
    "commit",
    "analysis",
    "provider_fallback",
    "max_retries",
    "retry_delay_ms",
    "backoff_multiplier",
    "max_backoff_ms",
    "max_cycles",
];

/// Valid keys for the [`agent_drains`] section.
pub const VALID_AGENT_DRAIN_KEYS: &[&str] = &[
    "planning",
    "development",
    "review",
    "fix",
    "commit",
    "analysis",
];

/// Get all valid configuration keys for typo detection.
///
/// Returns a flat list of all valid key names across all sections.
pub fn get_valid_config_keys() -> Vec<&'static str> {
    vec![
        // Top-level sections
        "general",
        "ccs",
        "agents",
        "ccs_aliases",
        "agent_chain",
        "agent_chains",
        "agent_drains",
        // General config keys
        "verbosity",
        "interactive",
        "auto_detect_stack",
        "strict_validation",
        "checkpoint_enabled",
        "force_universal_prompt",
        "isolation_mode",
        "developer_iters",
        "reviewer_reviews",
        "developer_context",
        "reviewer_context",
        "review_depth",
        "prompt_path",
        "templates_dir",
        "git_user_name",
        "git_user_email",
        "max_dev_continuations",
        "max_xsd_retries",
        "max_same_agent_retries",
        // Behavior flags (nested)
        "behavior",
        // Workflow flags (nested)
        "workflow",
        // Execution flags (nested)
        "execution",
        // CCS config keys
        "output_flag",
        "yolo_flag",
        "verbose_flag",
        "print_flag",
        "streaming_flag",
        "json_parser",
        "session_flag",
        "can_commit",
        // Agent config keys
        "cmd",
        "model_flag",
        "display_name",
        // CCS alias config keys
        "ccs_aliases",
        // Drain config keys
        "planning",
        "development",
        "review",
        "fix",
        "commit",
        "analysis",
    ]
}
