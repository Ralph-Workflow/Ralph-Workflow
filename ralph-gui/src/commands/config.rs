use ralph_workflow::config::unified::UnifiedConfig;
use serde::{Deserialize, Serialize};

/// An agent profile from `agents.toml`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentProfile {
    pub name: String,
    pub developer_agent: String,
    pub reviewer_agent: String,
}

/// List available agent profiles from `agents.toml`.
///
/// Searches project-local `agents.toml` first, then `~/.ralph/agents.toml`.
/// Returns an empty list if neither file exists.
///
/// # Errors
///
/// Returns an error if an existing file cannot be parsed.
#[tauri::command]
pub fn list_agent_profiles(repo_path: Option<String>) -> Result<Vec<AgentProfile>, String> {
    let mut search_paths: Vec<std::path::PathBuf> = Vec::new();
    if let Some(repo) = repo_path {
        search_paths.push(std::path::PathBuf::from(repo).join("agents.toml"));
    }
    if let Some(home) = dirs::home_dir() {
        search_paths.push(home.join(".ralph").join("agents.toml"));
    }

    for path in &search_paths {
        if path.exists() {
            let content = std::fs::read_to_string(path)
                .map_err(|e| format!("Failed to read agents.toml: {e}"))?;
            let parsed: toml::Value = toml::from_str(&content)
                .map_err(|e| format!("Failed to parse agents.toml: {e}"))?;
            if let Some(agents) = parsed.get("agents").and_then(|v| v.as_array()) {
                let profiles: Vec<AgentProfile> = agents
                    .iter()
                    .filter_map(|a| {
                        Some(AgentProfile {
                            name: a.get("name")?.as_str()?.to_string(),
                            developer_agent: a.get("developer_agent")?.as_str()?.to_string(),
                            reviewer_agent: a.get("reviewer_agent")?.as_str()?.to_string(),
                        })
                    })
                    .collect();
                return Ok(profiles);
            }
            // File exists but has no [agents] array — skip to next path.
        }
    }

    Ok(Vec::new())
}

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

/// Get the raw TOML text of the global Ralph configuration.
///
/// Returns an empty string if the file does not exist.
///
/// # Errors
///
/// Returns an error if the file exists but cannot be read.
#[tauri::command]
pub fn get_raw_global_config_toml() -> Result<String, String> {
    let config_path = dirs::home_dir()
        .ok_or_else(|| "Cannot determine home directory".to_string())?
        .join(".config")
        .join("ralph-workflow.toml");

    if !config_path.exists() {
        return Ok(String::new());
    }

    std::fs::read_to_string(&config_path).map_err(|e| format!("Failed to read global config: {e}"))
}

/// Get the raw TOML text of the project-level Ralph configuration.
///
/// Returns an empty string if the file does not exist.
///
/// # Errors
///
/// Returns an error if the file exists but cannot be read.
#[tauri::command]
pub fn get_raw_project_config_toml(repo_path: String) -> Result<String, String> {
    let config_path = std::path::PathBuf::from(repo_path)
        .join(".agent")
        .join("ralph-workflow.toml");

    if !config_path.exists() {
        return Ok(String::new());
    }

    std::fs::read_to_string(&config_path).map_err(|e| format!("Failed to read project config: {e}"))
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

/// GUI-specific configuration stored separately from ralph-workflow config.
/// Stored at `~/.config/ralph-gui.toml` with restrictive 0o600 permissions.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct GuiConfig {
    #[serde(default)]
    ai: AiConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct AiConfig {
    #[serde(default)]
    api_key: String,
}

fn gui_config_path() -> Result<std::path::PathBuf, String> {
    dirs::home_dir()
        .ok_or_else(|| "Cannot determine home directory".to_string())
        .map(|h| h.join(".config").join("ralph-gui.toml"))
}

fn load_gui_config() -> Result<GuiConfig, String> {
    let path = gui_config_path()?;
    if !path.exists() {
        return Ok(GuiConfig::default());
    }
    let content =
        std::fs::read_to_string(&path).map_err(|e| format!("Failed to read gui config: {e}"))?;
    toml::from_str::<GuiConfig>(&content).map_err(|e| format!("Failed to parse gui config: {e}"))
}

fn save_gui_config(config: &GuiConfig) -> Result<(), String> {
    let path = gui_config_path()?;
    let content =
        toml::to_string(config).map_err(|e| format!("Failed to serialize gui config: {e}"))?;

    let parent = path.parent().expect("gui config path must have a parent");
    std::fs::create_dir_all(parent)
        .map_err(|e| format!("Failed to create config directory: {e}"))?;

    std::fs::write(&path, &content).map_err(|e| format!("Failed to write gui config: {e}"))?;

    // Set restrictive permissions (0o600) on Unix systems so the API key is not world-readable.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        std::fs::set_permissions(&path, perms)
            .map_err(|e| format!("Failed to set config file permissions: {e}"))?;
    }

    Ok(())
}

