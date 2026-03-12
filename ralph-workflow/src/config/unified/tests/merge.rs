use super::*;

#[test]
fn test_merge_with_scalar_override() {
    let global = UnifiedConfig {
        general: GeneralConfig {
            verbosity: 2,
            developer_iters: 5,
            ..Default::default()
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        general: GeneralConfig {
            verbosity: 4,
            developer_iters: 10,
            ..Default::default()
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert_eq!(merged.general.verbosity, 4);
    assert_eq!(merged.general.developer_iters, 10);
}

#[test]
fn test_merge_with_preserves_global_when_local_optional_is_none() {
    let global = UnifiedConfig {
        general: GeneralConfig {
            git_user_name: Some("Global User".to_string()),
            git_user_email: Some("global@example.com".to_string()),
            ..Default::default()
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        general: GeneralConfig {
            git_user_name: None,
            git_user_email: None,
            ..Default::default()
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert_eq!(
        merged.general.git_user_name,
        Some("Global User".to_string())
    );
    assert_eq!(
        merged.general.git_user_email,
        Some("global@example.com".to_string())
    );
}

#[test]
fn test_merge_with_agents_map_merges_entries() {
    use std::collections::HashMap;

    let mut global_agents = HashMap::new();
    global_agents.insert(
        "claude".to_string(),
        AgentConfigToml {
            cmd: Some("claude".to_string()),
            ..Default::default()
        },
    );

    let mut local_agents = HashMap::new();
    local_agents.insert(
        "codex".to_string(),
        AgentConfigToml {
            cmd: Some("codex".to_string()),
            ..Default::default()
        },
    );

    let global = UnifiedConfig {
        agents: global_agents,
        ..Default::default()
    };

    let local = UnifiedConfig {
        agents: local_agents,
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert_eq!(merged.agents.len(), 2);
    assert!(merged.agents.contains_key("claude"));
    assert!(merged.agents.contains_key("codex"));
}

#[test]
fn test_merge_with_agent_chain_local_replaces_global() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            commit: vec!["claude".to_string()],
            analysis: vec![],
            provider_fallback: HashMap::default(),
            max_retries: 3,
            retry_delay_ms: 1000,
            backoff_multiplier: 2.0,
            max_backoff_ms: 60000,
            max_cycles: 3,
        }),
        ..Default::default()
    };

    let local = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["codex".to_string()],
            reviewer: vec!["codex".to_string()],
            commit: vec!["codex".to_string()],
            analysis: vec![],
            provider_fallback: HashMap::default(),
            max_retries: 3,
            retry_delay_ms: 1000,
            backoff_multiplier: 2.0,
            max_backoff_ms: 60000,
            max_cycles: 3,
        }),
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["codex"]);
    assert_eq!(chain.reviewer, vec!["codex"]);
}

