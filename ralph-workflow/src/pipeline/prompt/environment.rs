use std::collections::HashSet;

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
/// # Returns
///
/// A new HashMap with the specified variables removed (unless explicitly set in agent_env_vars)
///
/// # Behavior
///
/// - Removes all vars in `vars_to_sanitize` from the returned map
/// - EXCEPT for vars that are present in `agent_env_vars` (explicitly set)
/// - This prevents GLM CCS credentials from leaking into agent subprocesses
///
/// # Example
///
/// ```ignore
/// let env = std::env::vars().collect::<HashMap<_, _>>();
/// let agent_vars = HashMap::from([("ANTHROPIC_API_KEY", "agent-key")]);
/// let sanitized = sanitize_command_env(env, &agent_vars, ANTHROPIC_VARS);
/// // sanitized no longer contains ANTHROPIC_BASE_URL (not in agent_vars)
/// // sanitized still contains ANTHROPIC_API_KEY (explicitly set by agent)
/// ```
pub fn sanitize_command_env(
    env_vars: std::collections::HashMap<String, String>,
    agent_env_vars: &std::collections::HashMap<String, String>,
    vars_to_sanitize: &[&str],
) -> std::collections::HashMap<String, String> {
    let vars_to_remove: HashSet<String> = vars_to_sanitize
        .iter()
        .map(|s| s.to_string())
        .filter(|s| !agent_env_vars.contains_key(s))
        .collect();

    env_vars
        .into_iter()
        .filter(|(k, _)| !vars_to_remove.contains(k))
        .collect()
}
