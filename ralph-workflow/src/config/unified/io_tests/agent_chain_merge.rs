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
            .binding(crate::agents::AgentDrain::Fix)
            .expect("fix drain")
            .chain_name,
        "shared_review"
    );
}

#[test]
fn test_merge_with_content_general_retry_settings_override_global() {
    let global = UnifiedConfig {
        general: GeneralConfig {
            max_retries: 3,
            retry_delay_ms: 1_000,
            backoff_multiplier: 2.0,
            max_backoff_ms: 60_000,
            max_cycles: 3,
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general]
max_retries = 5
retry_delay_ms = 2500
max_cycles = 6
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    assert_eq!(merged.general.max_retries, 5);
    assert_eq!(merged.general.retry_delay_ms, 2_500);
    assert!((merged.general.backoff_multiplier - 2.0).abs() < f64::EPSILON);
    assert_eq!(merged.general.max_backoff_ms, 60_000);
    assert_eq!(merged.general.max_cycles, 6);
}

#[test]
fn test_resolve_agent_drains_checked_uses_general_provider_fallback() {
    let config: UnifiedConfig = toml::from_str(
        r#"
[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
analysis = "shared_dev"
review = "shared_review"
fix = "shared_review"
commit = "shared_review"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free"]
"#,
    )
    .unwrap();

    let resolved = config
        .resolve_agent_drains_checked()
        .expect("named drain config should resolve")
        .expect("resolved drains should exist");

    assert_eq!(
        resolved.provider_fallback.get("opencode"),
        Some(&vec!["-m opencode/glm-4.7-free".to_string()])
    );
}

#[test]
fn test_resolve_agent_drains_checked_accepts_removed_legacy_agent_chain() {
    let config: UnifiedConfig = toml::from_str(
        r#"
[agent_chain]
developer = ["codex"]
"#,
    )
    .unwrap();

    let resolved = config
        .resolve_agent_drains_checked()
        .expect("legacy agent_chain should remain compatible")
        .expect("legacy agent_chain should resolve drains");

    assert_eq!(
        resolved
            .binding(crate::agents::AgentDrain::Planning)
            .expect("planning drain")
            .agents,
        vec!["codex"]
    );
}

#[test]
fn test_resolve_agent_drains_checked_suggests_agent_chains_for_singular_typo() {
    let config: UnifiedConfig = toml::from_str(
        r#"
[agent_chain]
shared_dev = ["codex"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
"#,
    )
    .unwrap();

    let error = config
        .resolve_agent_drains_checked()
        .expect_err("singular agent_chain typo should fail");

    assert!(error.contains("did you mean [agent_chains]?"));
}

#[test]
fn test_resolve_agent_drains_checked_rejects_conflicting_legacy_and_named_chain_names() {
    let config: UnifiedConfig = toml::from_str(
        r#"
[agent_chain]
developer = ["codex"]

[agent_chains]
developer = ["claude"]

[agent_drains]
planning = "developer"
development = "developer"
analysis = "developer"
review = "developer"
fix = "developer"
commit = "developer"
"#,
    )
    .unwrap();

    let error = config
        .resolve_agent_drains_checked()
        .expect_err("conflicting chain names should fail");

    assert!(error.contains("conflicting agent chain definitions"));
    assert!(error.contains("developer"));
}
