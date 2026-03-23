/// List all available CCS profile names from JSON files under `~/.ccs/`.
///
/// Returns a Vec of profile names (derived from file names like
/// `{profile}.settings.json`, `{profile}.setting.json`, or `{profile}.json`).
/// Returns an empty Vec if the .ccs directory doesn't exist or cannot be read.
fn list_available_ccs_profiles_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
) -> Vec<String> {
    let Some(ccs_dir) = ccs_dir_with_env(env) else {
        return Vec::new();
    };

    // Collect profiles from all sources using iterator chaining
    let yaml_keys: Vec<String> = load_ccs_profiles_from_config_yaml_with_deps(env, fs)
        .map(|m| m.into_keys().collect::<Vec<_>>())
        .unwrap_or_default();
    let json_keys: Vec<String> = load_ccs_profiles_from_config_json_with_deps(env, fs)
        .map(|m| m.into_keys().collect::<Vec<_>>())
        .unwrap_or_default();
    let file_keys: Vec<String> = list_ccs_json_files_with_fs(fs, &ccs_dir)
        .map(|files| {
            files
                .iter()
                .filter_map(|path| {
                    path.file_name()
                        .and_then(|n| n.to_str())
                        .filter(|name| is_ccs_settings_filename(name))
                        .and_then(derive_ccs_profile_name_from_filename)
                })
                .collect()
        })
        .unwrap_or_default();

    // Chain all sources, collect to HashSet for uniqueness, then BTreeSet for ordering
    yaml_keys
        .into_iter()
        .chain(json_keys)
        .chain(file_keys)
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect()
}

/// Find suggestions for a CCS profile name using case-insensitive matching.
///
/// Returns profile names from ~/.ccs that match the input case-insensitively,
/// or contain the input as a substring.
pub fn find_ccs_profile_suggestions(input: &str) -> Vec<String> {
    find_ccs_profile_suggestions_with_deps(&RealCcsEnvironment, &RealCcsFilesystem, input)
}

fn find_ccs_profile_suggestions_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
    input: &str,
) -> Vec<String> {
    let available = list_available_ccs_profiles_with_deps(env, fs);
    let input_lower = input.to_lowercase();

    available
        .into_iter()
        .filter(|profile| {
            let profile_lower = profile.to_lowercase();
            // Exact case-insensitive match
            profile_lower == input_lower
                || // Substring match (user typed part of the name)
                profile_lower.contains(&input_lower)
                || input_lower.contains(&profile_lower)
        })
        .collect()
}

/// Load environment variables from a CCS settings file.
///
/// CCS stores profile -> settings file mapping in `~/.ccs/config.json` and/or
/// `~/.ccs/config.yaml`, and stores environment variables inside the settings file
/// under the `env` key (values must be strings).
///
/// Source (CCS): `dist/utils/config-manager.js` and `dist/types/config.d.ts`.
///
/// All key/value pairs found in the env map are imported as temporary process
/// environment variables for the agent invocation (they are not persisted).
///
/// # Arguments
///
/// * `profile` - The CCS profile name (e.g., "glm" for a matching `~/.ccs/glm.settings.json` file)
///
/// # Returns
///
/// Returns `HashMap<String, String>` with environment variables if successful.
/// Returns an error with context if the settings file cannot be read/parsed.
///
/// # Example
///
/// ```ignore
/// let env_vars = load_ccs_env_vars("glm").unwrap_or_default();
/// // env_vars contains: {
/// //   "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
/// //   "ANTHROPIC_AUTH_TOKEN": "...",
/// //   "ANTHROPIC_MODEL": "glm-4.7",
/// // }
/// ```
pub fn load_ccs_env_vars(
    profile: &str,
) -> Result<std::collections::HashMap<String, String>, CcsEnvVarsError> {
    load_ccs_env_vars_with_deps(&RealCcsEnvironment, &RealCcsFilesystem, profile)
}

/// Testable variant of [`load_ccs_env_vars`] for dependency injection.
///
/// This allows tests to mock both environment variables and filesystem access.
pub fn load_ccs_env_vars_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
    profile: &str,
) -> Result<std::collections::HashMap<String, String>, CcsEnvVarsError> {
    if profile.is_empty() {
        return Err(CcsEnvVarsError::InvalidProfile {
            profile: profile.to_string(),
        });
    }

    let settings_path = resolve_ccs_settings_path_with_deps(env, fs, profile)?;

    // Read and parse the settings file
    let content =
        fs.read_to_string(&settings_path)
            .map_err(|source| CcsEnvVarsError::ReadFile {
                path: settings_path.clone(),
                source,
            })?;

    // Parse JSON
    let json: serde_json::Value =
        serde_json::from_str(&content).map_err(|source| CcsEnvVarsError::ParseJson {
            path: settings_path.clone(),
            source,
        })?;

    let env_obj = find_env_object(&json).ok_or_else(|| CcsEnvVarsError::MissingEnv {
        path: settings_path.clone(),
    })?;

    // Convert to HashMap<String, String> - functional style with early error termination
    let env_vars: std::collections::HashMap<String, String> = env_obj
        .iter()
        .map(|(key, value)| {
            if !is_valid_env_var_name_portable(key) {
                return Err(CcsEnvVarsError::InvalidEnvVarName {
                    path: settings_path.clone(),
                    key: key.clone(),
                });
            }
            if is_dangerous_env_var_name(key) {
                return Err(CcsEnvVarsError::DangerousEnvVar {
                    path: settings_path.clone(),
                    key: key.clone(),
                });
            }
            let str_value =
                value
                    .as_str()
                    .ok_or_else(|| CcsEnvVarsError::NonStringEnvVarValue {
                        path: settings_path.clone(),
                        key: key.clone(),
                    })?;
            if !is_safe_env_var_value(str_value) {
                return Err(CcsEnvVarsError::UnsafeEnvVarValue {
                    path: settings_path.clone(),
                    key: key.clone(),
                });
            }
            Ok((key.clone(), str_value.to_string()))
        })
        .collect::<Result<_, _>>()?;

    Ok(env_vars)
}

/// Find the claude CLI binary path.
///
/// Returns the path to the claude command if found in PATH.
/// Returns None if claude is not installed or not in PATH.
pub fn find_claude_binary() -> Option<std::path::PathBuf> {
    which::which("claude").ok()
}
