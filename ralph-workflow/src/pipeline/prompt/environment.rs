/// Sanitize environment variables for agent subprocess execution.
///
/// This function removes problematic Anthropic environment variables from the
/// provided environment map, unless they were explicitly set by the agent
/// configuration.
///
/// # Arguments
///
/// * `env_vars` - Mutable reference to environment variables map
/// * `agent_env_vars` - Environment variables explicitly set by agent config
/// * `vars_to_sanitize` - List of environment variable names to remove
///
/// # Behavior
///
/// - Removes all vars in `vars_to_sanitize` from `env_vars`
/// - EXCEPT for vars that are present in `agent_env_vars` (explicitly set)
/// - This prevents GLM CCS credentials from leaking into agent subprocesses
///
/// # Example
///
/// ```ignore
/// let mut env = std::env::vars().collect::<HashMap<_, _>>();
/// let agent_vars = HashMap::from([("ANTHROPIC_API_KEY", "agent-key")]);
/// sanitize_command_env(&mut env, &agent_vars, ANTHROPIC_VARS);
/// // env no longer contains ANTHROPIC_BASE_URL (not in agent_vars)
/// // env still contains ANTHROPIC_API_KEY (explicitly set by agent)
/// ```
pub fn sanitize_command_env(
    env_vars: &mut std::collections::HashMap<String, String>,
    agent_env_vars: &std::collections::HashMap<String, String>,
    vars_to_sanitize: &[&str],
) {
    for &var in vars_to_sanitize {
        if !agent_env_vars.contains_key(var) {
            env_vars.remove(var);
        }
    }
}
