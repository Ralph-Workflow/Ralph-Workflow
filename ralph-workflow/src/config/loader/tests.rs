use super::*;
use crate::config::loader::ConfigLoadWithValidationError;
use crate::config::path_resolver::MemoryConfigEnvironment;
use crate::config::validation::ConfigValidationError;
use crate::config::Verbosity;
use std::path::Path;

#[test]
fn test_load_config_with_env_from_custom_path() {
    let toml_str = r#"
[general]
verbosity = 3
interactive = false
developer_iters = 10
review_depth = "standard"
"#;
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/custom/config.toml", toml_str);

    let Ok((config, unified, warnings)) =
        load_config_from_path_with_env(Some(Path::new("/custom/config.toml")), &env)
    else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert!(warnings.is_empty(), "Unexpected warnings: {warnings:?}");
    assert!(unified.is_some());
    assert_eq!(config.developer_iters, 10);
    assert!(!config.behavior.interactive);
}

#[test]
fn test_load_config_with_env_missing_file() {
    let env =
        MemoryConfigEnvironment::new().with_unified_config_path("/test/config/ralph-workflow.toml");

    let Ok((config, unified, warnings)) =
        load_config_from_path_with_env(Some(Path::new("/missing/config.toml")), &env)
    else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert!(unified.is_none());
    assert_eq!(warnings.len(), 1);
    assert!(warnings[0].contains("not found"));
    // Should fall back to defaults
    assert_eq!(config.developer_iters, 5);
}

