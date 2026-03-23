/// Handle the smart `--init` flag with a custom path resolver.
///
/// This function intelligently determines what the user wants to initialize:
/// - If a value is provided and matches a known template name -> create PROMPT.md
/// - If config doesn't exist and no template specified -> create config
/// - If config exists but PROMPT.md doesn't -> prompt to create PROMPT.md
/// - If both exist -> show helpful message about what's already set up
///
/// # Arguments
///
/// * `template_arg` - Optional template name from `--init=TEMPLATE`
/// * `force` - If true, overwrite existing PROMPT.md without prompting
/// * `colors` - Terminal color configuration for output
/// * `env` - Path resolver for determining config file locations
///
/// # Returns
///
/// Returns `Ok(true)` if the flag was handled (program should exit after),
/// or `Ok(false)` if not handled, or an error if initialization failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_smart_init_with<R: ConfigEnvironment>(
    template_arg: Option<&str>,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let config_path = env
        .unified_config_path()
        .ok_or_else(|| anyhow::anyhow!("Cannot determine config directory (no home directory)"))?;
    let prompt_path = env.prompt_path();
    handle_smart_init_at_paths_with_env(
        template_arg,
        force,
        colors,
        &config_path,
        &prompt_path,
        env,
    )
}

/// Handle the smart `--init` flag using the default path resolver.
///
/// This is a convenience wrapper that uses [`RealConfigEnvironment`] internally.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_smart_init(
    template_arg: Option<&str>,
    force: bool,
    colors: Colors,
) -> anyhow::Result<bool> {
    handle_smart_init_with(template_arg, force, colors, &RealConfigEnvironment)
}

fn handle_smart_init_at_paths_with_env<R: ConfigEnvironment>(
    template_arg: Option<&str>,
    _force: bool,
    colors: Colors,
    config_path: &std::path::Path,
    prompt_path: &Path,
    env: &R,
) -> anyhow::Result<bool> {
    let _config_exists = env.file_exists(config_path);
    let _prompt_exists = env.file_exists(prompt_path);

    // If a template name is provided (non-empty), treat it as --init <template>
    if let Some(template_name) = template_arg {
        if !template_name.is_empty() {
            return handle_init_template_arg_at_path_with_env(
                template_name,
                prompt_path,
                colors,
                env,
            );
        }
        // Empty string means --init was used without a value, fall through to smart inference
    }

    // No template provided - use smart inference based on current state
    handle_init_state_inference_with_env(
        config_path,
        prompt_path,
        template_arg,
        colors,
        env,
    )
}
