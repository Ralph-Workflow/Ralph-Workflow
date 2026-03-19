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
    let env_vars = HashMap::from([
        ("ANTHROPIC_API_KEY".to_string(), "glm-test-key".to_string()),
        (
            "ANTHROPIC_BASE_URL".to_string(),
            "https://glm.example.com".to_string(),
        ),
        ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ("HOME".to_string(), "/home/user".to_string()),
    ]);
    let agent_env_vars = HashMap::new();

    let sanitized = sanitize_command_env(env_vars, &agent_env_vars, ANTHROPIC_ENV_VARS_TO_SANITIZE);

    assert!(
        !sanitized.contains_key("ANTHROPIC_API_KEY"),
        "ANTHROPIC_API_KEY should be removed when not explicitly set by agent"
    );
    assert!(
        !sanitized.contains_key("ANTHROPIC_BASE_URL"),
        "ANTHROPIC_BASE_URL should be removed when not explicitly set by agent"
    );
    assert_eq!(
        sanitized.get("PATH"),
        Some(&"/usr/bin:/bin".to_string()),
        "Non-Anthropic vars should be preserved"
    );
    assert_eq!(
        sanitized.get("HOME"),
        Some(&"/home/user".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}

#[test]
fn test_sanitize_command_env_preserves_explicitly_set_anthropic_vars() {
    let env_vars = std::env::vars()
        .chain(
            [
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
            ]
            .into_iter(),
        )
        .collect();
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

    let sanitized = sanitize_command_env(env_vars, &agent_env_vars, ANTHROPIC_ENV_VARS_TO_SANITIZE);

    assert_eq!(
        sanitized.get("ANTHROPIC_API_KEY"),
        Some(&"agent-specific-key".to_string()),
        "ANTHROPIC_API_KEY explicitly set by agent should be preserved"
    );
    assert_eq!(
        sanitized.get("ANTHROPIC_BASE_URL"),
        Some(&"https://agent.example.com".to_string()),
        "ANTHROPIC_BASE_URL explicitly set by agent should be preserved"
    );
    assert!(
        !sanitized.contains_key("ANTHROPIC_AUTH_TOKEN"),
        "ANTHROPIC_AUTH_TOKEN not explicitly set by agent should be removed"
    );
    assert_eq!(
        sanitized.get("PATH"),
        Some(&"/usr/bin:/bin".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}

#[test]
fn test_sanitize_command_env_handles_empty_env_vars() {
    let env_vars = HashMap::new();
    let agent_env_vars = HashMap::new();

    let sanitized = sanitize_command_env(env_vars, &agent_env_vars, ANTHROPIC_ENV_VARS_TO_SANITIZE);

    assert!(
        sanitized.is_empty(),
        "Empty environment should produce empty sanitized result"
    );
}

#[test]
fn test_sanitize_command_env_handles_all_anthropic_vars() {
    let env_vars: HashMap<String, String> = ANTHROPIC_ENV_VARS_TO_SANITIZE
        .iter()
        .map(|&var| (var.to_string(), format!("value-{var}")))
        .chain(std::iter::once((
            "OTHER_VAR".to_string(),
            "other-value".to_string(),
        )))
        .collect();
    let agent_env_vars = HashMap::new();

    let sanitized = sanitize_command_env(env_vars, &agent_env_vars, ANTHROPIC_ENV_VARS_TO_SANITIZE);

    ANTHROPIC_ENV_VARS_TO_SANITIZE.iter().for_each(|&var| {
        assert!(
            !sanitized.contains_key(var),
            "{var} should be removed when not explicitly set"
        );
    });
    assert_eq!(
        sanitized.get("OTHER_VAR"),
        Some(&"other-value".to_string()),
        "Non-Anthropic vars should be preserved"
    );
}