#[test]
fn test_load_config_with_env_from_default_path() {
    let toml_str = r#"
[general]
verbosity = 4
developer_iters = 8
review_depth = "standard"
"#;
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let Ok((config, unified, warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert!(warnings.is_empty(), "Unexpected warnings: {warnings:?}");
    assert!(unified.is_some());
    assert_eq!(config.developer_iters, 8);
    assert_eq!(config.verbosity, Verbosity::Debug);
}

#[test]
fn test_default_config() {
    let config = default_config();
    assert!(config.developer_agent.is_none());
    assert!(config.reviewer_agent.is_none());
    assert_eq!(config.developer_iters, 5);
    assert_eq!(config.reviewer_reviews, 2);
    assert!(config.behavior.interactive);
    assert!(config.isolation_mode);
    assert_eq!(config.verbosity, Verbosity::Verbose);
    assert_eq!(config.max_same_agent_retries, Some(2));
}

#[test]
fn test_apply_env_overrides() {
    // Arrange: inject env vars via MemoryConfigEnvironment (no process-global state)
    let env = MemoryConfigEnvironment::new()
        .with_env_var("RALPH_DEVELOPER_ITERS", "10")
        .with_env_var("RALPH_ISOLATION_MODE", "false");

    // Act
    let mut warnings = Vec::new();
    let config = apply_env_overrides(default_config(), &mut warnings, &env);

    // Assert
    assert_eq!(config.developer_iters, 10);
    assert!(!config.isolation_mode);
    assert!(warnings.is_empty());
}

#[test]
fn test_unified_config_exists_with_env_returns_false_when_no_path() {
    // Test when there's no unified config path configured
    let env = MemoryConfigEnvironment::new();
    assert!(!unified_config_exists_with_env(&env));
}

#[test]
fn test_unified_config_exists_with_env_returns_false_when_file_missing() {
    // Test when path is configured but file doesn't exist
    let env =
        MemoryConfigEnvironment::new().with_unified_config_path("/test/config/ralph-workflow.toml");
    assert!(!unified_config_exists_with_env(&env));
}

#[test]
fn test_load_config_from_path_does_not_print_or_panic_on_validation_failure() {
    // Regression test: library-facing config loaders should return a typed error.
    // They must not print to stderr or panic on validation failures.
    // Uses MemoryConfigEnvironment with invalid TOML — no real filesystem or CWD change needed.

    // Arrange: local config path returns ".agent/ralph-workflow.toml" by default;
    // populate that path with invalid TOML in the in-memory store.
    let env = MemoryConfigEnvironment::new()
        .with_file(".agent/ralph-workflow.toml", "this is not valid toml = [");

    // Act
    let result = load_config_from_path_with_env(None, &env);

    // Assert: must return a typed error, not panic
    assert!(
        result.is_err(),
        "expected validation error, got: {result:?}"
    );
}

#[test]
fn test_unified_config_exists_with_env_returns_true_when_file_exists() {
    // Test when path is configured and file exists
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", "[general]");
    assert!(unified_config_exists_with_env(&env));
}

#[test]
fn test_max_dev_continuations_zero_is_valid() {
    let toml_str = r#"
[general]
verbosity = 4
developer_iters = 8
review_depth = "standard"
max_dev_continuations = 0
"#;

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let Ok((config, _unified, warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // 0 is valid and means "no continuations" (total attempts = 1).
    assert_eq!(config.max_dev_continuations, Some(0));
    assert!(
        !warnings
            .iter()
            .any(|w: &String| w.contains("max_dev_continuations")),
        "Should not warn about max_dev_continuations=0, got: {warnings:?}"
    );
}

#[test]
fn test_max_xsd_retries_zero_is_valid() {
    // max_xsd_retries=0 is valid and means "disable XSD retries" (immediate agent fallback)
    let toml_str = r#"
[general]
verbosity = 4
developer_iters = 8
review_depth = "standard"
max_xsd_retries = 0
"#;

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let Ok((config, _unified, warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // 0 should be accepted (not rejected with warning)
    assert_eq!(config.max_xsd_retries, Some(0));
    assert!(
        !warnings
            .iter()
            .any(|w: &String| w.contains("max_xsd_retries")),
        "Should not warn about max_xsd_retries=0, got: {warnings:?}"
    );
}

#[test]
fn test_max_same_agent_retries_zero_is_valid() {
    // max_same_agent_retries=0 is valid and means "disable same-agent retries"
    let toml_str = r#"
[general]
verbosity = 4
developer_iters = 8
review_depth = "standard"
max_same_agent_retries = 0
"#;

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let Ok((config, _unified, warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert_eq!(config.max_same_agent_retries, Some(0));
    assert!(
        !warnings
            .iter()
            .any(|w: &String| w.contains("max_same_agent_retries")),
        "Should not warn about max_same_agent_retries=0, got: {warnings:?}"
    );
}

#[test]
fn test_load_config_returns_defaults_without_file() {
    // Arrange: MemoryConfigEnvironment with no files and no env vars.
    // apply_env_overrides uses env.get_env_var() which returns None for all keys,
    // providing complete isolation from the real process environment without #[serial].
    let env = MemoryConfigEnvironment::new();

    // Act
    let Ok((config, _unified, _warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // Assert: defaults are applied regardless of process environment state
    assert_eq!(config.developer_iters, 5);
    assert_eq!(config.verbosity, Verbosity::Verbose);
}

#[test]
fn test_load_config_from_path_with_env_rejects_invalid_named_drain_reference() {
    let toml_str = r#"
[agent_chains]
shared_dev = ["codex"]

[agent_drains]
planning = "missing_chain"
"#;

    let env = MemoryConfigEnvironment::new().with_file(".agent/ralph-workflow.toml", toml_str);

    let result = load_config_from_path_with_env(None, &env);
    let Err(ConfigLoadWithValidationError::ValidationErrors(errors)) = result else {
        panic!("expected semantic validation failure from config loader");
    };

    assert!(
        errors.iter().any(|error| matches!(
            error,
            ConfigValidationError::InvalidValue { key, message, .. }
                if key == "agent_drains.planning" && message.contains("missing_chain")
        )),
        "expected invalid named drain binding error, got: {errors:?}"
    );
}

#[test]
fn test_load_config_with_local_override() {
    let global_toml = r"
[general]
verbosity = 2
developer_iters = 5
reviewer_reviews = 2
";

    let local_toml = r"
[general]
developer_iters = 10
reviewer_reviews = 3
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", global_toml)
        .with_file("/test/project/.agent/ralph-workflow.toml", local_toml);

    let Ok((config, _, _)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // Local overrides take effect
    assert_eq!(config.developer_iters, 10);
    assert_eq!(config.reviewer_reviews, 3);
    // Global value preserved (not overridden)
    assert_eq!(config.verbosity as u8, 2);
}

#[test]
fn test_load_config_with_explicit_path_does_not_merge_local_override() {
    let explicit_toml = r"
[general]
verbosity = 2
developer_iters = 5
reviewer_reviews = 2
";

    let local_toml = r"
[general]
developer_iters = 10
reviewer_reviews = 3
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/custom/config.toml", explicit_toml)
        .with_file("/test/project/.agent/ralph-workflow.toml", local_toml);

    let Ok((config, merged, _warnings)) =
        load_config_from_path_with_env(Some(Path::new("/custom/config.toml")), &env)
    else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert_eq!(
        config.developer_iters, 5,
        "explicit --config should not be overridden by local config"
    );
    assert_eq!(
        config.reviewer_reviews, 2,
        "explicit --config should not be overridden by local config"
    );

    let unified = merged.expect("expected merged unified config from explicit path");
    assert_eq!(
        unified.general.developer_iters, 5,
        "merged unified config should come from explicit --config only"
    );
    assert_eq!(
        unified.general.reviewer_reviews, 2,
        "merged unified config should come from explicit --config only"
    );
}

#[test]
fn test_load_config_with_explicit_path_ignores_invalid_local_config() {
    let explicit_toml = r"
[general]
verbosity = 2
developer_iters = 6
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/custom/config.toml", explicit_toml)
        .with_file(
            "/test/project/.agent/ralph-workflow.toml",
            "[general\ndeveloper_iters = 10",
        );

    let result = load_config_from_path_with_env(Some(Path::new("/custom/config.toml")), &env);
    assert!(
        result.is_ok(),
        "explicit --config should ignore invalid local config; got: {result:?}"
    );

    let (config, _merged, _warnings) = result.expect("explicit config load should succeed");
    assert_eq!(config.developer_iters, 6);
}

#[test]
fn test_load_config_local_only() {
    let local_toml = r"
[general]
verbosity = 4
developer_iters = 8
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/project/.agent/ralph-workflow.toml", local_toml);
    // Global config doesn't exist

    let Ok((config, _, _)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert_eq!(config.verbosity as u8, 4);
    assert_eq!(config.developer_iters, 8);
}

#[test]
fn test_load_config_global_only_no_local() {
    let global_toml = r"
[general]
verbosity = 3
developer_iters = 7
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", global_toml);
    // Local config doesn't exist

    let Ok((config, _, _)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    assert_eq!(config.verbosity as u8, 3);
    assert_eq!(config.developer_iters, 7);
}

#[test]
fn test_load_config_global_only_partial_agent_chain_uses_built_in_missing_roles() {
    let global_toml = r#"
[general]
verbosity = 3

[agent_chain]
developer = ["codex"]
"#;

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", global_toml);
    // Local config doesn't exist

    let Ok((_config, merged, _warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    let unified = merged.expect("merged unified config should exist");
    let chain = unified.agent_chain.expect("agent_chain should exist");

    let builtins = crate::agents::AgentRegistry::new()
        .expect("built-in registry should load")
        .fallback_config();

    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(
        chain.reviewer, builtins.reviewer,
        "missing global reviewer chain should fall back to built-in defaults"
    );
}

#[test]
fn test_load_config_global_partial_agent_chain_with_local_config_but_no_local_agent_chain_uses_built_in_missing_roles(
) {
    let global_toml = r#"
[general]
verbosity = 3

[agent_chain]
developer = ["codex"]
"#;

    let local_toml = r"
[general]
developer_iters = 9
";

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", global_toml)
        .with_file("/test/project/.agent/ralph-workflow.toml", local_toml);

    let Ok((_config, merged, _warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    let unified = merged.expect("merged unified config should exist");
    let chain = unified.agent_chain.expect("agent_chain should exist");

    let builtins = crate::agents::AgentRegistry::new()
        .expect("built-in registry should load")
        .fallback_config();

    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(
        chain.reviewer, builtins.reviewer,
        "missing reviewer chain should fall back to built-in defaults when local config omits agent_chain"
    );
    assert_eq!(
        chain.commit, builtins.commit,
        "missing commit chain should fall back to built-in defaults when local config omits agent_chain"
    );
}

#[test]
fn test_load_config_precedence_env_vars_override_local() {
    let global_toml = r"
[general]
developer_iters = 5
";

    let local_toml = r"
[general]
developer_iters = 10
";

    // Arrange: inject env var via with_env_var() — no process-global state mutation
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", global_toml)
        .with_file("/test/project/.agent/ralph-workflow.toml", local_toml)
        .with_env_var("RALPH_DEVELOPER_ITERS", "15");

    // Act
    let Ok((config, _, _)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // Assert: env var wins over local config
    assert_eq!(config.developer_iters, 15);
}

/// Regression test for infinite continuation loop bug (wt-39).
///
/// CRITICAL: This test verifies that `default_config()` always sets `max_dev_continuations`
/// to Some(2), ensuring bounded continuation even when config files are missing.
///
/// Without this default, the system could allow infinite continuation loops when
/// `max_dev_continuations` is omitted from config files.
#[test]
fn test_default_config_sets_continuation_limits() {
    let config = default_config();

    // CRITICAL: default_config() MUST set max_dev_continuations to Some(2)
    assert_eq!(
        config.max_dev_continuations,
        Some(2),
        "default_config() must set max_dev_continuations to Some(2) to prevent infinite continuation loops"
    );

    // Also verify other limits are set
    assert_eq!(
        config.max_xsd_retries,
        Some(10),
        "default_config() must set max_xsd_retries to Some(10)"
    );
    assert_eq!(
        config.max_same_agent_retries,
        Some(2),
        "default_config() must set max_same_agent_retries to Some(2)"
    );
    assert_eq!(
        config.max_commit_residual_retries,
        Some(10),
        "default_config() must set max_commit_residual_retries to Some(10)"
    );
}

/// Regression test for infinite continuation loop bug (wt-39).
///
/// CRITICAL: This test verifies that when `max_dev_continuations` is omitted from
/// a config file, the serde default is applied correctly.
///
/// When the key is missing, `UnifiedConfig` should apply `default_max_dev_continuations()` = 2,
/// which then gets wrapped in `Some()` when converting to Config.
#[test]
fn test_missing_max_dev_continuations_key_applies_serde_default() {
    // Config file with max_dev_continuations omitted
    let toml_str = r#"
[general]
verbosity = 2
developer_iters = 5
review_depth = "standard"
"#;

    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let Ok((config, _unified, warnings)) = load_config_from_path_with_env(None, &env) else {
        panic!("load_config_from_path_with_env should succeed");
    };

    // CRITICAL: When key is missing, serde should apply default_max_dev_continuations() = 2
    assert_eq!(
        config.max_dev_continuations,
        Some(2),
        "Missing max_dev_continuations key must default to Some(2) via serde default"
    );

    assert!(
        warnings.is_empty(),
        "Should not warn about missing max_dev_continuations (serde default applies): {warnings:?}"
    );

    assert_eq!(
        config.max_commit_residual_retries,
        Some(10),
        "Missing max_commit_residual_retries key must default to Some(10) via serde default"
    );
}
