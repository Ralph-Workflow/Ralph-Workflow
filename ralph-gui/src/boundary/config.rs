use crate::boundary::config_io;
use crate::commands::{config_helpers as helpers, config_schema, config_storage};
use std::path::PathBuf;

pub use helpers::{
    AgentInfo, AgentProfile, AgentToolInfo, ChainInfo, ConfigFieldSchema, ConfigFieldWithSource,
    ConfigSection, ConfigSource, ConfigView, EffectiveChainsConfig, EffectiveConfigWithSources,
    ToolUpdateInfo,
};

/// List available agent profiles from `agents.toml`.
#[tauri::command]
#[specta::specta]
pub fn list_agent_profiles(repo_path: Option<String>) -> Result<Vec<AgentProfile>, String> {
    let repo_paths = repo_path
        .into_iter()
        .map(|repo| PathBuf::from(repo).join("agents.toml"));
    let home_paths = config_io::home_dir()
        .map(|home: PathBuf| home.join(".ralph").join("agents.toml"))
        .into_iter();
    let content: Option<Result<String, String>> = repo_paths.chain(home_paths).find_map(|path| {
        if config_io::path_exists(&path) {
            Some(config_io::map_io_error(
                config_io::read_to_string(&path),
                "Failed to read agents.toml",
            ))
        } else {
            None
        }
    });

    let toml_content = match content {
        Some(Ok(text)) => text,
        Some(Err(err)) => return Err(err),
        None => return Ok(vec![]),
    };

    Ok(helpers::parse_agent_profiles_from_toml(&toml_content))
}

/// Get the global Ralph configuration.
#[tauri::command]
#[specta::specta]
pub fn get_global_config() -> Result<ConfigView, String> {
    helpers::get_global_config()
}

/// Get the project-level Ralph configuration.
#[tauri::command]
#[specta::specta]
pub fn get_project_config(repo_path: String) -> Result<Option<ConfigView>, String> {
    helpers::get_project_config(repo_path)
}

/// Get the effective configuration (global + project overrides).
#[tauri::command]
#[specta::specta]
pub fn get_effective_config(repo_path: String) -> Result<ConfigView, String> {
    helpers::get_effective_config(repo_path)
}

/// Get the effective config with per-field source tracking.
#[tauri::command]
#[specta::specta]
pub fn get_effective_config_with_sources(
    repo_path: String,
) -> Result<EffectiveConfigWithSources, String> {
    helpers::get_effective_config_with_sources(repo_path)
}

/// Get the effective agent chain and drain configuration.
#[tauri::command]
#[specta::specta]
pub fn get_effective_chains_config(repo_path: String) -> Result<EffectiveChainsConfig, String> {
    helpers::get_effective_chains_config(repo_path)
}

/// Save the global Ralph configuration.
#[tauri::command]
#[specta::specta]
pub fn save_global_config(config_toml: String) -> Result<(), String> {
    config_storage::save_global_config(config_toml)
}

/// Get the raw global config TOML.
#[tauri::command]
#[specta::specta]
pub fn get_raw_global_config_toml() -> Result<String, String> {
    config_storage::get_raw_global_config_toml()
}

/// Get the raw project config TOML.
#[tauri::command]
#[specta::specta]
pub fn get_raw_project_config_toml(repo_path: String) -> Result<String, String> {
    config_storage::get_raw_project_config_toml(repo_path)
}

/// Save the project configuration.
#[tauri::command]
#[specta::specta]
pub fn save_project_config(repo_path: String, config_toml: String) -> Result<(), String> {
    config_storage::save_project_config(repo_path, config_toml)
}

/// Validate a config TOML string.
#[tauri::command]
#[specta::specta]
pub fn validate_config_toml(config_toml: String) -> Result<Option<String>, String> {
    config_storage::validate_config_toml(config_toml)
}

/// Get the GUI config schema sections.
#[tauri::command]
#[specta::specta]
pub fn get_config_schema() -> Result<Vec<ConfigSection>, String> {
    config_schema::get_config_schema()
}

/// Check for updates to agent tools.
#[tauri::command]
#[specta::specta]
pub fn check_tool_updates() -> Result<Vec<ToolUpdateInfo>, String> {
    helpers::check_tool_updates()
}

/// Install an agent tool via the preferred command.
#[tauri::command]
#[specta::specta]
pub fn install_agent_tool(name: String) -> Result<(), String> {
    helpers::install_agent_tool(name)
}

/// Open the CLI settings for an agent tool.
#[tauri::command]
#[specta::specta]
pub fn open_tool_settings(name: String) -> Result<(), String> {
    helpers::open_tool_settings(name)
}

/// Refresh available models for an agent tool.
#[tauri::command]
#[specta::specta]
pub fn refresh_tool_models(name: String) -> Result<Vec<String>, String> {
    helpers::refresh_tool_models(name)
}

/// Probe known agent tool binaries.
#[tauri::command]
#[specta::specta]
pub fn get_agent_tools() -> Result<Vec<AgentToolInfo>, String> {
    helpers::get_agent_tools()
}

/// Test connection to an agent tool.
#[tauri::command]
#[specta::specta]
pub fn test_agent_tool_connection(name: String) -> Result<String, String> {
    helpers::test_agent_tool_connection(name)
}

/// Get stored AI API key.
#[tauri::command]
#[specta::specta]
pub fn get_ai_api_key() -> Result<String, String> {
    config_storage::get_ai_api_key()
}

/// Save the AI API key.
#[tauri::command]
#[specta::specta]
pub fn save_ai_api_key(api_key: String) -> Result<(), String> {
    config_storage::save_ai_api_key(api_key)
}
