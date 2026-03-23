use super::super::parser::parse_env_bool;
use super::super::path_resolver::ConfigEnvironment;
use super::env_parsing::{parse_env_u32, parse_env_u8};
use crate::config::types::{BehavioralFlags, Config, FeatureFlags, ReviewDepth, Verbosity};

/// Result of applying environment overrides, including any warnings.
#[derive(Debug, Clone, PartialEq)]
pub struct EnvOverrideResult {
    pub config: Config,
    pub warnings: Vec<String>,
}

impl EnvOverrideResult {
    pub fn new(config: Config) -> Self {
        Self {
            config,
            warnings: Vec::new(),
        }
    }

    pub fn with_warnings(config: Config, warnings: Vec<String>) -> Self {
        Self { config, warnings }
    }

    pub fn with_warning(self, warning: impl Into<String>) -> Self {
        let new_warnings = self
            .warnings
            .into_iter()
            .chain(std::iter::once(warning.into()))
            .collect();
        Self {
            config: self.config,
            warnings: new_warnings,
        }
    }
}

/// Apply environment variable overrides to config (functional pattern).
///
/// Uses the injected `env` accessor instead of reading from the real process
/// environment, enabling parallel-safe unit tests without `#[serial]`.
#[must_use]
pub fn apply_env_overrides(config: Config, env: &dyn ConfigEnvironment) -> EnvOverrideResult {
    const MAX_ITERS: u32 = 50;
    const MAX_REVIEWS: u32 = 10;
    const MAX_CONTEXT: u8 = 2;
    const MAX_FORMAT_RETRIES: u32 = 20;

    // Apply all environment variable overrides by category
    let result = apply_agent_selection_env(config, env);
    let result = apply_command_env(result, env);
    let result = apply_model_provider_env(result, env);
    let result = apply_iteration_counts_env(result, MAX_ITERS, MAX_REVIEWS, env);
    let result = apply_review_config_env(result, MAX_FORMAT_RETRIES, env);
    let result = apply_boolean_flags_env(result, env);
    let result = apply_verbosity_env(result, env);
    let result = apply_review_depth_env(result, env);
    let result = apply_paths_env(result, env);
    let result = apply_context_levels_env(result, MAX_CONTEXT, env);
    apply_git_identity_env(result, env)
}

