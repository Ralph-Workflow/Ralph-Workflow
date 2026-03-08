use super::super::parser::parse_env_bool;
use super::super::path_resolver::ConfigEnvironment;
use super::env_parsing::{parse_env_u32, parse_env_u8};
use crate::config::types::{Config, ReviewDepth, Verbosity};

/// Apply environment variable overrides to config.
///
/// Uses the injected `env` accessor instead of reading from the real process
/// environment, enabling parallel-safe unit tests without `#[serial]`.
pub fn apply_env_overrides(
    mut config: Config,
    warnings: &mut Vec<String>,
    env: &dyn ConfigEnvironment,
) -> Config {
    const MAX_ITERS: u32 = 50;
    const MAX_REVIEWS: u32 = 10;
    const MAX_CONTEXT: u8 = 2;
    const MAX_FORMAT_RETRIES: u32 = 20;

    // Apply all environment variable overrides by category
    apply_agent_selection_env(&mut config, warnings, env);
    apply_command_env(&mut config, warnings, env);
    apply_model_provider_env(&mut config, env);
    apply_iteration_counts_env(&mut config, warnings, MAX_ITERS, MAX_REVIEWS, env);
    apply_review_config_env(&mut config, warnings, MAX_FORMAT_RETRIES, env);
    apply_boolean_flags_env(&mut config, env);
    apply_verbosity_env(&mut config, warnings, env);
    apply_review_depth_env(&mut config, warnings, env);
    apply_paths_env(&mut config, env);
    apply_context_levels_env(&mut config, warnings, MAX_CONTEXT, env);
    apply_git_identity_env(&mut config, env);

    config
}

/// Apply agent selection environment variables.
fn apply_agent_selection_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    env: &dyn ConfigEnvironment,
) {
    if let Some(val) = env.get_env_var("RALPH_DEVELOPER_AGENT") {
        let trimmed = val.trim().to_string();
        if trimmed.is_empty() {
            warnings.push("Env var RALPH_DEVELOPER_AGENT is empty; ignoring.".to_string());
        } else {
            config.developer_agent = Some(trimmed);
        }
    }

    if let Some(val) = env.get_env_var("RALPH_REVIEWER_AGENT") {
        let trimmed = val.trim().to_string();
        if trimmed.is_empty() {
            warnings.push("Env var RALPH_REVIEWER_AGENT is empty; ignoring.".to_string());
        } else {
            config.reviewer_agent = Some(trimmed);
        }
    }
}

/// Apply command override environment variables.
fn apply_command_env(config: &mut Config, warnings: &mut Vec<String>, env: &dyn ConfigEnvironment) {
    for (env_var, field) in [
        ("RALPH_DEVELOPER_CMD", &mut config.developer_cmd),
        ("RALPH_REVIEWER_CMD", &mut config.reviewer_cmd),
        ("RALPH_COMMIT_CMD", &mut config.commit_cmd),
    ] {
        if let Some(val) = env.get_env_var(env_var) {
            let trimmed = val.trim().to_string();
            if trimmed.is_empty() {
                warnings.push(format!("Env var {env_var} is empty; ignoring."));
            } else {
                *field = Some(trimmed);
            }
        }
    }

    for (env_var, field) in [
        ("FAST_CHECK_CMD", &mut config.fast_check_cmd),
        ("FULL_CHECK_CMD", &mut config.full_check_cmd),
    ] {
        if let Some(val) = env.get_env_var(env_var) {
            if !val.is_empty() {
                *field = Some(val);
            }
        }
    }
}

/// Apply model and provider environment variables.
fn apply_model_provider_env(config: &mut Config, env: &dyn ConfigEnvironment) {
    for (env_var, field) in [
        ("RALPH_DEVELOPER_MODEL", &mut config.developer_model),
        ("RALPH_REVIEWER_MODEL", &mut config.reviewer_model),
        ("RALPH_DEVELOPER_PROVIDER", &mut config.developer_provider),
        ("RALPH_REVIEWER_PROVIDER", &mut config.reviewer_provider),
    ] {
        if let Some(val) = env.get_env_var(env_var) {
            *field = Some(val);
        }
    }

    // JSON parser override for reviewer (useful for testing different parsers)
    if let Some(val) = env.get_env_var("RALPH_REVIEWER_JSON_PARSER") {
        let trimmed = val.trim().to_string();
        if !trimmed.is_empty() {
            config.reviewer_json_parser = Some(trimmed);
        }
    }

    // Force universal review prompt (useful for problematic agents)
    if let Some(val) = env.get_env_var("RALPH_REVIEWER_UNIVERSAL_PROMPT") {
        if let Some(b) = parse_env_bool(&val) {
            config.features.force_universal_prompt = b;
        }
    }
}

/// Apply iteration count environment variables.
fn apply_iteration_counts_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    max_iters: u32,
    max_reviews: u32,
    env: &dyn ConfigEnvironment,
) {
    if let Some(n) = parse_env_u32(
        "RALPH_DEVELOPER_ITERS",
        |k| env.get_env_var(k),
        warnings,
        max_iters,
    ) {
        config.developer_iters = n;
    }
    if let Some(n) = parse_env_u32(
        "RALPH_REVIEWER_REVIEWS",
        |k| env.get_env_var(k),
        warnings,
        max_reviews,
    ) {
        config.reviewer_reviews = n;
    }
}

