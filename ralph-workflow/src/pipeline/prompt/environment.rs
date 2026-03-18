/// Sanitize environment variables for agent subprocess execution.
///
/// This function removes problematic Anthropic environment variables from the
/// provided environment map, unless they were explicitly set by the agent
/// configuration.
///
/// # Arguments
///
/// * `env_vars` - Environment variables map to sanitize
/// * `agent_env_vars` - Environment variables explicitly set by agent config
/// * `vars_to_sanitize` - List of environment variable names to remove
///
/// # Behavior
///
/// - Removes all vars in `vars_to_sanitize` from env_vars
/// - EXCEPT for vars that are present in `agent_env_vars` (explicitly set)
/// - This prevents GLM CCS credentials from leaking into agent subprocesses
///
/// # Returns
///
/// A new `HashMap` with the sanitized environment variables.
#[must_use]
pub fn sanitize_command_env(
    env_vars: std::collections::HashMap<String, String>,
    agent_env_vars: &std::collections::HashMap<String, String>,
    vars_to_sanitize: &[&str],
) -> std::collections::HashMap<String, String> {
    let agent_keys: std::collections::HashSet<_> = agent_env_vars.keys().collect();
    env_vars
        .into_iter()
        .filter(|(key, _)| !vars_to_sanitize.contains(&key.as_str()) || agent_keys.contains(key))
        .collect()
}
