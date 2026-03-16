fn parse_ccs_profiles_from_config_yaml(content: &str) -> std::collections::HashMap<String, String> {
    // Minimal YAML extractor for CCS `config.yaml`.
    // Source (CCS): `dist/config/unified-config-types.d.ts` and
    // `dist/utils/config-manager.js` getSettingsPath() uses `profiles.<name>.settings`.
    //
    // CCS writes this file via js-yaml with quotingType='"', producing a predictable shape:
    // profiles:
    //   glm:
    //     type: api
    //     settings: "~/.ccs/glm.settings.json"
    let mut in_profiles = false;
    let mut profiles_indent = 0usize;
    let mut current_profile: Option<(String, usize)> = None;
    let mut out: std::collections::HashMap<String, String> = std::collections::HashMap::new();

    for raw_line in content.lines() {
        let line = raw_line.trim_end();
        if line.trim().is_empty() {
            continue;
        }
        let trimmed = line.trim_start();
        if trimmed.starts_with('#') {
            continue;
        }

        let indent = line.len().saturating_sub(trimmed.len());

        if !in_profiles {
            if trimmed == "profiles:" {
                in_profiles = true;
                profiles_indent = indent;
                continue;
            }
            continue;
        }

        // End of `profiles:` block when indentation drops back.
        if indent <= profiles_indent {
            break;
        }

        // Profile entry line: "<indent> name:" (or with inline mapping).
        //
        // CCS writes YAML with two-space indentation, but be tolerant of other indentation
        // styles to reduce surprising `ProfileNotFound` behavior.
        if indent > profiles_indent && current_profile.is_none() {
            if let Some((name, rest)) = trimmed.split_once(':') {
                let profile_name = name.trim().to_string();
                let rest = rest.trim();
                current_profile = Some((profile_name.clone(), indent));

                // Inline mapping form: name: { ..., settings: "..." }
                if rest.contains("settings:") {
                    if let Some(settings) = extract_yaml_inline_settings_value(rest) {
                        out.insert(profile_name, settings);
                    }
                }
                continue;
            }
        }

        // Nested under a profile. Look for "settings:".
        if let Some((profile_name, profile_indent)) = current_profile.as_ref() {
            if indent <= *profile_indent {
                // We've left the current profile's block.
                current_profile = None;
                continue;
            }
            if let Some(value) = trimmed.strip_prefix("settings:") {
                let settings = unquote_yaml_scalar(value.trim());
                if !settings.is_empty() {
                    out.insert(profile_name.clone(), settings);
                }
            }
        }
    }

    out
}

fn extract_yaml_inline_settings_value(inline: &str) -> Option<String> {
    // Very small parser for "{ ..., settings: \"...\" }" emitted by yaml.dump().
    let needle = "settings:";
    let idx = inline.find(needle)?;
    let start = idx.checked_add(needle.len())?;
    let after = inline[start..].trim_start();
    let token = after
        .split(',')
        .next()
        .unwrap_or(after)
        .trim()
        .trim_end_matches('}')
        .trim();
    let value = unquote_yaml_scalar(token);
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

fn unquote_yaml_scalar(value: &str) -> String {
    let v = value.trim();
    if v.len() >= 2
        && ((v.starts_with('"') && v.ends_with('"')) || (v.starts_with('\'') && v.ends_with('\'')))
    {
        // Use checked subtraction - the len() >= 2 check guarantees this is safe
        let end_idx = v.len().saturating_sub(1);
        let inner = &v[1..end_idx];
        // CCS uses js-yaml with double quotes; keep unescaping minimal for paths.
        inner.replace("\\\"", "\"").replace("\\\\", "\\")
    } else {
        v.to_string()
    }
}

fn load_ccs_profiles_from_config_yaml_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
) -> Result<std::collections::HashMap<String, String>, CcsEnvVarsError> {
    let Some(path) = ccs_config_yaml_path_with_env(env) else {
        return Err(CcsEnvVarsError::MissingHomeDir);
    };
    if !fs.exists(&path) {
        return Ok(std::collections::HashMap::new());
    }
    let content = fs
        .read_to_string(&path)
        .map_err(|source| CcsEnvVarsError::ReadConfig {
            path: path.clone(),
            source,
        })?;
    Ok(parse_ccs_profiles_from_config_yaml(&content))
}