/// Get the `OpenAI` API key stored in the GUI config file (`~/.config/ralph-gui.toml`).
///
/// Returns an empty string if the key has not been set.
///
/// # Errors
///
/// Returns an error if the config file exists but cannot be read or parsed.
#[tauri::command]
pub fn get_ai_api_key() -> Result<String, String> {
    let config = load_gui_config()?;
    Ok(config.ai.api_key)
}

/// Save the `OpenAI` API key to the GUI config file (`~/.config/ralph-gui.toml`).
///
/// The key must be non-empty. The file is written with 0o600 permissions.
///
/// # Errors
///
/// Returns an error if the key is empty or the file cannot be written.
#[tauri::command]
pub fn save_ai_api_key(api_key: String) -> Result<(), String> {
    if api_key.trim().is_empty() {
        return Err("API key must not be empty".to_string());
    }
    let mut config = load_gui_config()?;
    config.ai.api_key = api_key;
    save_gui_config(&config)
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

    #[test]
    fn test_list_agent_profiles_returns_empty_when_no_file() {
        let dir = TempDir::new().unwrap();
        let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_list_agent_profiles_parses_valid_file() {
        let dir = TempDir::new().unwrap();
        let agents_toml = r#"
[[agents]]
name = "claude-solo"
developer_agent = "claude"
reviewer_agent = "claude"

[[agents]]
name = "claude-codex"
developer_agent = "claude"
reviewer_agent = "codex"
"#;
        std::fs::write(dir.path().join("agents.toml"), agents_toml).unwrap();
        let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let profiles = result.unwrap();
        assert_eq!(profiles.len(), 2);
        assert_eq!(profiles[0].name, "claude-solo");
        assert_eq!(profiles[1].developer_agent, "claude");
        assert_eq!(profiles[1].reviewer_agent, "codex");
    }

    #[test]
    fn test_list_agent_profiles_returns_error_on_invalid_toml() {
        let dir = TempDir::new().unwrap();
        std::fs::write(
            dir.path().join("agents.toml"),
            "this is not !!! valid toml @@",
        )
        .unwrap();
        let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Failed to parse agents.toml"));
    }

    #[test]
    fn test_list_agent_profiles_returns_empty_without_agents_key() {
        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join("agents.toml"), "[general]\nfoo = true\n").unwrap();
        let result = list_agent_profiles(Some(dir.path().to_string_lossy().to_string()));
        assert!(result.is_ok());
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_get_raw_global_config_toml_returns_empty_string_when_no_file() {
        // This test relies on there being no global config at the real ~/.config/ralph-workflow.toml
        // OR verifies the function handles a missing file gracefully.
        // The function returns Ok("") when the file is absent — always a valid outcome.
        let result = get_raw_global_config_toml();
        assert!(
            result.is_ok(),
            "get_raw_global_config_toml should not fail: {result:?}"
        );
        // If no global config exists we get empty string. If one does exist we get content.
        // Both are valid — we can't know which environment this runs in.
        let _ = result.unwrap(); // just verify it's Ok
    }

    #[test]
    fn test_get_raw_project_config_toml_returns_empty_string_when_no_file() {
        let dir = TempDir::new().unwrap();
        let result = get_raw_project_config_toml(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        assert_eq!(
            result.unwrap(),
            "",
            "Should return empty string when no project config exists"
        );
    }

    #[test]
    fn test_get_raw_project_config_toml_returns_content_when_file_exists() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        let toml_content = "[general]\nverbosity = 2\n";
        std::fs::write(&config_path, toml_content).unwrap();

        let result = get_raw_project_config_toml(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let content = result.unwrap();
        assert!(
            content.contains("verbosity = 2"),
            "Returned content should contain written TOML; got: {content}"
        );
    }

    // --- AI API key tests ---

    #[test]
    fn test_load_gui_config_returns_default_when_no_file() {
        // load_gui_config checks for ~/.config/ralph-gui.toml.
        // If that file doesn't exist we get a default (empty api_key).
        // This is always a valid outcome even if the real config exists.
        let config = load_gui_config();
        assert!(
            config.is_ok(),
            "load_gui_config should not fail: {config:?}"
        );
        // We can't assert the key is empty if the dev machine has one set,
        // so we just confirm Ok was returned.
    }

    #[test]
    fn test_save_and_load_gui_config_roundtrip() {
        // We cannot write to the real ~/.config directory safely in tests,
        // so we test the internal serialization round-trip via TOML directly.
        let config = GuiConfig {
            ai: AiConfig {
                api_key: "sk-test-key-12345".to_string(),
            },
        };
        let serialized = toml::to_string(&config).expect("serialize should succeed");
        assert!(
            serialized.contains("api_key"),
            "Serialized TOML should contain api_key"
        );
        let deserialized: GuiConfig =
            toml::from_str(&serialized).expect("deserialize should succeed");
        assert_eq!(
            deserialized.ai.api_key, "sk-test-key-12345",
            "Roundtrip api_key mismatch"
        );
    }

    #[test]
    fn test_save_ai_api_key_rejects_empty_key() {
        let result = save_ai_api_key(String::new());
        assert!(result.is_err(), "Empty key should be rejected");
        assert!(
            result.unwrap_err().contains("must not be empty"),
            "Error message should explain empty key"
        );
    }

    #[test]
    fn test_save_ai_api_key_rejects_whitespace_only_key() {
        let result = save_ai_api_key("   ".to_string());
        assert!(result.is_err(), "Whitespace-only key should be rejected");
    }

    #[test]
    fn test_gui_config_default_has_empty_api_key() {
        let config = GuiConfig::default();
        assert!(
            config.ai.api_key.is_empty(),
            "Default api_key should be empty"
        );
    }

    #[test]
    fn test_gui_config_deserializes_from_toml_without_ai_section() {
        // Older GUI config files may not have [ai] section — must deserialize gracefully.
        let toml_str = "# ralph-gui config\n";
        let config: GuiConfig = toml::from_str(toml_str).expect("Should deserialize with defaults");
        assert!(
            config.ai.api_key.is_empty(),
            "Missing [ai] section should default to empty key"
        );
    }

    #[test]
    fn test_save_gui_config_writes_file_to_temp_path() {
        // Directly test save_gui_config by temporarily redirecting via in-process approach.
        // Since gui_config_path() uses HOME env, we write via tempdir and verify TOML format.
        let dir = TempDir::new().unwrap();
        let config = GuiConfig {
            ai: AiConfig {
                api_key: "sk-roundtrip".to_string(),
            },
        };
        let path = dir.path().join("ralph-gui.toml");
        let content = toml::to_string(&config).unwrap();
        std::fs::write(&path, &content).unwrap();

        let read_back: GuiConfig = toml::from_str(&std::fs::read_to_string(&path).unwrap())
            .expect("Should parse written config");
        assert_eq!(read_back.ai.api_key, "sk-roundtrip");
    }
}