/// Apply review-specific configuration environment variables.
fn apply_review_config_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    max_retries: u32,
    env: &dyn ConfigEnvironment,
) {
    if let Some(n) = parse_env_u32(
        "RALPH_REVIEW_FORMAT_RETRIES",
        |k| env.get_env_var(k),
        warnings,
        max_retries,
    ) {
        config.review_format_retries = n;
    }
}

/// Apply boolean flag environment variables.
fn apply_boolean_flags_env(config: &mut Config, env: &dyn ConfigEnvironment) {
    // Read all boolean env vars first
    let vars: std::collections::HashMap<&str, bool> = [
        "RALPH_INTERACTIVE",
        "RALPH_AUTO_DETECT_STACK",
        "RALPH_CHECKPOINT_ENABLED",
        "RALPH_STRICT_VALIDATION",
        "RALPH_ISOLATION_MODE",
    ]
    .iter()
    .filter_map(|&name| env.get_env_var(name).map(|v| (name, v)))
    .filter_map(|(name, val)| parse_env_bool(&val).map(|b| (name, b)))
    .collect();

    // Apply each boolean flag
    for (name, value) in vars {
        match name {
            "RALPH_INTERACTIVE" => config.behavior.interactive = value,
            "RALPH_AUTO_DETECT_STACK" => config.behavior.auto_detect_stack = value,
            "RALPH_CHECKPOINT_ENABLED" => config.features.checkpoint_enabled = value,
            "RALPH_STRICT_VALIDATION" => config.behavior.strict_validation = value,
            "RALPH_ISOLATION_MODE" => config.isolation_mode = value,
            _ => {}
        }
    }
}

/// Apply verbosity environment variable.
fn apply_verbosity_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    env: &dyn ConfigEnvironment,
) {
    let Some(val) = env.get_env_var("RALPH_VERBOSITY") else {
        return;
    };
    let trimmed = val.trim().to_string();
    if trimmed.is_empty() {
        return;
    }
    match trimmed.parse::<u8>() {
        Ok(n) => {
            if n > 4 {
                warnings.push(format!(
                    "Env var RALPH_VERBOSITY={n} is out of range; clamping to 4 (debug)."
                ));
            }
            config.verbosity = Verbosity::from(n.min(4));
        }
        Err(_) => {
            warnings.push(format!(
                "Env var RALPH_VERBOSITY='{trimmed}' is not a valid number; ignoring."
            ));
        }
    }
}

/// Apply review depth environment variable.
fn apply_review_depth_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    env: &dyn ConfigEnvironment,
) {
    if let Some(val) = env.get_env_var("RALPH_REVIEW_DEPTH") {
        if let Some(depth) = ReviewDepth::from_str(&val) {
            config.review_depth = depth;
        } else if !val.trim().is_empty() {
            warnings.push(format!(
                "Env var RALPH_REVIEW_DEPTH='{}' is invalid; ignoring.",
                val.trim()
            ));
        }
    }
}

/// Apply path environment variables.
fn apply_paths_env(config: &mut Config, env: &dyn ConfigEnvironment) {
    if let Some(val) = env.get_env_var("RALPH_PROMPT_PATH") {
        config.prompt_path = std::path::PathBuf::from(val);
    }
    if let Some(val) = env.get_env_var("RALPH_TEMPLATES_DIR") {
        let trimmed = val.trim().to_string();
        if !trimmed.is_empty() {
            config.user_templates_dir = Some(std::path::PathBuf::from(trimmed));
        }
    }
}

/// Apply context level environment variables.
fn apply_context_levels_env(
    config: &mut Config,
    warnings: &mut Vec<String>,
    max_context: u8,
    env: &dyn ConfigEnvironment,
) {
    if let Some(n) = parse_env_u8(
        "RALPH_DEVELOPER_CONTEXT",
        |k| env.get_env_var(k),
        warnings,
        max_context,
    ) {
        config.developer_context = n;
    }
    if let Some(n) = parse_env_u8(
        "RALPH_REVIEWER_CONTEXT",
        |k| env.get_env_var(k),
        warnings,
        max_context,
    ) {
        config.reviewer_context = n;
    }
}

/// Apply git user identity environment variables.
fn apply_git_identity_env(config: &mut Config, env: &dyn ConfigEnvironment) {
    if let Some(val) = env.get_env_var("RALPH_GIT_USER_NAME") {
        let trimmed = val.trim().to_string();
        if !trimmed.is_empty() {
            config.git_user_name = Some(trimmed);
        }
    }
    if let Some(val) = env.get_env_var("RALPH_GIT_USER_EMAIL") {
        let trimmed = val.trim().to_string();
        if !trimmed.is_empty() {
            config.git_user_email = Some(trimmed);
        }
    }
}