fn resolve_ccs_settings_path_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
    profile: &str,
) -> Result<std::path::PathBuf, CcsEnvVarsError> {
    let Some(ccs_dir) = ccs_dir_with_env(env) else {
        return Err(CcsEnvVarsError::MissingHomeDir);
    };

    // 1) Unified YAML config (preferred by CCS when present).
    let yaml_profiles = load_ccs_profiles_from_config_yaml_with_deps(env, fs)?;
    if let Some(settings) = yaml_profiles.get(profile) {
        // Validate path doesn't use traversal or absolute paths
        if !is_path_safe_for_resolution(settings) {
            return Err(CcsEnvVarsError::UnsafeSettingsPath {
                path: ccs_dir.join("config.yaml"),
                settings_path: settings.clone(),
            });
        }
        return Ok(expand_user_path_with_env(env, settings));
    }

    // 2) Legacy config.json.
    let json_profiles = load_ccs_profiles_from_config_json_with_deps(env, fs)?;
    if let Some(settings) = json_profiles.get(profile) {
        // Validate path doesn't use traversal or absolute paths
        if !is_path_safe_for_resolution(settings) {
            return Err(CcsEnvVarsError::UnsafeSettingsPath {
                path: ccs_dir.join("config.json"),
                settings_path: settings.clone(),
            });
        }
        return Ok(expand_user_path_with_env(env, settings));
    }

    // 3) Fallback: direct profile settings file in ~/.ccs/ (common default path).
    // Source (CCS): unified config docs and type comments use "~/.ccs/<profile>.settings.json".
    if is_safe_profile_filename_stem(profile) {
        let candidates = [
            ccs_dir.join(format!("{profile}.settings.json")),
            ccs_dir.join(format!("{profile}.setting.json")),
        ];
        for candidate in candidates {
            if fs.exists(&candidate) {
                return Ok(candidate);
            }
        }
    }

    Err(CcsEnvVarsError::ProfileNotFound {
        profile: profile.to_string(),
        ccs_dir,
    })
}

/// Check if a path string is absolute (starts with / or is a Windows drive/UNC path).
/// Returns true if the path is absolute.
fn is_absolute_path(path: &str) -> bool {
    if path.starts_with('/') {
        return true;
    }
    if cfg!(windows) {
        let mut chars = path.chars();
        match (chars.next(), chars.next()) {
            // UNC paths: \\server\share or \\?\device
            // Drive letter paths: C:\
            (Some('\\'), Some('\\')) | (Some(_), Some(':')) => return true,
            _ => {}
        }
    }
    false
}

/// Validate that a path doesn't escape the intended directory through traversal.
/// Returns true if the path is safe (no `..` components, no absolute paths).
fn is_path_safe_for_resolution(path: &str) -> bool {
    // Reject absolute paths - they could point anywhere on the filesystem
    if is_absolute_path(path) {
        return false;
    }
    // Reject paths containing parent directory references
    if path.contains("..") {
        return false;
    }
    // Reject paths with null bytes
    if path.contains('\0') {
        return false;
    }
    true
}

fn expand_user_path_with_env(env: &dyn CcsEnvironment, path: &str) -> std::path::PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = ccs_home_dir_with_env(env) {
            return home.join(rest);
        }
    }
    // Relative paths are resolved relative to the CCS directory
    if let Some(ccs_dir) = ccs_dir_with_env(env) {
        // If path is not absolute and doesn't start with ~, it's a relative path
        if !is_absolute_path(path) {
            return ccs_dir.join(path);
        }
    }
    std::path::PathBuf::from(path)
}

fn find_env_object(
    json: &serde_json::Value,
) -> Option<&serde_json::Map<String, serde_json::Value>> {
    // Source (CCS): `dist/types/config.d.ts` defines Settings as:
    //   { env?: Record<string, string>, ... }
    // and `dist/types/config.js` validates env values are strings.
    json.as_object()?.get("env")?.as_object()
}