/// Apply agent selection environment variables (functional pattern).
fn apply_agent_selection_env(config: Config, env: &dyn ConfigEnvironment) -> EnvOverrideResult {
    let dev_agent_val = env.get_env_var("RALPH_DEVELOPER_AGENT");
    let rev_agent_val = env.get_env_var("RALPH_REVIEWER_AGENT");

    let developer_agent = dev_agent_val.as_ref().and_then(|val| {
        let trimmed = val.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let reviewer_agent = rev_agent_val.as_ref().and_then(|val| {
        let trimmed = val.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let warnings: Vec<String> = std::iter::empty()
        .chain(
            dev_agent_val
                .is_some_and(|v| v.trim().is_empty())
                .then_some("Env var RALPH_DEVELOPER_AGENT is empty; ignoring.".to_string()),
        )
        .chain(
            rev_agent_val
                .is_some_and(|v| v.trim().is_empty())
                .then_some("Env var RALPH_REVIEWER_AGENT is empty; ignoring.".to_string()),
        )
        .collect();

    EnvOverrideResult::with_warnings(
        Config {
            developer_agent: developer_agent.or(config.developer_agent),
            reviewer_agent: reviewer_agent.or(config.reviewer_agent),
            ..config
        },
        warnings,
    )
}

/// Apply command override environment variables (functional pattern).
fn apply_command_env(result: EnvOverrideResult, env: &dyn ConfigEnvironment) -> EnvOverrideResult {
    let dev_cmd_val = env.get_env_var("RALPH_DEVELOPER_CMD");
    let rev_cmd_val = env.get_env_var("RALPH_REVIEWER_CMD");
    let commit_cmd_val = env.get_env_var("RALPH_COMMIT_CMD");

    let developer_cmd = dev_cmd_val.as_ref().and_then(|val| {
        let trimmed = val.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let reviewer_cmd = rev_cmd_val.as_ref().and_then(|val| {
        let trimmed = val.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let commit_cmd = commit_cmd_val.as_ref().and_then(|val| {
        let trimmed = val.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    });

    let fast_check_cmd = env.get_env_var("FAST_CHECK_CMD").filter(|v| !v.is_empty());

    let full_check_cmd = env.get_env_var("FULL_CHECK_CMD").filter(|v| !v.is_empty());

    let warnings: Vec<String> = std::iter::empty()
        .chain(
            dev_cmd_val
                .is_some_and(|v| v.trim().is_empty())
                .then_some("Env var RALPH_DEVELOPER_CMD is empty; ignoring.".to_string()),
        )
        .chain(
            rev_cmd_val
                .is_some_and(|v| v.trim().is_empty())
                .then_some("Env var RALPH_REVIEWER_CMD is empty; ignoring.".to_string()),
        )
        .chain(
            commit_cmd_val
                .is_some_and(|v| v.trim().is_empty())
                .then_some("Env var RALPH_COMMIT_CMD is empty; ignoring.".to_string()),
        )
        .collect();

    EnvOverrideResult::with_warnings(
        Config {
            developer_cmd: developer_cmd.or(result.config.developer_cmd),
            reviewer_cmd: reviewer_cmd.or(result.config.reviewer_cmd),
            commit_cmd: commit_cmd.or(result.config.commit_cmd),
            fast_check_cmd: fast_check_cmd.or(result.config.fast_check_cmd),
            full_check_cmd: full_check_cmd.or(result.config.full_check_cmd),
            ..result.config
        },
        warnings,
    )
}

/// Apply model and provider environment variables (functional pattern).
fn apply_model_provider_env(
    result: EnvOverrideResult,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let developer_model = env.get_env_var("RALPH_DEVELOPER_MODEL");
    let reviewer_model = env.get_env_var("RALPH_REVIEWER_MODEL");
    let developer_provider = env.get_env_var("RALPH_DEVELOPER_PROVIDER");
    let reviewer_provider = env.get_env_var("RALPH_REVIEWER_PROVIDER");
    let reviewer_json_parser = env
        .get_env_var("RALPH_REVIEWER_JSON_PARSER")
        .and_then(|val| {
            let trimmed = val.trim().to_string();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed)
            }
        });
    let force_universal_prompt = env
        .get_env_var("RALPH_REVIEWER_UNIVERSAL_PROMPT")
        .and_then(|val| parse_env_bool(&val));

    EnvOverrideResult::new(Config {
        developer_model: developer_model.or(result.config.developer_model),
        reviewer_model: reviewer_model.or(result.config.reviewer_model),
        developer_provider: developer_provider.or(result.config.developer_provider),
        reviewer_provider: reviewer_provider.or(result.config.reviewer_provider),
        reviewer_json_parser: reviewer_json_parser.or(result.config.reviewer_json_parser),
        features: force_universal_prompt
            .map(|b| FeatureFlags {
                force_universal_prompt: b,
                ..result.config.features
            })
            .unwrap_or(result.config.features),
        ..result.config
    })
}

/// Apply iteration count environment variables (functional pattern).
fn apply_iteration_counts_env(
    result: EnvOverrideResult,
    max_iters: u32,
    max_reviews: u32,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let developer_parsed =
        parse_env_u32("RALPH_DEVELOPER_ITERS", |k| env.get_env_var(k), max_iters);

    let reviewer_parsed = parse_env_u32(
        "RALPH_REVIEWER_REVIEWS",
        |k| env.get_env_var(k),
        max_reviews,
    );

    let warnings: Vec<String> = developer_parsed
        .warnings
        .into_iter()
        .chain(reviewer_parsed.warnings)
        .collect();

    EnvOverrideResult::with_warnings(
        Config {
            developer_iters: developer_parsed
                .value
                .unwrap_or(result.config.developer_iters),
            reviewer_reviews: reviewer_parsed
                .value
                .unwrap_or(result.config.reviewer_reviews),
            ..result.config
        },
        warnings,
    )
}

/// Apply review-specific configuration environment variables (functional pattern).
fn apply_review_config_env(
    result: EnvOverrideResult,
    max_retries: u32,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let parsed = parse_env_u32(
        "RALPH_REVIEW_FORMAT_RETRIES",
        |k| env.get_env_var(k),
        max_retries,
    );

    EnvOverrideResult::with_warnings(
        Config {
            review_format_retries: parsed.value.unwrap_or(result.config.review_format_retries),
            ..result.config
        },
        parsed.warnings,
    )
}

/// Apply boolean flag environment variables (functional pattern using iterator pipeline).
fn apply_boolean_flags_env(
    result: EnvOverrideResult,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    // Read all boolean env vars first using iterator pipeline
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

    // Apply each boolean flag using functional transformation
    let config = vars
        .iter()
        .fold(result.config, |cfg, (&name, &value)| match name {
            "RALPH_INTERACTIVE" => Config {
                behavior: BehavioralFlags {
                    interactive: value,
                    ..cfg.behavior
                },
                ..cfg
            },
            "RALPH_AUTO_DETECT_STACK" => Config {
                behavior: BehavioralFlags {
                    auto_detect_stack: value,
                    ..cfg.behavior
                },
                ..cfg
            },
            "RALPH_CHECKPOINT_ENABLED" => Config {
                features: FeatureFlags {
                    checkpoint_enabled: value,
                    ..cfg.features
                },
                ..cfg
            },
            "RALPH_STRICT_VALIDATION" => Config {
                behavior: BehavioralFlags {
                    strict_validation: value,
                    ..cfg.behavior
                },
                ..cfg
            },
            "RALPH_ISOLATION_MODE" => Config {
                isolation_mode: value,
                ..cfg
            },
            _ => cfg,
        });

    EnvOverrideResult::new(config)
}

/// Apply verbosity environment variable (functional pattern).
fn apply_verbosity_env(
    result: EnvOverrideResult,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let Some(val) = env.get_env_var("RALPH_VERBOSITY") else {
        return result;
    };
    let trimmed = val.trim().to_string();
    if trimmed.is_empty() {
        return result;
    }

    match trimmed.parse::<u8>() {
        Ok(n) => {
            let warnings = (n > 4)
                .then_some(vec![format!(
                    "Env var RALPH_VERBOSITY={n} is out of range; clamping to 4 (debug)."
                )])
                .unwrap_or_default();

            EnvOverrideResult::with_warnings(
                Config {
                    verbosity: Verbosity::from(n.min(4)),
                    ..result.config
                },
                warnings,
            )
        }
        Err(_) => EnvOverrideResult::new(result.config).with_warning(format!(
            "Env var RALPH_VERBOSITY='{trimmed}' is not a valid number; ignoring."
        )),
    }
}

/// Apply review depth environment variable (functional pattern).
fn apply_review_depth_env(
    result: EnvOverrideResult,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let env_val = env.get_env_var("RALPH_REVIEW_DEPTH");

    let review_depth = env_val.as_ref().and_then(|val| {
        if val.trim().is_empty() {
            None
        } else {
            ReviewDepth::from_str(val)
        }
    });

    let warnings: Vec<String> = if review_depth.is_none() {
        env_val
            .as_ref()
            .filter(|val| !val.trim().is_empty())
            .map(|val| {
                format!(
                    "Env var RALPH_REVIEW_DEPTH='{}' is invalid; ignoring.",
                    val.trim()
                )
            })
            .into_iter()
            .collect()
    } else {
        vec![]
    };

    EnvOverrideResult::with_warnings(
        Config {
            review_depth: review_depth.unwrap_or(result.config.review_depth),
            ..result.config
        },
        warnings,
    )
}

/// Apply path environment variables (functional pattern).
fn apply_paths_env(result: EnvOverrideResult, env: &dyn ConfigEnvironment) -> EnvOverrideResult {
    let prompt_path = env
        .get_env_var("RALPH_PROMPT_PATH")
        .map(std::path::PathBuf::from);

    let user_templates_dir = env.get_env_var("RALPH_TEMPLATES_DIR").and_then(|val| {
        let trimmed = val.trim().to_string();
        if trimmed.is_empty() {
            None
        } else {
            Some(std::path::PathBuf::from(trimmed))
        }
    });

    EnvOverrideResult::new(Config {
        prompt_path: prompt_path.unwrap_or(result.config.prompt_path),
        user_templates_dir: user_templates_dir.or(result.config.user_templates_dir),
        ..result.config
    })
}

/// Apply context level environment variables (functional pattern).
fn apply_context_levels_env(
    result: EnvOverrideResult,
    max_context: u8,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let developer_parsed = parse_env_u8(
        "RALPH_DEVELOPER_CONTEXT",
        |k| env.get_env_var(k),
        max_context,
    );

    let reviewer_parsed = parse_env_u8(
        "RALPH_REVIEWER_CONTEXT",
        |k| env.get_env_var(k),
        max_context,
    );

    let warnings: Vec<String> = result
        .warnings
        .into_iter()
        .chain(developer_parsed.warnings)
        .chain(reviewer_parsed.warnings)
        .collect();

    EnvOverrideResult::with_warnings(
        Config {
            developer_context: developer_parsed
                .value
                .unwrap_or(result.config.developer_context),
            reviewer_context: reviewer_parsed
                .value
                .unwrap_or(result.config.reviewer_context),
            ..result.config
        },
        warnings,
    )
}

/// Apply git user identity environment variables (functional pattern).
fn apply_git_identity_env(
    result: EnvOverrideResult,
    env: &dyn ConfigEnvironment,
) -> EnvOverrideResult {
    let git_user_name = env.get_env_var("RALPH_GIT_USER_NAME").and_then(|val| {
        let trimmed = val.trim().to_string();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    });

    let git_user_email = env.get_env_var("RALPH_GIT_USER_EMAIL").and_then(|val| {
        let trimmed = val.trim().to_string();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    });

    EnvOverrideResult::new(Config {
        git_user_name: git_user_name.or(result.config.git_user_name),
        git_user_email: git_user_email.or(result.config.git_user_email),
        ..result.config
    })
}
