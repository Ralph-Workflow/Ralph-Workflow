use super::environment::sanitize_command_env;
use std::collections::HashMap;

const ANTHROPIC_ENV_VARS_TO_SANITIZE: &[&str] = &[
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
];

#[test]
fn test_sanitize_command_env_removes_anthropic_vars_when_not_explicitly_set() {
    // Setup: Environment with GLM-like Anthropic credentials
    let mut env_vars = HashMap::from([
        ("ANTHROPIC_API_KEY".to_string(), "glm-test-key".to_string()),
        (
            "ANTHROPIC_BASE_URL".to_string(),
            "https://glm.example.com".to_string(),
        ),
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), "/home/user".to_string()),
    ]);
    let agent_env_vars = HashMap::new(); // Agent doesn't set any Anthropic vars

    // Execute: Sanitize environment
    sanitize_command_env(
        &mut env_vars,
        &agent_env_vars,
        ANTHROPIC_ENV_VARS_TO_SANITIZE,
    );

    // Assert: Anthropic vars should be removed, other vars preserved
    assert!(
        !env_vars.contains_key("ANTHROPIC_API_KEY"),
        "ANTHROPIC_API_KEY should be removed when not explicitly set by agent"
    );
    assert!(
        !env_vars.contains_key("ANTHROPIC_BASE_URL"),
        "ANTHROPIC_BASE_URL should be removed when not explicitly set by agent"
    );
    assert_eq!(
        env_vars.get("PATH"),
        Some(&"/usr/bin:/bin".to_string()),
        "Non-Anthropic vars should be preserved"
    );
    assert_eq!(
        env_vars.get("HOME"),
        Some(&"/home/user".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}

#[test]
fn test_sanitize_command_env_preserves_explicitly_set_anthropic_vars() {
    // Setup: Environment with parent Anthropic vars + agent's explicit vars
    let mut env_vars = HashMap::from([
        ("ANTHROPIC_API_KEY".to_string(), "parent-key".to_string()),
        (
            "ANTHROPIC_BASE_URL".to_string(),
            "https://parent.example.com".to_string(),
        ),
        (
            "ANTHROPIC_AUTH_TOKEN".to_string(),
            "parent-token".to_string(),
        ),
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
    ]);
    let agent_env_vars = HashMap::from([
        (
            "ANTHROPIC_API_KEY".to_string(),
            "agent-specific-key".to_string(),
        ),
        (
            "ANTHROPIC_BASE_URL".to_string(),
            "https://agent.example.com".to_string(),
        ),
    ]);

    // First, insert agent env vars into env_vars (mimics production code pattern)
    for (key, value) in &agent_env_vars {
        env_vars.insert(key.clone(), value.clone());
    }

    // Execute: Sanitize environment
    sanitize_command_env(
        &mut env_vars,
        &agent_env_vars,
        ANTHROPIC_ENV_VARS_TO_SANITIZE,
    );

    // Assert: Explicitly set Anthropic vars should be preserved with agent's values
    assert_eq!(
        env_vars.get("ANTHROPIC_API_KEY"),
        Some(&"agent-specific-key".to_string()),
        "ANTHROPIC_API_KEY explicitly set by agent should be preserved"
    );
    assert_eq!(
        env_vars.get("ANTHROPIC_BASE_URL"),
        Some(&"https://agent.example.com".to_string()),
        "ANTHROPIC_BASE_URL explicitly set by agent should be preserved"
    );
    assert!(
        !env_vars.contains_key("ANTHROPIC_AUTH_TOKEN"),
        "ANTHROPIC_AUTH_TOKEN not explicitly set by agent should be removed"
    );
    assert_eq!(
        env_vars.get("PATH"),
        Some(&"/usr/bin:/bin".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}

#[test]
fn test_sanitize_command_env_handles_empty_env_vars() {
    // Setup: Empty environment
    let mut env_vars = HashMap::new();
    let agent_env_vars = HashMap::new();

    // Execute: Should not panic on empty input
    sanitize_command_env(
        &mut env_vars,
        &agent_env_vars,
        ANTHROPIC_ENV_VARS_TO_SANITIZE,
    );

    // Assert: Environment should remain empty
    assert!(env_vars.is_empty(), "Empty environment should remain empty");
}

#[test]
fn test_sanitize_command_env_handles_all_anthropic_vars() {
    // Setup: Environment with all Anthropic vars
    let mut env_vars: std::collections::HashMap<String, String> = ANTHROPIC_ENV_VARS_TO_SANITIZE
        .iter()
        .map(|&var| (var.to_string(), format!("value-{var}")))
        .collect();
    env_vars.insert("OTHER_VAR".to_string(), "other-value".to_string());

    let agent_env_vars = HashMap::new();

    // Execute: Sanitize all Anthropic vars
    sanitize_command_env(
        &mut env_vars,
        &agent_env_vars,
        ANTHROPIC_ENV_VARS_TO_SANITIZE,
    );

    // Assert: All Anthropic vars should be removed
    for &var in ANTHROPIC_ENV_VARS_TO_SANITIZE {
        assert!(
            !env_vars.contains_key(var),
            "{var} should be removed when not explicitly set"
        );
    }
    assert_eq!(
        env_vars.get("OTHER_VAR"),
        Some(&"other-value".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}
