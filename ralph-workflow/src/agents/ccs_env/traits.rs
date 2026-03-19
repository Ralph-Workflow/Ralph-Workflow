// ============================================================================
// Environment and Filesystem Traits for Testability
// ============================================================================

/// Trait for accessing CCS-related environment variables.
///
/// This trait enables dependency injection for testing without global state pollution.
pub trait CcsEnvironment {
    fn get_var(&self, name: &str) -> Option<String>;

    fn home_dir(&self) -> Option<PathBuf>;
}

/// Trait for CCS filesystem operations.
///
/// This trait abstracts filesystem access for testability.
pub trait CcsFilesystem {
    fn exists(&self, path: &std::path::Path) -> bool;

    fn read_to_string(&self, path: &std::path::Path) -> std::io::Result<String>;

    fn read_dir(&self, path: &std::path::Path) -> std::io::Result<Vec<CcsDirEntry>>;
}

/// Directory entry for `CcsFilesystem`.
pub struct CcsDirEntry {
    pub path: std::path::PathBuf,
    pub file_name: String,
    pub is_file: bool,
}

/// Subset of CCS' legacy `~/.ccs/config.json` format.
///
/// Source (CCS): `dist/types/config.d.ts` and `dist/utils/config-manager.js`.
#[derive(Debug, serde::Deserialize)]
pub(crate) struct CcsConfigJson {
    pub(crate) profiles: std::collections::HashMap<String, String>,
}

/// Errors that can occur when loading CCS environment variables.
#[derive(Debug, thiserror::Error)]
pub enum CcsEnvVarsError {
    #[error("Invalid CCS profile name '{profile}' (must be non-empty)")]
    InvalidProfile { profile: String },
    #[error("Could not determine home directory for CCS settings")]
    MissingHomeDir,
    #[error("No CCS settings file found for profile '{profile}' in {ccs_dir}")]
    ProfileNotFound {
        profile: String,
        ccs_dir: std::path::PathBuf,
    },
    #[error("Failed to read CCS config at {path}: {source}")]
    ReadConfig {
        path: std::path::PathBuf,
        source: std::io::Error,
    },
    #[error("Failed to parse CCS config JSON at {path}: {source}")]
    ParseConfigJson {
        path: std::path::PathBuf,
        source: serde_json::Error,
    },
    #[error("Failed to read CCS settings file at {path}: {source}")]
    ReadFile {
        path: std::path::PathBuf,
        source: std::io::Error,
    },
    #[error("Failed to parse CCS settings JSON at {path}: {source}")]
    ParseJson {
        path: std::path::PathBuf,
        source: serde_json::Error,
    },
    #[error("Could not find an environment-variable map in CCS settings JSON at {path}")]
    MissingEnv { path: std::path::PathBuf },
    #[error("CCS settings JSON at {path} contains invalid env var name '{key}'")]
    InvalidEnvVarName {
        path: std::path::PathBuf,
        key: String,
    },
    #[error("CCS settings JSON at {path} has non-string env value for key '{key}'")]
    NonStringEnvVarValue {
        path: std::path::PathBuf,
        key: String,
    },
    #[error(
        "CCS settings JSON at {path} contains dangerous env var '{key}' (not allowed from external config)"
    )]
    DangerousEnvVar {
        path: std::path::PathBuf,
        key: String,
    },
    #[error("CCS settings JSON at {path} contains unsafe env value for key '{key}'")]
    UnsafeEnvVarValue {
        path: std::path::PathBuf,
        key: String,
    },
    #[error(
        "CCS config at {path} contains unsafe settings path '{settings_path}' (path traversal not allowed)"
    )]
    UnsafeSettingsPath {
        path: std::path::PathBuf,
        settings_path: String,
    },
}

/// List of dangerous environment variable names that should not be allowed from external config.
const DANGEROUS_ENV_VAR_NAMES: &[&str] = &[
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "IFS",
    "PATH",
    "SHELL",
    "ENV",
    "BASH_ENV",
];

/// Check if an environment variable name is dangerous (could be used for injection).
pub(crate) fn is_dangerous_env_var_name(name: &str) -> bool {
    DANGEROUS_ENV_VAR_NAMES
        .iter()
        .any(|&dangerous| name.eq_ignore_ascii_case(dangerous))
}

pub(crate) fn is_valid_env_var_name_portable(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    if name.contains('\0') || name.contains('=') {
        return false;
    }
    // On Windows, environment variable names cannot start with '='.
    #[cfg(windows)]
    {
        if name.starts_with('=') {
            return false;
        }
    }
    true
}

/// Validate environment variable value for safety.
pub(crate) fn is_safe_env_var_value(value: &str) -> bool {
    // Reject null bytes and newlines (could be used for injection)
    if value.contains('\0') || value.contains('\n') || value.contains('\r') {
        return false;
    }
    // Reject backticks (command substitution in shells)
    if value.contains('`') {
        return false;
    }
    // Allow most other characters
    true
}

pub(crate) fn derive_ccs_profile_name_from_filename(filename: &str) -> Option<String> {
    filename
        .strip_suffix(".settings.json")
        .or_else(|| filename.strip_suffix(".setting.json"))
        .or_else(|| filename.strip_suffix(".json"))
        .map(std::string::ToString::to_string)
}

pub(crate) fn is_ccs_settings_filename(name: &str) -> bool {
    name.ends_with(".settings.json") || name.ends_with(".setting.json")
}

pub(crate) fn is_safe_profile_filename_stem(stem: &str) -> bool {
    !stem.is_empty()
        && stem
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
}

pub(crate) fn list_ccs_json_files_with_fs(
    fs: &dyn CcsFilesystem,
    ccs_dir: &std::path::Path,
) -> Result<Vec<std::path::PathBuf>, std::io::Error> {
    fs.read_dir(ccs_dir).map(|entries| {
        entries
            .into_iter()
            .filter(|entry| entry.is_file)
            .filter(|entry| {
                std::path::Path::new(&entry.file_name)
                    .extension()
                    .is_some_and(|ext| ext.eq_ignore_ascii_case("json"))
            })
            .map(|entry| entry.path)
            .collect()
    })
}

pub(crate) fn ccs_home_dir_with_env(env: &dyn CcsEnvironment) -> Option<std::path::PathBuf> {
    env.get_var("CCS_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| env.home_dir())
}

pub(crate) fn ccs_dir_with_env(env: &dyn CcsEnvironment) -> Option<std::path::PathBuf> {
    ccs_home_dir_with_env(env).map(|home| home.join(".ccs"))
}

pub(crate) fn ccs_config_json_path_with_env(
    env: &dyn CcsEnvironment,
) -> Option<std::path::PathBuf> {
    env.get_var("CCS_CONFIG")
        .map(std::path::PathBuf::from)
        .or_else(|| ccs_dir_with_env(env).map(|d| d.join("config.json")))
}

pub(crate) fn ccs_config_yaml_path_with_env(
    env: &dyn CcsEnvironment,
) -> Option<std::path::PathBuf> {
    ccs_dir_with_env(env).map(|d| d.join("config.yaml"))
}

pub(crate) fn load_ccs_profiles_from_config_json_with_deps(
    env: &dyn CcsEnvironment,
    fs: &dyn CcsFilesystem,
) -> Result<std::collections::HashMap<String, String>, CcsEnvVarsError> {
    let Some(path) = ccs_config_json_path_with_env(env) else {
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
    let parsed: CcsConfigJson =
        serde_json::from_str(&content).map_err(|source| CcsEnvVarsError::ParseConfigJson {
            path: path.clone(),
            source,
        })?;
    Ok(parsed.profiles)
}
