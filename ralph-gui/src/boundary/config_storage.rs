use ralph_workflow::config::unified::UnifiedConfig;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

fn io_context<T>(result: std::io::Result<T>, context: &str) -> Result<T, String> {
    result.map_err(|error| format!("{context}: {error}"))
}

fn home_dir_or_err() -> Result<PathBuf, String> {
    dirs::home_dir().ok_or_else(|| "Cannot determine home directory".to_string())
}

pub fn save_global_config(config_toml: String) -> Result<(), String> {
    UnifiedConfig::load_from_content(&config_toml).map_err(|e| format!("Invalid config: {e}"))?;

    let config_path = home_dir_or_err()?
        .join(".config")
        .join("ralph-workflow.toml");
    let parent = config_path
        .parent()
        .expect("config path must have a parent directory");
    io_context(
        fs::create_dir_all(parent),
        "Failed to create config directory",
    )?;
    io_context(
        fs::write(&config_path, config_toml),
        "Failed to write global config",
    )
}

pub fn get_raw_global_config_toml() -> Result<String, String> {
    let config_path = home_dir_or_err()?
        .join(".config")
        .join("ralph-workflow.toml");

    if !config_path.exists() {
        return Ok(String::new());
    }

    io_context(
        fs::read_to_string(&config_path),
        "Failed to read global config",
    )
}

pub fn get_raw_project_config_toml(repo_path: String) -> Result<String, String> {
    let config_path = PathBuf::from(repo_path)
        .join(".agent")
        .join("ralph-workflow.toml");

    if !config_path.exists() {
        return Ok(String::new());
    }

    io_context(
        fs::read_to_string(&config_path),
        "Failed to read project config",
    )
}

pub fn save_project_config(repo_path: String, config_toml: String) -> Result<(), String> {
    UnifiedConfig::load_from_content(&config_toml).map_err(|e| format!("Invalid config: {e}"))?;

    let agent_dir = PathBuf::from(repo_path).join(".agent");
    io_context(
        fs::create_dir_all(&agent_dir),
        "Failed to create .agent directory",
    )?;

    let config_path = agent_dir.join("ralph-workflow.toml");
    io_context(
        fs::write(&config_path, config_toml),
        "Failed to write project config",
    )
}

pub fn validate_config_toml(config_toml: String) -> Result<Option<String>, String> {
    match UnifiedConfig::load_from_content(&config_toml) {
        Ok(_) => Ok(None),
        Err(e) => Ok(Some(format!("{e}"))),
    }
}

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

fn gui_config_path() -> Result<PathBuf, String> {
    home_dir_or_err().map(|home| home.join(".config").join("ralph-gui.toml"))
}

fn load_gui_config() -> Result<GuiConfig, String> {
    let path = gui_config_path()?;
    if !path.exists() {
        return Ok(GuiConfig::default());
    }
    let content = io_context(fs::read_to_string(&path), "Failed to read gui config")?;
    toml::from_str::<GuiConfig>(&content).map_err(|e| format!("Failed to parse gui config: {e}"))
}

fn save_gui_config(config: &GuiConfig) -> Result<(), String> {
    let path = gui_config_path()?;
    let content =
        toml::to_string(config).map_err(|e| format!("Failed to serialize gui config: {e}"))?;

    if let Some(parent) = path.parent() {
        io_context(
            fs::create_dir_all(parent),
            "Failed to create config directory",
        )?;
    }

    io_context(fs::write(&path, &content), "Failed to write gui config")?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode_result = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
        io_context(mode_result, "Failed to set config file permissions")?;
    }

    Ok(())
}

pub fn get_ai_api_key() -> Result<String, String> {
    let config = load_gui_config()?;
    Ok(config.ai.api_key)
}

pub fn save_ai_api_key(api_key: String) -> Result<(), String> {
    if api_key.trim().is_empty() {
        return Err("API key must not be empty".to_string());
    }
    let config = load_gui_config()?;
    let updated = GuiConfig {
        ai: AiConfig {
            api_key,
            ..config.ai.clone()
        },
        ..config
    };
    save_gui_config(&updated)
}
