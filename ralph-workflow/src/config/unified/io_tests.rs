// Unified config tests.
//
// Split into topic-specific test modules for maintainability.

use super::*;
use crate::config::path_resolver::MemoryConfigEnvironment;
use crate::config::types::Verbosity;
use std::collections::HashMap;
use std::path::Path;

fn get_ccs_alias_cmd(config: &UnifiedConfig, alias: &str) -> Option<String> {
    config.ccs_aliases.get(alias).map(|v| v.as_config().cmd)
}

#[test]
fn test_load_with_env_reads_from_config_environment() {
    let toml_str = r"
[general]
verbosity = 3
interactive = false
developer_iters = 10
";
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", toml_str);

    let config = UnifiedConfig::load_with_env(&env).unwrap();

    assert_eq!(config.general.verbosity, 3);
    assert!(!config.general.behavior.interactive);
    assert_eq!(config.general.developer_iters, 10);
}

#[test]
fn test_load_with_env_returns_none_when_no_config_path() {
    let env = MemoryConfigEnvironment::new();
    // No unified_config_path set

    let result = UnifiedConfig::load_with_env(&env);

    assert!(result.is_none());
}

#[test]
fn test_load_with_env_returns_none_when_file_missing() {
    let env =
        MemoryConfigEnvironment::new().with_unified_config_path("/test/config/ralph-workflow.toml");
    // Path set but file doesn't exist

    let result = UnifiedConfig::load_with_env(&env);

    assert!(result.is_none());
}

#[test]
fn test_load_from_path_with_env() {
    let toml_str = r"
[general]
verbosity = 4
";
    let env = MemoryConfigEnvironment::new().with_file("/custom/path.toml", toml_str);

    let config =
        UnifiedConfig::load_from_path_with_env(Path::new("/custom/path.toml"), &env).unwrap();

    assert_eq!(config.general.verbosity, 4);
}

#[test]
fn test_ensure_config_exists_with_env_creates_file() {
    let env =
        MemoryConfigEnvironment::new().with_unified_config_path("/test/config/ralph-workflow.toml");

    let result = UnifiedConfig::ensure_config_exists_with_env(&env).unwrap();

    assert_eq!(result, ConfigInitResult::Created);
    assert!(env.was_written(Path::new("/test/config/ralph-workflow.toml")));
}

#[test]
fn test_ensure_config_exists_with_env_skips_existing() {
    let env = MemoryConfigEnvironment::new()
        .with_unified_config_path("/test/config/ralph-workflow.toml")
        .with_file("/test/config/ralph-workflow.toml", "existing content");

    let result = UnifiedConfig::ensure_config_exists_with_env(&env).unwrap();

    assert_eq!(result, ConfigInitResult::AlreadyExists);
    // Content should be unchanged
    assert_eq!(
        env.get_file(Path::new("/test/config/ralph-workflow.toml")),
        Some("existing content".to_string())
    );
}

#[test]
fn test_general_config_defaults() {
    let config = GeneralConfig::default();
    assert_eq!(config.verbosity, 2);
    assert!(config.behavior.interactive);
    assert!(config.execution.isolation_mode);
    assert!(config.behavior.auto_detect_stack);
    assert!(config.workflow.checkpoint_enabled);
    assert_eq!(config.developer_iters, 5);
    assert_eq!(config.reviewer_reviews, 2);
}

#[test]
fn test_unified_config_defaults() {
    let config = UnifiedConfig::default();
    assert!(config.agents.is_empty());
    assert!(config.ccs_aliases.is_empty());
    assert!(config.agent_chain.is_none());
}

#[test]
fn test_parse_unified_config() {
    let toml_str = r#"
[general]
verbosity = 3
interactive = false
developer_iters = 10

[agents.claude]
cmd = "claude -p"
output_flag = "--output-format=stream-json"
can_commit = true
json_parser = "claude"

[ccs_aliases]
work = "ccs work"
personal = "ccs personal"
gemini = "ccs gemini"

[agent_chain]
developer = ["ccs/work", "claude"]
reviewer = ["claude"]
"#;
    let config: UnifiedConfig = toml::from_str(toml_str).unwrap();
    assert_eq!(config.general.verbosity, 3);
    assert!(!config.general.behavior.interactive);
    assert_eq!(config.general.developer_iters, 10);
    assert!(config.agents.contains_key("claude"));
    assert_eq!(
        config.ccs_aliases.get("work").unwrap().as_config().cmd,
        "ccs work"
    );
    assert_eq!(
        config.ccs_aliases.get("personal").unwrap().as_config().cmd,
        "ccs personal"
    );
    assert!(config.ccs_aliases.contains_key("work"));
    assert!(!config.ccs_aliases.contains_key("nonexistent"));
    let chain = config.agent_chain.expect("agent_chain should parse");
    assert_eq!(
        chain.developer,
        vec!["ccs/work".to_string(), "claude".to_string()]
    );
    assert_eq!(chain.reviewer, vec!["claude".to_string()]);
}

#[test]
fn test_ccs_alias_lookup() {
    let mut config = UnifiedConfig::default();
    config.ccs_aliases.insert(
        "work".to_string(),
        CcsAliasToml::Command("ccs work".to_string()),
    );
    config.ccs_aliases.insert(
        "gemini".to_string(),
        CcsAliasToml::Command("ccs gemini".to_string()),
    );

    assert_eq!(
        get_ccs_alias_cmd(&config, "work"),
        Some("ccs work".to_string())
    );
    assert_eq!(
        get_ccs_alias_cmd(&config, "gemini"),
        Some("ccs gemini".to_string())
    );
    assert_eq!(get_ccs_alias_cmd(&config, "nonexistent"), None);
}

#[test]
fn test_verbosity_conversion() {
    let mut config = UnifiedConfig::default();
    config.general.verbosity = 0;
    assert_eq!(Verbosity::from(config.general.verbosity), Verbosity::Quiet);
    config.general.verbosity = 4;
    assert_eq!(Verbosity::from(config.general.verbosity), Verbosity::Debug);
}

#[test]
fn test_unified_config_path() {
    // Just verify it returns something (path depends on system)
    let path = unified_config_path();
    if let Some(p) = path {
        assert!(p.to_string_lossy().contains("ralph-workflow.toml"));
    }
}

// Merge behavior tests
#[path = "io_tests/merge.rs"]
mod merge;

// Agent chain per-key merge tests
#[path = "io_tests/agent_chain_merge.rs"]
mod agent_chain_merge;

// Startup validation rule tests (Rules 1, 2, 9, 10, 11, 12, 23, 24)
#[path = "io_tests/validation_rules.rs"]
mod validation_rules;
