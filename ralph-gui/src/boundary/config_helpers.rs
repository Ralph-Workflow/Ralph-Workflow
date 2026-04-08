use super::{config_schema, config_tools};
use crate::boundary::config_storage;
use ralph_workflow::config::unified::UnifiedConfig;
use serde::{Deserialize, Serialize};
use specta::Type;

pub use super::config_chains::{
    get_effective_chains_config, parse_agent_profiles_from_toml, AgentInfo, AgentProfile,
    ChainInfo, EffectiveChainsConfig,
};
pub use super::config_schema::{ConfigFieldSchema, ConfigSection};
pub use super::config_tools::{AgentToolInfo, ToolUpdateInfo};

/// Where a config field's value originates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Type)]
#[serde(rename_all = "lowercase")]
pub enum ConfigSource {
    /// Value comes from compiled-in defaults (not set in any file).
    Default,
    /// Value is explicitly set in the global config file (`~/.config/ralph-workflow.toml`).
    Global,
    /// Value is explicitly set in the project config file (`.agent/ralph-workflow.toml`).
    Project,
}

/// A single config field paired with its provenance source.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ConfigFieldWithSource {
    pub field_name: String,
    pub source: ConfigSource,
}

/// Effective config with per-field source provenance.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct EffectiveConfigWithSources {
    /// The merged effective config values.
    pub config: ConfigView,
    /// Provenance for each field in `config`.
    pub sources: Vec<ConfigFieldWithSource>,
}

/// Serializable representation of the Ralph configuration for the GUI.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Type)]
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
pub fn get_project_config(repo_path: String) -> Result<Option<ConfigView>, String> {
    let project_content = config_storage::get_raw_project_config_toml(repo_path)?;
    if project_content.is_empty() {
        return Ok(None);
    }
    let config = UnifiedConfig::load_from_content(&project_content)
        .map_err(|e| format!("Failed to parse project config: {e}"))?;
    Ok(Some(ConfigView::from(&config)))
}

/// Get the effective configuration (global merged with project overrides).
///
/// # Errors
///
/// Returns an error string if configs cannot be read.
pub fn get_effective_config(repo_path: String) -> Result<ConfigView, String> {
    let global = UnifiedConfig::load_default().unwrap_or_default();

    let project_content = config_storage::get_raw_project_config_toml(repo_path)?;
    if project_content.is_empty() {
        return Ok(ConfigView::from(&global));
    }

    let project_parsed = UnifiedConfig::load_from_content(&project_content)
        .map_err(|e| format!("Failed to parse project config: {e}"))?;
    let merged = global.merge_with_content(&project_content, &project_parsed);
    Ok(ConfigView::from(&merged))
}

/// Get the effective config with per-field source tracking (default / global / project).
///
/// Each field in the returned `sources` vec indicates where the corresponding
/// field value in `config` originates from.
///
/// # Errors
///
/// Returns an error string if configs cannot be read or parsed.
pub fn get_effective_config_with_sources(
    repo_path: String,
) -> Result<EffectiveConfigWithSources, String> {
    let global_toml = config_storage::get_raw_global_config_toml()?;
    let project_toml = config_storage::get_raw_project_config_toml(repo_path)?;
    super::config_parsing::parse_effective_config_with_sources(&global_toml, &project_toml)
}

/// Save the global Ralph configuration.
pub fn save_global_config(config_toml: String) -> Result<(), String> {
    config_storage::save_global_config(config_toml)
}

/// Read the raw global Ralph configuration TOML.
pub fn get_raw_global_config_toml() -> Result<String, String> {
    config_storage::get_raw_global_config_toml()
}

/// Read the raw project-level Ralph configuration TOML.
pub fn get_raw_project_config_toml(repo_path: String) -> Result<String, String> {
    config_storage::get_raw_project_config_toml(repo_path)
}

/// Save the project-level Ralph configuration.
pub fn save_project_config(repo_path: String, config_toml: String) -> Result<(), String> {
    config_storage::save_project_config(repo_path, config_toml)
}

/// Validate raw TOML text against the ralph-workflow schema.
pub fn validate_config_toml(config_toml: String) -> Result<Option<String>, String> {
    config_storage::validate_config_toml(config_toml)
}

/// Return the GUI configuration schema sections.
pub fn get_config_schema() -> Result<Vec<ConfigSection>, String> {
    config_schema::get_config_schema()
}

/// Read the stored AI API key.
pub fn get_ai_api_key() -> Result<String, String> {
    config_storage::get_ai_api_key()
}

