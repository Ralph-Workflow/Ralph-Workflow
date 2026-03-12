// ============================================================================
// Agent Chain Per-Key Merge Tests
// ============================================================================

use super::*;

#[test]
fn test_merge_with_content_resolves_named_chains_and_drain_bindings() {
    let global = UnifiedConfig::default();

    let local_toml = r#"
[agent_chains]
shared_dev = ["codex", "claude"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
analysis = "shared_dev"
review = "shared_review"
fix = "shared_review"
commit = "shared_review"
"#;

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);
    let resolved = merged
        .resolve_agent_drains()
        .expect("named chains and drain bindings should resolve");

    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Planning)
            .expect("planning drain")
            .chain_name,
        "shared_dev"
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Development)
            .expect("development drain")
            .chain_name,
        "shared_dev"
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Fix)
            .expect("fix drain")
            .chain_name,
        "shared_review"
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Planning)
            .expect("planning drain")
            .agents,
        vec!["codex".to_string(), "claude".to_string()]
    );
}

#[test]
fn test_merge_with_content_legacy_agent_chain_translates_to_resolved_drains() {
    let local_toml = r#"
[agent_chain]
developer = ["codex"]
reviewer = ["claude"]
commit = ["opencode"]
"#;

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let resolved = local
        .resolve_agent_drains()
        .expect("legacy role-keyed config should translate to resolved drains");

    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Planning)
            .expect("planning drain")
            .agents,
        vec!["codex".to_string()]
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Development)
            .expect("development drain")
            .agents,
        vec!["codex".to_string()]
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Review)
            .expect("review drain")
            .agents,
        vec!["claude".to_string()]
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Fix)
            .expect("fix drain")
            .agents,
        vec!["claude".to_string()]
    );
    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Commit)
            .expect("commit drain")
            .agents,
        vec!["opencode".to_string()]
    );
}

#[test]
fn test_merge_with_content_agent_chain_merges_by_key() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            ..Default::default()
        }),
        ..Default::default()
    };

    let local_toml = r"
[agent_chain]
developer = ['codex']
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    // Local developer chain overrides global
    assert_eq!(chain.developer, vec!["codex"]);
    // Global reviewer chain preserved (not wiped out)
    assert_eq!(
        chain.reviewer,
        vec!["claude"],
        "reviewer chain should be preserved from global when not set in local"
    );
}

#[test]
fn test_merge_with_content_agent_chain_local_only_developer_preserves_global_reviewer() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            commit: vec!["claude".to_string()],
            ..Default::default()
        }),
        ..Default::default()
    };

    let local_toml = r"
[agent_chain]
developer = ['codex']
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(chain.reviewer, vec!["claude"]);
    assert_eq!(chain.commit, vec!["claude"]);
}

#[test]
fn test_merge_with_agent_chain_local_none_preserves_global_regression() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            ..Default::default()
        }),
        ..Default::default()
    };

    // No agent_chain in local at all
    let local_toml = r"
[general]
verbosity = 3
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["claude"]);
    assert_eq!(chain.reviewer, vec!["claude"]);
}

#[test]
fn test_merge_with_content_agent_chain_metadata_uses_local_when_present() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            max_retries: 3,
            retry_delay_ms: 1000,
            ..Default::default()
        }),
        ..Default::default()
    };

    let local_toml = r"
[agent_chain]
max_retries = 5
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    // max_retries should be overridden by local
    assert_eq!(chain.max_retries, 5);
    // retry_delay_ms should be preserved from global
    assert_eq!(chain.retry_delay_ms, 1000);
    // Chain lists should be preserved from global (not set in local)
    assert_eq!(chain.developer, vec!["claude"]);
    assert_eq!(chain.reviewer, vec!["claude"]);
}

#[test]
fn test_merge_with_agent_chain_per_key_programmatic() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            commit: vec!["claude".to_string()],
            ..Default::default()
        }),
        ..Default::default()
    };

    let local = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["codex".to_string()],
            // Programmatic merge path has no key-presence info, so empty chains
            // are treated as "not set" and fall through to global.
            reviewer: vec![],
            commit: vec![],
            ..Default::default()
        }),
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(
        chain.reviewer,
        vec!["claude"],
        "programmatic merge should treat empty local reviewer as fallback-to-global"
    );
    assert_eq!(
        chain.commit,
        vec!["claude"],
        "programmatic merge should treat empty local commit as fallback-to-global"
    );
}

#[test]
fn test_merge_with_content_agent_chain_empty_local_list_overrides_global() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            commit: vec!["claude".to_string()],
            ..Default::default()
        }),
        ..Default::default()
    };

    let local_toml = r"
[agent_chain]
reviewer = []
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["claude"]);
    assert!(
        chain.reviewer.is_empty(),
        "explicitly present empty local reviewer chain must override global reviewer"
    );
    assert_eq!(chain.commit, vec!["claude"]);
}

#[test]
fn test_merge_with_content_local_agent_chain_only_uses_built_in_defaults_for_missing_roles() {
    let global = UnifiedConfig::default();

    let local_toml = r#"
[agent_chain]
developer = ["codex"]
"#;

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    let builtins = crate::agents::AgentRegistry::new()
        .expect("built-in registry should load")
        .fallback_config();

    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(
        chain.reviewer, builtins.reviewer,
        "missing local reviewer should inherit built-in defaults"
    );
    assert_eq!(
        chain.commit, builtins.commit,
        "missing local commit should inherit built-in defaults"
    );
    assert_eq!(
        chain.analysis, builtins.analysis,
        "missing local analysis should inherit built-in defaults"
    );
}

#[test]
fn test_merge_with_content_local_agent_chain_partial_toml_keeps_built_in_metadata_defaults() {
    let global = UnifiedConfig::default();

    let local_toml = r#"
[agent_chain]
developer = ["codex"]
"#;

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    let chain = merged.agent_chain.unwrap();
    let builtins = crate::agents::AgentRegistry::new()
        .expect("built-in registry should load")
        .fallback_config();

    assert_eq!(chain.max_retries, builtins.max_retries);
    assert_eq!(chain.retry_delay_ms, builtins.retry_delay_ms);
    assert!((chain.backoff_multiplier - builtins.backoff_multiplier).abs() < f64::EPSILON);
    assert_eq!(chain.max_backoff_ms, builtins.max_backoff_ms);
    assert_eq!(chain.max_cycles, builtins.max_cycles);
}
