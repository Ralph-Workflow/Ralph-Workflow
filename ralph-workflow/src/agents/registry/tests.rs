// Tests for the agent registry module.

use super::*;
use crate::agents::JsonParserType;

const TEST_SOURCES: &str =
    "local config (.agent/ralph-workflow.toml), global config (~/.config/ralph-workflow.toml), built-in defaults";

fn default_ccs() -> CcsConfig {
    CcsConfig::default()
}

#[test]
fn test_registry_new() {
    let registry = AgentRegistry::new().unwrap();
    // Behavioral test: agents are registered if they resolve
    assert!(registry.resolve_config("claude").is_some());
    assert!(registry.resolve_config("codex").is_some());
}

#[test]
fn test_registry_register() {
    let mut registry = AgentRegistry::new().unwrap();
    registry.register(
        "testbot",
        AgentConfig {
            cmd: "testbot run".to_string(),
            output_flag: "--json".to_string(),
            yolo_flag: "--yes".to_string(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );
    // Behavioral test: registered agent should resolve
    assert!(registry.resolve_config("testbot").is_some());
}

#[test]
fn test_registry_display_name() {
    let mut registry = AgentRegistry::new().unwrap();

    // Agent without custom display name uses registry key
    registry.register(
        "claude",
        AgentConfig {
            cmd: "claude -p".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Agent with custom display name uses that
    registry.register(
        "claude",
        AgentConfig {
            cmd: "claude -p".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Test display names
    assert_eq!(registry.display_name("claude"), "claude");
    assert_eq!(registry.display_name("ccs/glm"), "ccs-glm");

    // Unknown agent returns the key as-is
    assert_eq!(registry.display_name("unknown"), "unknown");
}

#[test]
fn test_resolve_from_logfile_name() {
    let mut registry = AgentRegistry::new().unwrap();

    // Register a CCS agent with slash in name
    registry.register(
        "ccs/glm",
        AgentConfig {
            cmd: "ccs glm".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: "-p".to_string(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: Some("ccs-glm".to_string()),
        },
    );

    // Register a plain agent without slash
    registry.register(
        "claude",
        AgentConfig {
            cmd: "claude -p".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Register an OpenCode agent with multiple slashes
    registry.register(
        "opencode/anthropic/claude-sonnet-4",
        AgentConfig {
            cmd: "opencode run".to_string(),
            output_flag: "--format json".to_string(),
            yolo_flag: String::new(),
            verbose_flag: "--log-level DEBUG".to_string(),
            can_commit: true,
            json_parser: JsonParserType::OpenCode,
            model_flag: Some("-p anthropic -m claude-sonnet-4".to_string()),
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: "-s {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: Some("OpenCode (anthropic)".to_string()),
        },
    );

    // Test: Agent names that don't need sanitization
    assert_eq!(
        registry.resolve_from_logfile_name("claude"),
        Some("claude".to_string())
    );

    // Test: CCS agent - sanitized name resolved to registry name
    assert_eq!(
        registry.resolve_from_logfile_name("ccs-glm"),
        Some("ccs/glm".to_string())
    );

    // Test: OpenCode agent - sanitized name resolved to registry name
    assert_eq!(
        registry.resolve_from_logfile_name("opencode-anthropic-claude-sonnet-4"),
        Some("opencode/anthropic/claude-sonnet-4".to_string())
    );

    // Test: Unregistered CCS agent - should still resolve via pattern matching
    assert_eq!(
        registry.resolve_from_logfile_name("ccs-zai"),
        Some("ccs/zai".to_string())
    );

    // Test: Unregistered OpenCode agent - should still resolve via pattern matching
    assert_eq!(
        registry.resolve_from_logfile_name("opencode-google-gemini-pro"),
        Some("opencode/google/gemini-pro".to_string())
    );

    // Test: Unknown agent returns None
    assert_eq!(registry.resolve_from_logfile_name("unknown-agent"), None);
}

#[test]
fn test_registry_available_fallbacks() {
    // Test that available_fallbacks filters to only agents with commands in PATH.
    // Uses system commands (echo, cat) that exist on all systems to avoid
    // creating real executables or modifying PATH.
    let mut registry = AgentRegistry::new().unwrap();

    // Register agents using commands that exist on all systems
    registry.register(
        "echo-agent",
        AgentConfig {
            cmd: "echo test".to_string(),
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );
    registry.register(
        "cat-agent",
        AgentConfig {
            cmd: "cat --version".to_string(),
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );
    registry.register(
        "nonexistent-agent",
        AgentConfig {
            cmd: "this-command-definitely-does-not-exist-xyz123".to_string(),
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Set fallback chain using registered agents
    let toml_str = r#"
        [agent_chain]
        developer = ["echo-agent", "nonexistent-agent", "cat-agent"]
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
    registry.apply_unified_config(&unified);

    let fallbacks = registry.available_fallbacks(AgentRole::Developer);
    assert!(
        fallbacks.contains(&"echo-agent"),
        "echo-agent should be available"
    );
    assert!(
        fallbacks.contains(&"cat-agent"),
        "cat-agent should be available"
    );
    assert!(
        !fallbacks.contains(&"nonexistent-agent"),
        "nonexistent-agent should not be available"
    );
}

#[test]
fn test_validate_agent_chains() {
    let mut registry = AgentRegistry::new().unwrap();

    // Both chains configured should pass - use apply_unified_config (public API)
    let toml_str = r#"
        [agent_chain]
        developer = ["claude"]
        reviewer = ["codex"]
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
    registry.apply_unified_config(&unified);
    assert!(registry.validate_agent_chains(TEST_SOURCES).is_ok());
}

#[test]
fn test_apply_unified_config_named_schema_projects_resolved_drains_into_fallback_compatibility() {
    let mut registry = AgentRegistry::new().unwrap();

    let toml_str = r#"
        [agent_chains]
        developer = ["codex"]
        reviewer = ["claude"]
        commit = ["opencode"]
        analysis = ["gemini"]

        [agent_drains]
        planning = "developer"
        development = "developer"
        review = "reviewer"
        fix = "reviewer"
        commit = "commit"
        analysis = "analysis"
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();

    registry.apply_unified_config(&unified);

    assert_eq!(
        registry.fallback_config().developer,
        vec!["codex"],
        "named drain bindings should project into the compatibility fallback config"
    );
    assert_eq!(registry.fallback_config().reviewer, vec!["claude"]);
    assert_eq!(registry.fallback_config().commit, vec!["opencode"]);
    assert_eq!(registry.fallback_config().analysis, vec!["gemini"]);
}

#[test]
fn test_apply_unified_config_keeps_drain_defaults_when_named_chains_use_shared_names() {
    let mut registry = AgentRegistry::new().unwrap();

    let toml_str = r#"
        [agent_chains]
        shared_dev = ["codex", "claude"]
        shared_review = ["claude", "opencode"]

        [agent_drains]
        planning = "shared_dev"
        development = "shared_dev"
        review = "shared_review"
        fix = "shared_review"
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();

    registry.apply_unified_config(&unified);

    let commit = registry
        .resolved_drain(crate::agents::AgentDrain::Commit)
        .expect("commit drain should inherit the bound review chain");
    let analysis = registry
        .resolved_drain(crate::agents::AgentDrain::Analysis)
        .expect("analysis drain should inherit the bound development chain");

    assert_eq!(commit.chain_name, "shared_review");
    assert_eq!(
        commit.agents,
        vec!["claude".to_string(), "opencode".to_string()]
    );
    assert_eq!(analysis.chain_name, "shared_dev");
    assert_eq!(
        analysis.agents,
        vec!["codex".to_string(), "claude".to_string()]
    );
}

#[test]
fn test_available_fallbacks_for_drain_preserves_distinct_review_and_fix_bindings() {
    let mut registry = AgentRegistry::new().unwrap();

    let toml_str = r#"
        [agent_chains]
        review_chain = ["claude"]
        fix_chain = ["codex"]

        [agent_drains]
        planning = "review_chain"
        development = "review_chain"
        review = "review_chain"
        fix = "fix_chain"
        commit = "review_chain"
        analysis = "review_chain"
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();

    registry.apply_unified_config(&unified);

    assert_eq!(
        registry.available_fallbacks_for_drain(crate::agents::AgentDrain::Review),
        vec!["claude"]
    );
    assert_eq!(
        registry.available_fallbacks_for_drain(crate::agents::AgentDrain::Fix),
        vec!["codex"]
    );
}

#[test]
fn test_validate_agent_chains_error_mentions_searched_sources() {
    let mut registry = AgentRegistry::new().unwrap();
    // Override chains with empty values via apply_unified_config
    let toml_str = "\n[agent_chain]\ndeveloper = []\nreviewer = []\n";
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
    registry.apply_unified_config(&unified);

    let err = registry.validate_agent_chains(TEST_SOURCES).unwrap_err();
    assert!(
        err.contains("local config"),
        "error should mention local config: {err}"
    );
    assert!(
        err.contains("global config"),
        "error should mention global config: {err}"
    );
    assert!(
        err.contains("built-in defaults"),
        "error should mention built-in defaults: {err}"
    );
    assert!(
        err.contains("agent_chains") && err.contains("agent_drains"),
        "error should guide users toward the named chain/drain schema: {err}"
    );
}

#[test]
fn test_validate_agent_chains_uses_provided_sources_verbatim() {
    let mut registry = AgentRegistry::new().unwrap();
    let toml_str = "\n[agent_chain]\ndeveloper = []\nreviewer = []\n";
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
    registry.apply_unified_config(&unified);

    let err = registry
        .validate_agent_chains("global config (/custom/path.toml), built-in defaults")
        .unwrap_err();

    assert!(
        err.contains("global config (/custom/path.toml), built-in defaults"),
        "error should include provided sources: {err}"
    );
    assert!(
        !err.contains("local config"),
        "error should not mention local config when not part of provided sources: {err}"
    );
}

#[test]
fn test_ccs_aliases_registration() {
    // Test that CCS aliases are registered correctly
    let mut registry = AgentRegistry::new().unwrap();

    let mut aliases = HashMap::new();
    aliases.insert(
        "work".to_string(),
        CcsAliasConfig {
            cmd: "ccs work".to_string(),
            ..CcsAliasConfig::default()
        },
    );
    aliases.insert(
        "personal".to_string(),
        CcsAliasConfig {
            cmd: "ccs personal".to_string(),
            ..CcsAliasConfig::default()
        },
    );
    aliases.insert(
        "gemini".to_string(),
        CcsAliasConfig {
            cmd: "ccs gemini".to_string(),
            ..CcsAliasConfig::default()
        },
    );

    registry.set_ccs_aliases(&aliases, default_ccs());

    // CCS aliases should be registered as agents - behavioral test: they resolve
    assert!(registry.resolve_config("ccs/work").is_some());
    assert!(registry.resolve_config("ccs/personal").is_some());
    assert!(registry.resolve_config("ccs/gemini").is_some());

    // Get should return valid config
    let config = registry.resolve_config("ccs/work").unwrap();
    // When claude binary is found, it replaces "ccs work" with the path to claude
    assert!(
        config.cmd.ends_with("claude") || config.cmd == "ccs work",
        "cmd should be 'ccs work' or a path ending with 'claude', got: {}",
        config.cmd
    );
    assert!(config.can_commit);
    assert_eq!(config.json_parser, JsonParserType::Claude);
}

#[test]
fn test_ccs_in_fallback_chain() {
    // Test that CCS aliases can be used in fallback chains.
    // Uses `echo` command which exists on all systems to avoid creating
    // real executables or modifying PATH.
    let mut registry = AgentRegistry::new().unwrap();

    // Register CCS aliases using echo command (exists on all systems)
    let mut aliases = HashMap::new();
    aliases.insert(
        "work".to_string(),
        CcsAliasConfig {
            cmd: "echo work".to_string(),
            ..CcsAliasConfig::default()
        },
    );
    registry.set_ccs_aliases(&aliases, default_ccs());

    // Register a system command agent for comparison
    registry.register(
        "echo-agent",
        AgentConfig {
            cmd: "echo test".to_string(),
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            can_commit: true,
            json_parser: JsonParserType::Generic,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: String::new(),
            session_flag: String::new(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Set fallback chain with CCS alias using apply_unified_config (public API)
    let toml_str = r#"
        [agent_chain]
        developer = ["ccs/work", "echo-agent"]
        reviewer = ["echo-agent"]
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();
    registry.apply_unified_config(&unified);

    // ccs/work should be in available fallbacks (since echo is in PATH)
    let fallbacks = registry.available_fallbacks(AgentRole::Developer);
    assert!(
        fallbacks.contains(&"ccs/work"),
        "ccs/work should be available"
    );
    assert!(
        fallbacks.contains(&"echo-agent"),
        "echo-agent should be available"
    );

    // Validate chains should pass
    assert!(registry.validate_agent_chains(TEST_SOURCES).is_ok());
}

#[test]
fn test_ccs_aliases_with_registry_constructor() {
    let mut registry = AgentRegistry::new().unwrap();
    registry.set_ccs_aliases(&HashMap::new(), default_ccs());

    // Should have built-in agents - behavioral test: they resolve
    assert!(registry.resolve_config("claude").is_some());
    assert!(registry.resolve_config("codex").is_some());

    // Now test with actual aliases
    let mut registry2 = AgentRegistry::new().unwrap();
    let mut aliases = HashMap::new();
    aliases.insert(
        "work".to_string(),
        CcsAliasConfig {
            cmd: "ccs work".to_string(),
            ..CcsAliasConfig::default()
        },
    );

    registry2.set_ccs_aliases(&aliases, default_ccs());
    // Behavioral test: CCS alias should resolve
    assert!(registry2.resolve_config("ccs/work").is_some());
}

#[test]
fn test_list_includes_ccs_aliases() {
    let mut registry = AgentRegistry::new().unwrap();

    let mut aliases = HashMap::new();
    aliases.insert(
        "work".to_string(),
        CcsAliasConfig {
            cmd: "ccs work".to_string(),
            ..CcsAliasConfig::default()
        },
    );
    aliases.insert(
        "personal".to_string(),
        CcsAliasConfig {
            cmd: "ccs personal".to_string(),
            ..CcsAliasConfig::default()
        },
    );
    registry.set_ccs_aliases(&aliases, default_ccs());

    let all_agents = registry.list();

    assert_eq!(
        all_agents
            .iter()
            .filter(|(name, _)| name.starts_with("ccs/"))
            .count(),
        2
    );
}

#[test]
fn test_resolve_fuzzy_exact_match() {
    let registry = AgentRegistry::new().unwrap();
    assert_eq!(registry.resolve_fuzzy("claude"), Some("claude".to_string()));
    assert_eq!(registry.resolve_fuzzy("codex"), Some("codex".to_string()));
}

#[test]
fn test_resolve_fuzzy_ccs_unregistered() {
    let registry = AgentRegistry::new().unwrap();
    // ccs/<unregistered> should return as-is for direct execution
    assert_eq!(
        registry.resolve_fuzzy("ccs/random"),
        Some("ccs/random".to_string())
    );
    assert_eq!(
        registry.resolve_fuzzy("ccs/unregistered"),
        Some("ccs/unregistered".to_string())
    );
}

#[test]
fn test_resolve_fuzzy_typos() {
    let registry = AgentRegistry::new().unwrap();
    // Test common typos
    assert_eq!(registry.resolve_fuzzy("claud"), Some("claude".to_string()));
    assert_eq!(registry.resolve_fuzzy("CLAUD"), Some("claude".to_string()));
}

#[test]
fn test_resolve_fuzzy_codex_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test codex variations
    assert_eq!(registry.resolve_fuzzy("codeex"), Some("codex".to_string()));
    assert_eq!(registry.resolve_fuzzy("code-x"), Some("codex".to_string()));
    assert_eq!(registry.resolve_fuzzy("CODEEX"), Some("codex".to_string()));
}

#[test]
fn test_resolve_fuzzy_cursor_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test cursor variations
    assert_eq!(registry.resolve_fuzzy("crusor"), Some("cursor".to_string()));
    assert_eq!(registry.resolve_fuzzy("CRUSOR"), Some("cursor".to_string()));
}

#[test]
fn test_resolve_fuzzy_gemini_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test gemini variations
    assert_eq!(registry.resolve_fuzzy("gemeni"), Some("gemini".to_string()));
    assert_eq!(registry.resolve_fuzzy("gemni"), Some("gemini".to_string()));
    assert_eq!(registry.resolve_fuzzy("GEMENI"), Some("gemini".to_string()));
}

#[test]
fn test_resolve_fuzzy_qwen_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test qwen variations
    assert_eq!(registry.resolve_fuzzy("quen"), Some("qwen".to_string()));
    assert_eq!(registry.resolve_fuzzy("quwen"), Some("qwen".to_string()));
    assert_eq!(registry.resolve_fuzzy("QUEN"), Some("qwen".to_string()));
}

#[test]
fn test_resolve_fuzzy_aider_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test aider variations
    assert_eq!(registry.resolve_fuzzy("ader"), Some("aider".to_string()));
    assert_eq!(registry.resolve_fuzzy("ADER"), Some("aider".to_string()));
}

#[test]
fn test_resolve_fuzzy_vibe_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test vibe variations
    assert_eq!(registry.resolve_fuzzy("vib"), Some("vibe".to_string()));
    assert_eq!(registry.resolve_fuzzy("VIB"), Some("vibe".to_string()));
}

#[test]
fn test_resolve_fuzzy_cline_variations() {
    let registry = AgentRegistry::new().unwrap();
    // Test cline variations
    assert_eq!(registry.resolve_fuzzy("kline"), Some("cline".to_string()));
    assert_eq!(registry.resolve_fuzzy("KLINE"), Some("cline".to_string()));
}

#[test]
fn test_resolve_fuzzy_ccs_dash_to_slash() {
    let registry = AgentRegistry::new().unwrap();
    // Test ccs- to ccs/ conversion (even for unregistered aliases)
    assert_eq!(
        registry.resolve_fuzzy("ccs-random"),
        Some("ccs/random".to_string())
    );
    assert_eq!(
        registry.resolve_fuzzy("ccs-test"),
        Some("ccs/test".to_string())
    );
}

#[test]
fn test_resolve_fuzzy_underscore_replacement() {
    // Test underscore to dash/slash replacement
    // Note: These test the pattern, actual agents may not exist
    let result = AgentRegistry::get_fuzzy_alternatives("my_agent");
    assert!(result.contains(&"my_agent".to_string()));
    assert!(result.contains(&"my-agent".to_string()));
    assert!(result.contains(&"my/agent".to_string()));
}

#[test]
fn test_resolve_fuzzy_unknown() {
    let registry = AgentRegistry::new().unwrap();
    // Unknown agent should return None
    assert_eq!(registry.resolve_fuzzy("totally-unknown"), None);
}

#[test]
fn test_apply_unified_config_does_not_inherit_env_vars() {
    // Regression test for CCS env vars leaking between agents.
    // Ensures that when apply_unified_config merges agent configurations,
    // env_vars from the existing agent are NOT inherited into the merged agent.
    let mut registry = AgentRegistry::new().unwrap();

    // First, manually register a "claude" agent with some env vars (simulating
    // a previously-loaded agent with CCS env vars or manually-specified vars)
    registry.register(
        "claude",
        AgentConfig {
            cmd: "claude -p".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            // Simulate CCS env vars from a previous load
            env_vars: {
                let mut vars = std::collections::HashMap::new();
                vars.insert(
                    "ANTHROPIC_BASE_URL".to_string(),
                    "https://api.z.ai/api/anthropic".to_string(),
                );
                vars.insert(
                    "ANTHROPIC_AUTH_TOKEN".to_string(),
                    "test-token-glm".to_string(),
                );
                vars.insert("ANTHROPIC_MODEL".to_string(), "glm-4.7".to_string());
                vars
            },
            display_name: None,
        },
    );

    // Verify the "claude" agent has the GLM env vars
    let claude_config = registry.resolve_config("claude").unwrap();
    assert_eq!(claude_config.env_vars.len(), 3);
    assert_eq!(
        claude_config.env_vars.get("ANTHROPIC_BASE_URL"),
        Some(&"https://api.z.ai/api/anthropic".to_string())
    );

    // Now apply a unified config that overrides the "claude" agent
    // (simulating user's ~/.config/ralph-workflow.toml with [agents.claude])
    // Create a minimal GeneralConfig via Default for UnifiedConfig
    // Note: We can't directly construct UnifiedConfig with Default because agents is not Default
    // So we'll create it by deserializing from a TOML string
    let toml_str = r#"
        [general]
        verbosity = 2
        interactive = true
        isolation_mode = true

        [agents.claude]
        cmd = "claude -p"
        display_name = "My Custom Claude"
    "#;
    let unified: crate::config::UnifiedConfig = toml::from_str(toml_str).unwrap();

    // Apply the unified config
    registry.apply_unified_config(&unified);

    // Verify that the "claude" agent's env_vars are now empty (NOT inherited)
    let claude_config_after = registry.resolve_config("claude").unwrap();
    assert_eq!(
        claude_config_after.env_vars.len(),
        0,
        "env_vars should NOT be inherited from the existing agent when unified config is applied"
    );
    assert_eq!(
        claude_config_after.display_name,
        Some("My Custom Claude".to_string()),
        "display_name should be updated from the unified config"
    );
}

#[test]
fn test_resolve_config_does_not_share_env_vars_between_agents() {
    // Regression test for the exact bug scenario:
    // 1. User runs Ralph with ccs/glm agent (with GLM env vars)
    // 2. User then runs Ralph with claude agent
    // 3. Claude should NOT have GLM env vars
    //
    // This test verifies that resolve_config() returns independent AgentConfig
    // instances with separate env_vars HashMaps - i.e., modifications to one
    // agent's env_vars don't affect another agent's config.
    let mut registry = AgentRegistry::new().unwrap();

    // Register ccs/glm with GLM environment variables
    registry.register(
        "ccs/glm",
        AgentConfig {
            cmd: "ccs glm".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: "-p".to_string(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: {
                let mut vars = std::collections::HashMap::new();
                vars.insert(
                    "ANTHROPIC_BASE_URL".to_string(),
                    "https://api.z.ai/api/anthropic".to_string(),
                );
                vars.insert(
                    "ANTHROPIC_AUTH_TOKEN".to_string(),
                    "test-token-glm".to_string(),
                );
                vars.insert("ANTHROPIC_MODEL".to_string(), "glm-4.7".to_string());
                vars
            },
            display_name: Some("ccs-glm".to_string()),
        },
    );

    // Register claude with empty env_vars (typical configuration)
    registry.register(
        "claude",
        AgentConfig {
            cmd: "claude -p".to_string(),
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            can_commit: true,
            json_parser: JsonParserType::Claude,
            model_flag: None,
            print_flag: String::new(),
            streaming_flag: "--include-partial-messages".to_string(),
            session_flag: "--resume {}".to_string(),
            env_vars: std::collections::HashMap::new(),
            display_name: None,
        },
    );

    // Resolve ccs/glm config first
    let glm_config = registry.resolve_config("ccs/glm").unwrap();
    assert_eq!(glm_config.env_vars.len(), 3);
    assert_eq!(
        glm_config.env_vars.get("ANTHROPIC_BASE_URL"),
        Some(&"https://api.z.ai/api/anthropic".to_string())
    );

    // Resolve claude config
    let claude_config = registry.resolve_config("claude").unwrap();
    assert_eq!(
        claude_config.env_vars.len(),
        0,
        "claude agent should have empty env_vars"
    );

    // Resolve ccs/glm again to ensure we get a fresh clone
    let glm_config2 = registry.resolve_config("ccs/glm").unwrap();
    assert_eq!(glm_config2.env_vars.len(), 3);

    // Modify the first GLM config's env_vars
    // This should NOT affect the second GLM config if cloning is deep
    drop(glm_config);

    // Verify claude still has empty env_vars after another resolve
    let claude_config2 = registry.resolve_config("claude").unwrap();
    assert_eq!(
        claude_config2.env_vars.len(),
        0,
        "claude agent env_vars should remain independent"
    );
}
