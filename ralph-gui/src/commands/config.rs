use ralph_workflow::config::unified::UnifiedConfig;
use serde::{Deserialize, Serialize};

/// Serializable representation of the Ralph configuration for the GUI.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigView {
    pub verbosity: u8,
    pub developer_iters: u32,
    pub reviewer_reviews: u32,
    pub checkpoint_enabled: bool,
    pub isolation_mode: bool,
    pub interactive: bool,
    pub review_depth: String,
    pub max_dev_continuations: u32,
}

impl From<&UnifiedConfig> for ConfigView {
    fn from(c: &UnifiedConfig) -> Self {
        Self {
            verbosity: c.general.verbosity,
            developer_iters: c.general.developer_iters,
            reviewer_reviews: c.general.reviewer_reviews,
            checkpoint_enabled: c.general.workflow.checkpoint_enabled,
            isolation_mode: c.general.execution.isolation_mode,
            interactive: c.general.behavior.interactive,
            review_depth: c.general.review_depth.clone(),
            max_dev_continuations: c.general.max_dev_continuations,
        }
    }
}

/// Get the global Ralph configuration (from `~/.config/ralph-workflow.toml`).
///
/// Returns defaults if the file does not exist.
///
/// # Errors
///
/// Returns an error string if the config file cannot be read or parsed.
#[tauri::command]
pub fn get_global_config() -> Result<ConfigView, String> {
    Ok(ConfigView::from(
        &UnifiedConfig::load_default().unwrap_or_default(),
    ))
}

/// Get the project-level Ralph configuration (from `<repo>/.agent/ralph-workflow.toml`).
///
/// Returns `None` if no project config exists.
///
/// # Errors
///
/// Returns an error string if the config file cannot be parsed.
#[tauri::command]
pub fn get_project_config(repo_path: String) -> Result<Option<ConfigView>, String> {
    let config_path = std::path::PathBuf::from(repo_path)
        .join(".agent")
        .join("ralph-workflow.toml");

    if !config_path.exists() {
        return Ok(None);
    }

    let config = UnifiedConfig::load_from_path(&config_path)
        .map_err(|e| format!("Failed to load project config: {e}"))?;

    Ok(Some(ConfigView::from(&config)))
}

/// Get the effective configuration (global merged with project overrides).
///
/// # Errors
///
/// Returns an error string if configs cannot be read.
#[tauri::command]
pub fn get_effective_config(repo_path: String) -> Result<ConfigView, String> {
    let global = UnifiedConfig::load_default().unwrap_or_default();

    let project_config_path = std::path::PathBuf::from(repo_path)
        .join(".agent")
        .join("ralph-workflow.toml");

    if project_config_path.exists() {
        let project_content = std::fs::read_to_string(&project_config_path)
            .map_err(|e| format!("Failed to read project config: {e}"))?;
        let project_parsed = UnifiedConfig::load_from_content(&project_content)
            .map_err(|e| format!("Failed to parse project config: {e}"))?;
        let merged = global.merge_with_content(&project_content, &project_parsed);
        Ok(ConfigView::from(&merged))
    } else {
        Ok(ConfigView::from(&global))
    }
}

/// Save the global Ralph configuration.
///
/// # Errors
///
/// Returns an error if the config directory cannot be created or the file cannot be written.
///
/// # Panics
///
/// Panics if the config path has no parent directory (should not happen in practice).
#[tauri::command]
pub fn save_global_config(config_toml: String) -> Result<(), String> {
    // Validate the TOML first
    UnifiedConfig::load_from_content(&config_toml).map_err(|e| format!("Invalid config: {e}"))?;

    let config_path = dirs::home_dir()
        .ok_or_else(|| "Cannot determine home directory".to_string())?
        .join(".config")
        .join("ralph-workflow.toml");

    std::fs::create_dir_all(config_path.parent().expect("config path must have parent"))
        .map_err(|e| format!("Failed to create config directory: {e}"))?;

    // Pass config_toml by value (moves it) rather than by reference
    std::fs::write(&config_path, config_toml)
        .map_err(|e| format!("Failed to write global config: {e}"))
}

/// Save the project-level Ralph configuration.
///
/// # Errors
///
/// Returns an error if the `.agent` directory cannot be created or the file cannot be written.
#[tauri::command]
pub fn save_project_config(repo_path: String, config_toml: String) -> Result<(), String> {
    // Validate the TOML first
    UnifiedConfig::load_from_content(&config_toml).map_err(|e| format!("Invalid config: {e}"))?;

    let agent_dir = std::path::PathBuf::from(repo_path).join(".agent");
    std::fs::create_dir_all(&agent_dir)
        .map_err(|e| format!("Failed to create .agent directory: {e}"))?;

    let config_path = agent_dir.join("ralph-workflow.toml");
    // Pass config_toml by value (moves it) rather than by reference
    std::fs::write(&config_path, config_toml)
        .map_err(|e| format!("Failed to write project config: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_get_global_config_returns_default_when_no_file() {
        let result = get_global_config();
        assert!(
            result.is_ok(),
            "get_global_config should not fail: {result:?}"
        );
        let config = result.unwrap();
        assert!(config.verbosity <= 4, "Verbosity should be 0-4");
    }

    #[test]
    fn test_get_project_config_returns_none_when_no_file() {
        let dir = TempDir::new().unwrap();
        let result = get_project_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());
    }

    #[test]
    fn test_get_project_config_returns_config_when_file_exists() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        std::fs::write(&config_path, "[general]\nverbosity = 3\n").unwrap();

        let result = get_project_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config = result.unwrap();
        assert!(config.is_some());
        assert_eq!(config.unwrap().verbosity, 3);
    }

    #[test]
    fn test_get_effective_config_returns_global_when_no_project_config() {
        let dir = TempDir::new().unwrap();
        let result = get_effective_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
    }

    #[test]
    fn test_get_effective_config_merges_project_overrides() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

        let result = get_effective_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config = result.unwrap();
        assert_eq!(
            config.developer_iters, 7,
            "Project override should take effect"
        );
    }

    #[test]
    fn test_save_project_config_rejects_invalid_toml() {
        let dir = TempDir::new().unwrap();
        let result = save_project_config(
            dir.path().to_string_lossy().to_string(),
            "this is not valid toml !!!!!".to_string(),
        );
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Invalid config"));
    }

    #[test]
    fn test_save_project_config_writes_valid_toml() {
        let dir = TempDir::new().unwrap();
        let result = save_project_config(
            dir.path().to_string_lossy().to_string(),
            "[general]\nverbosity = 1\n".to_string(),
        );
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config_path = dir.path().join(".agent").join("ralph-workflow.toml");
        assert!(config_path.exists(), "Config file should have been created");
    }
}