/// Save the AI API key.
pub fn save_ai_api_key(api_key: String) -> Result<(), String> {
    config_storage::save_ai_api_key(api_key)
}

/// List available agent profiles from `agents.toml`.
///
/// Looks for the file at `{repo_path}/agents.toml` first, then `~/.ralph/agents.toml`.
/// Returns an empty list if neither file exists.
pub fn list_agent_profiles(repo_path: Option<String>) -> Result<Vec<AgentProfile>, String> {
    let Some(content) = config_storage::get_raw_agents_toml(repo_path)? else {
        return Ok(vec![]);
    };
    toml::from_str::<toml::Value>(&content)
        .map_err(|e| format!("Failed to parse agents.toml: {e}"))?;
    Ok(parse_agent_profiles_from_toml(&content))
}

/// Parse a TOML string-array literal (e.g. `["a", "b"]`) into a `Vec<String>`.
///
/// Returns `None` if the input is not a valid TOML array of strings.
pub fn parse_toml_string_array(s: &str) -> Option<Vec<String>> {
    let wrapped = format!("v = {s}");
    let parsed: toml::Value = toml::from_str(&wrapped).ok()?;
    let arr = parsed.get("v")?.as_array()?;
    arr.iter().map(|v| v.as_str().map(str::to_string)).collect()
}

/// Parse a TOML quoted-string literal (e.g. `"hello"`) into a `String`.
///
/// Returns `None` if the input is not a valid TOML string.
pub fn parse_toml_quoted_string(s: &str) -> Option<String> {
    let wrapped = format!("v = {s}");
    let parsed: toml::Value = toml::from_str(&wrapped).ok()?;
    parsed.get("v")?.as_str().map(str::to_string)
}

/// Check for updates to agent tools.
pub fn check_tool_updates() -> Result<Vec<ToolUpdateInfo>, String> {
    config_tools::check_tool_updates()
}

/// Install an agent tool.
pub fn install_agent_tool(name: String) -> Result<(), String> {
    config_tools::install_agent_tool(name)
}

/// Open agent tool settings.
pub fn open_tool_settings(name: String) -> Result<(), String> {
    config_tools::open_tool_settings(name)
}

/// Refresh available models for an agent tool.
pub fn refresh_tool_models(name: String) -> Result<Vec<String>, String> {
    config_tools::refresh_tool_models(name)
}

/// Get the installed agent tools.
pub fn get_agent_tools() -> Result<Vec<AgentToolInfo>, String> {
    config_tools::get_agent_tools()
}

/// Test connection to an agent tool.
pub fn test_agent_tool_connection(name: String) -> Result<String, String> {
    config_tools::test_agent_tool_connection(name)
}

/// Determine the source for each config field by comparing layer values.
///
/// A field is `Project` when its value in `project_view` differs from `default_view`,
/// `Global` when `global_view` differs from `default_view`, and `Default` otherwise.
/// Production code uses [`build_source_list_from_toml`] which detects TOML presence.
#[cfg(test)]
pub(crate) fn build_source_list(
    default_view: &ConfigView,
    global_view: &ConfigView,
    project_view: Option<&ConfigView>,
) -> Vec<ConfigFieldWithSource> {
    macro_rules! source_for {
        ($field:ident) => {
            ConfigFieldWithSource {
                field_name: stringify!($field).to_string(),
                source: if project_view.is_some_and(|p| p.$field != default_view.$field) {
                    ConfigSource::Project
                } else if global_view.$field != default_view.$field {
                    ConfigSource::Global
                } else {
                    ConfigSource::Default
                },
            }
        };
    }
    vec![
        source_for!(verbosity),
        source_for!(developer_iters),
        source_for!(reviewer_reviews),
        source_for!(checkpoint_enabled),
        source_for!(isolation_mode),
        source_for!(interactive),
        source_for!(review_depth),
        source_for!(max_dev_continuations),
    ]
}

// Items re-exported for unit tests only.
#[cfg(test)]
pub(crate) use super::config_parsing::{
    build_source_list_from_toml, merge_chains, merge_drains, parse_agents_from_toml,
    parse_chains_from_toml, parse_drains_from_toml,
};
#[cfg(test)]
pub(crate) use crate::boundary::config_storage::{load_gui_config, AiConfig, GuiConfig};

/// Determine the source for each config field by comparing layers.
///
/// Priority: Project > Global > Default.
/// A field is considered "set" at a layer when its value differs from the
/// default compiled-in value.
///
/// This version is used by tests; production code uses `build_source_list_from_toml`.
#[cfg(test)]
#[path = "config_helpers/tests.rs"]
mod tests;