#[test]
fn test_merge_with_local_none_agent_chain_preserves_global() {
    use crate::agents::fallback::FallbackConfig;

    let global = UnifiedConfig {
        agent_chain: Some(FallbackConfig {
            developer: vec!["claude".to_string()],
            reviewer: vec!["claude".to_string()],
            commit: vec!["claude".to_string()],
            analysis: vec![],
            provider_fallback: HashMap::default(),
            max_retries: 3,
            retry_delay_ms: 1000,
            backoff_multiplier: 2.0,
            max_backoff_ms: 60000,
            max_cycles: 3,
        }),
        ..Default::default()
    };

    let local = UnifiedConfig {
        agent_chain: None,
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    let chain = merged.agent_chain.unwrap();
    assert_eq!(chain.developer, vec!["claude"]);
    assert_eq!(chain.reviewer, vec!["claude"]);
}

#[test]
fn test_merge_with_nested_behavior_flags() {
    let global = UnifiedConfig {
        general: GeneralConfig {
            behavior: GeneralBehaviorFlags {
                interactive: true,
                auto_detect_stack: true,
                strict_validation: false,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        general: GeneralConfig {
            behavior: GeneralBehaviorFlags {
                interactive: false,
                auto_detect_stack: true,
                strict_validation: true,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert!(!merged.general.behavior.interactive);
    assert!(merged.general.behavior.auto_detect_stack);
    assert!(merged.general.behavior.strict_validation);
}

#[test]
fn test_merge_with_ccs_aliases_map_merges() {
    use std::collections::HashMap;

    let mut global_aliases = HashMap::new();
    global_aliases.insert(
        "work".to_string(),
        CcsAliasToml::Command("ccs work".to_string()),
    );

    let mut local_aliases = HashMap::new();
    local_aliases.insert(
        "personal".to_string(),
        CcsAliasToml::Command("ccs personal".to_string()),
    );

    let global = UnifiedConfig {
        ccs_aliases: global_aliases,
        ..Default::default()
    };

    let local = UnifiedConfig {
        ccs_aliases: local_aliases,
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert_eq!(merged.ccs_aliases.len(), 2);
    assert!(merged.ccs_aliases.contains_key("work"));
    assert!(merged.ccs_aliases.contains_key("personal"));
}

#[test]
fn test_merge_with_ccs_empty_string_preserves_global() {
    // Test that empty string in local config does NOT override global
    // This is important for CCS where empty string means "disable this feature"
    let global = UnifiedConfig {
        ccs: CcsConfig {
            output_flag: "--output-format=stream-json".to_string(),
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            print_flag: "--print".to_string(),
            streaming_flag: "--include-partial-messages".to_string(),
            json_parser: "claude".to_string(),
            session_flag: "--resume {}".to_string(),
            can_commit: true,
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        ccs: CcsConfig {
            // Empty string should preserve global value
            output_flag: String::new(),
            // Non-empty string should override
            yolo_flag: "--yolo".to_string(),
            // Empty string should preserve global value
            verbose_flag: String::new(),
            // Empty string should preserve global value
            print_flag: String::new(),
            streaming_flag: String::new(),
            json_parser: String::new(),
            session_flag: String::new(),
            can_commit: false, // Boolean overrides normally
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    // Empty string in local should preserve global value
    assert_eq!(merged.ccs.output_flag, "--output-format=stream-json");
    // Non-empty string in local should override
    assert_eq!(merged.ccs.yolo_flag, "--yolo");
    // Empty string in local should preserve global value
    assert_eq!(merged.ccs.verbose_flag, "--verbose");
    assert_eq!(merged.ccs.print_flag, "--print");
    assert_eq!(merged.ccs.streaming_flag, "--include-partial-messages");
    assert_eq!(merged.ccs.json_parser, "claude");
    assert_eq!(merged.ccs.session_flag, "--resume {}");
    // Boolean overrides normally
    assert!(!merged.ccs.can_commit);
}

#[test]
fn test_resolve_agent_drains_checked_rejects_mixed_legacy_and_named_schema() {
    let config = UnifiedConfig {
        agent_chain: Some(crate::agents::fallback::FallbackConfig {
            developer: vec!["codex".to_string()],
            ..Default::default()
        }),
        agent_chains: std::collections::HashMap::from([(
            "shared_dev".to_string(),
            vec!["claude".to_string()],
        )]),
        ..Default::default()
    };

    let error = config
        .resolve_agent_drains_checked()
        .expect_err("mixed schemas should fail");

    assert!(error.contains("agent_chain"));
    assert!(error.contains("agent_chains/agent_drains"));
}

#[test]
fn test_resolve_agent_drains_checked_rejects_missing_builtin_coverage() {
    let config = UnifiedConfig {
        agent_chains: std::collections::HashMap::from([(
            "shared_review".to_string(),
            vec!["claude".to_string()],
        )]),
        agent_drains: std::collections::HashMap::from([
            ("review".to_string(), "shared_review".to_string()),
            ("fix".to_string(), "shared_review".to_string()),
        ]),
        ..Default::default()
    };

    let error = config
        .resolve_agent_drains_checked()
        .expect_err("missing built-in drains should fail");

    assert!(error.contains("planning"));
    assert!(error.contains("development"));
    assert!(error.contains("analysis"));
}

#[test]
fn test_resolve_agent_drains_checked_derives_commit_and_analysis_from_bound_drains() {
    let config = UnifiedConfig {
        agent_chain: Some(crate::agents::fallback::FallbackConfig {
            provider_fallback: std::collections::HashMap::from([(
                "opencode".to_string(),
                vec!["-m opencode/glm-4.7-free".to_string()],
            )]),
            max_retries: 7,
            retry_delay_ms: 2_500,
            backoff_multiplier: 3.0,
            max_backoff_ms: 90_000,
            max_cycles: 5,
            ..Default::default()
        }),
        agent_chains: std::collections::HashMap::from([
            (
                "shared_dev".to_string(),
                vec!["codex".to_string(), "claude".to_string()],
            ),
            (
                "shared_review".to_string(),
                vec!["claude".to_string(), "opencode".to_string()],
            ),
        ]),
        agent_drains: std::collections::HashMap::from([
            ("planning".to_string(), "shared_dev".to_string()),
            ("development".to_string(), "shared_dev".to_string()),
            ("review".to_string(), "shared_review".to_string()),
            ("fix".to_string(), "shared_review".to_string()),
        ]),
        ..Default::default()
    };

    let resolved = config
        .resolve_agent_drains_checked()
        .expect("drain defaults should derive from existing bound drains")
        .expect("named drain config should resolve");

    let commit = resolved
        .binding(crate::agents::AgentDrain::Commit)
        .expect("commit drain should be derived from review/fix binding");
    let analysis = resolved
        .binding(crate::agents::AgentDrain::Analysis)
        .expect("analysis drain should be derived from planning/development binding");

    assert_eq!(commit.chain_name, "shared_review");
    assert_eq!(commit.agents, vec!["claude", "opencode"]);
    assert_eq!(analysis.chain_name, "shared_dev");
    assert_eq!(analysis.agents, vec!["codex", "claude"]);
    assert_eq!(resolved.max_retries, 7);
    assert_eq!(resolved.retry_delay_ms, 2_500);
    assert!((resolved.backoff_multiplier - 3.0).abs() < f64::EPSILON);
    assert_eq!(resolved.max_backoff_ms, 90_000);
    assert_eq!(resolved.max_cycles, 5);
    assert_eq!(
        resolved.provider_fallback.get("opencode"),
        Some(&vec!["-m opencode/glm-4.7-free".to_string()])
    );
}

#[test]
fn test_merge_with_ccs_all_empty_preserves_all_global() {
    let global = UnifiedConfig {
        ccs: CcsConfig {
            output_flag: "--output=json".to_string(),
            yolo_flag: "--yes".to_string(),
            verbose_flag: "-v".to_string(),
            print_flag: "-p".to_string(),
            streaming_flag: "-s".to_string(),
            json_parser: "generic".to_string(),
            session_flag: "--continue {}".to_string(),
            can_commit: true,
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        ccs: CcsConfig {
            output_flag: String::new(),
            yolo_flag: String::new(),
            verbose_flag: String::new(),
            print_flag: String::new(),
            streaming_flag: String::new(),
            json_parser: String::new(),
            session_flag: String::new(),
            can_commit: true,
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    // All global values should be preserved
    assert_eq!(merged.ccs.output_flag, "--output=json");
    assert_eq!(merged.ccs.yolo_flag, "--yes");
    assert_eq!(merged.ccs.verbose_flag, "-v");
    assert_eq!(merged.ccs.print_flag, "-p");
    assert_eq!(merged.ccs.streaming_flag, "-s");
    assert_eq!(merged.ccs.json_parser, "generic");
    assert_eq!(merged.ccs.session_flag, "--continue {}");
    assert!(merged.ccs.can_commit);
}

#[test]
fn test_merge_with_ccs_non_empty_overrides() {
    let global = UnifiedConfig {
        ccs: CcsConfig {
            output_flag: "--output=json".to_string(),
            yolo_flag: "--yes".to_string(),
            verbose_flag: "-v".to_string(),
            print_flag: "-p".to_string(),
            streaming_flag: "-s".to_string(),
            json_parser: "generic".to_string(),
            session_flag: "--continue {}".to_string(),
            can_commit: true,
        },
        ..Default::default()
    };

    let local = UnifiedConfig {
        ccs: CcsConfig {
            output_flag: "--output=stream-json".to_string(),
            yolo_flag: "--yolo".to_string(),
            verbose_flag: "-vv".to_string(),
            print_flag: "--print".to_string(),
            streaming_flag: "--include-partial".to_string(),
            json_parser: "claude".to_string(),
            session_flag: "--resume {}".to_string(),
            can_commit: false,
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    // All local values should override
    assert_eq!(merged.ccs.output_flag, "--output=stream-json");
    assert_eq!(merged.ccs.yolo_flag, "--yolo");
    assert_eq!(merged.ccs.verbose_flag, "-vv");
    assert_eq!(merged.ccs.print_flag, "--print");
    assert_eq!(merged.ccs.streaming_flag, "--include-partial");
    assert_eq!(merged.ccs.json_parser, "claude");
    assert_eq!(merged.ccs.session_flag, "--resume {}");
    assert!(!merged.ccs.can_commit);
}

#[test]
fn test_merge_with_minimal_local_preserves_global() {
    // This is the critical test case from Issue #1
    // When local config contains only developer_iters, other fields should preserve global values
    let global = UnifiedConfig {
        general: GeneralConfig {
            verbosity: 3,
            developer_iters: 5,
            reviewer_reviews: 2,
            developer_context: 1,
            reviewer_context: 0,
            behavior: GeneralBehaviorFlags {
                interactive: false,
                auto_detect_stack: false,
                strict_validation: true,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    // Local config with only developer_iters set (all others at default)
    let local = UnifiedConfig {
        general: GeneralConfig {
            developer_iters: 10, // Only this is overridden
            // Everything else is at default values
            ..Default::default()
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    // developer_iters should be from local
    assert_eq!(merged.general.developer_iters, 10);

    // All other values should be from global, NOT defaults
    assert_eq!(
        merged.general.verbosity, 3,
        "verbosity should be from global"
    );
    assert_eq!(
        merged.general.reviewer_reviews, 2,
        "reviewer_reviews should be from global"
    );
    assert_eq!(
        merged.general.developer_context, 1,
        "developer_context should be from global"
    );
    assert_eq!(
        merged.general.reviewer_context, 0,
        "reviewer_context should be from global"
    );
    assert!(
        !merged.general.behavior.interactive,
        "interactive should be from global"
    );
    assert!(
        !merged.general.behavior.auto_detect_stack,
        "auto_detect_stack should be from global"
    );
    assert!(
        merged.general.behavior.strict_validation,
        "strict_validation should be from global"
    );
}

#[test]
fn test_merge_with_partial_override_preserves_rest() {
    let global = UnifiedConfig {
        general: GeneralConfig {
            verbosity: 4,
            developer_iters: 7,
            reviewer_reviews: 3,
            ..Default::default()
        },
        ..Default::default()
    };

    // Local overrides verbosity but not reviewer_reviews
    let local = UnifiedConfig {
        general: GeneralConfig {
            verbosity: 1,
            developer_iters: 3, // Also override this
            // reviewer_reviews is at default (2)
            ..Default::default()
        },
        ..Default::default()
    };

    let merged = global.merge_with(&local);

    assert_eq!(merged.general.verbosity, 1);
    assert_eq!(merged.general.developer_iters, 3);
    // This should be from global, not default
    assert_eq!(merged.general.reviewer_reviews, 3);
}

// Tests for merge_with_content - verifying proper presence tracking for nested fields

#[test]
fn test_workflow_flags_default() {
    let flags = GeneralWorkflowFlags::default();
    println!(
        "GeneralWorkflowFlags::default().checkpoint_enabled = {}",
        flags.checkpoint_enabled
    );
    let config = GeneralConfig::default();
    println!(
        "GeneralConfig::default().workflow.checkpoint_enabled = {}",
        config.workflow.checkpoint_enabled
    );
}

#[test]
fn test_toml_deserialization_with_workflow() {
    // NOTE: workflow and execution fields are FLATTENED into [general], not separate tables.
    // So the correct TOML structure is [general] with checkpoint_enabled, not [general.workflow].
    let toml = r"
[general]
checkpoint_enabled = true
";
    let config: UnifiedConfig = toml::from_str(toml).unwrap();
    println!(
        "Deserialized config.general.workflow.checkpoint_enabled = {}",
        config.general.workflow.checkpoint_enabled
    );
    assert!(
        config.general.workflow.checkpoint_enabled,
        "Should deserialize to true"
    );
}

#[test]
fn test_merge_with_content_workflow_checkpoint_enabled_at_default() {
    // Test that when local config sets checkpoint_enabled = true (which is the default),
    // it correctly overrides global config's checkpoint_enabled = false.
    // NOTE: workflow fields are flattened into [general], not in [general.workflow].
    let global = UnifiedConfig {
        general: GeneralConfig {
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: false,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general]
checkpoint_enabled = true
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    // Should use local value (true), not global (false)
    assert!(
        merged.general.workflow.checkpoint_enabled,
        "checkpoint_enabled should be from local (true), not global (false)"
    );
}

#[test]
fn test_merge_with_content_execution_isolation_mode_at_default() {
    // Test that when local config sets isolation_mode = true (which is the default),
    // it correctly overrides global config's isolation_mode = false.
    // NOTE: execution fields are flattened into [general], not in [general.execution].
    let global = UnifiedConfig {
        general: GeneralConfig {
            execution: GeneralExecutionFlags {
                isolation_mode: false,
                force_universal_prompt: false,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general]
isolation_mode = true
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    // Should use local value (true), not global (false)
    assert!(
        merged.general.execution.isolation_mode,
        "isolation_mode should be from local (true), not global (false)"
    );
}

#[test]
fn test_merge_with_content_execution_force_universal_prompt_preserves_global() {
    // Test that when local config does NOT set force_universal_prompt,
    // the global value is preserved.
    // NOTE: execution fields are flattened into [general], not in [general.execution].
    let global = UnifiedConfig {
        general: GeneralConfig {
            execution: GeneralExecutionFlags {
                isolation_mode: true,
                force_universal_prompt: true,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general]
isolation_mode = false
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    // isolation_mode should be from local (false)
    assert!(
        !merged.general.execution.isolation_mode,
        "isolation_mode should be from local (false)"
    );
    // force_universal_prompt should be from global (true)
    assert!(
        merged.general.execution.force_universal_prompt,
        "force_universal_prompt should be from global (true)"
    );
}

#[test]
fn test_merge_with_content_nested_fields_independent() {
    // Test that fields in different sections are tracked independently.
    // NOTE: workflow and execution fields are flattened into [general], not separate tables.
    let global = UnifiedConfig {
        general: GeneralConfig {
            behavior: GeneralBehaviorFlags {
                interactive: false,
                auto_detect_stack: false,
                strict_validation: false,
            },
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: false,
            },
            execution: GeneralExecutionFlags {
                isolation_mode: false,
                force_universal_prompt: false,
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general.behavior]
interactive = true

[general]
isolation_mode = true
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    // Fields from [general.behavior] that are set should be from local
    assert!(
        merged.general.behavior.interactive,
        "interactive should be from local (true)"
    );
    // Fields from [general.behavior] that are NOT set should be from global
    assert!(
        !merged.general.behavior.auto_detect_stack,
        "auto_detect_stack should be from global (false)"
    );
    assert!(
        !merged.general.behavior.strict_validation,
        "strict_validation should be from global (false)"
    );

    // Fields from workflow that are NOT set should be from global
    assert!(
        !merged.general.workflow.checkpoint_enabled,
        "checkpoint_enabled should be from global (false)"
    );

    // Fields from execution that are set should be from local
    assert!(
        merged.general.execution.isolation_mode,
        "isolation_mode should be from local (true)"
    );
    // Fields from execution that are NOT set should be from global
    assert!(
        !merged.general.execution.force_universal_prompt,
        "force_universal_prompt should be from global (false)"
    );
}

#[test]
fn test_merge_with_content_all_nested_sections_with_defaults() {
    // Comprehensive test: local config sets all fields to their default values,
    // global config has all non-default values. Local should win on all fields.
    // NOTE: workflow and execution fields are flattened into [general], not separate tables.
    let global = UnifiedConfig {
        general: GeneralConfig {
            behavior: GeneralBehaviorFlags {
                interactive: false,       // default is true
                auto_detect_stack: false, // default is true
                strict_validation: true,  // default is false
            },
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: false, // default is true
            },
            execution: GeneralExecutionFlags {
                isolation_mode: false,        // default is true
                force_universal_prompt: true, // default is false
            },
            ..Default::default()
        },
        ..Default::default()
    };

    let local_toml = r"
[general.behavior]
interactive = true
auto_detect_stack = true
strict_validation = false

[general]
checkpoint_enabled = true
isolation_mode = true
force_universal_prompt = false
";

    let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    let merged = global.merge_with_content(local_toml, &local);

    // All fields should be from local (which happens to be default values)
    assert!(
        merged.general.behavior.interactive,
        "interactive should be from local (true)"
    );
    assert!(
        merged.general.behavior.auto_detect_stack,
        "auto_detect_stack should be from local (true)"
    );
    assert!(
        !merged.general.behavior.strict_validation,
        "strict_validation should be from local (false)"
    );
    assert!(
        merged.general.workflow.checkpoint_enabled,
        "checkpoint_enabled should be from local (true)"
    );
    assert!(
        merged.general.execution.isolation_mode,
        "isolation_mode should be from local (true)"
    );
    assert!(
        !merged.general.execution.force_universal_prompt,
        "force_universal_prompt should be from local (false)"
    );
}
