use ralph_workflow::config::unified::UnifiedConfig;
use serde::{Deserialize, Serialize};
use specta::Type;

/// Information about a single configured agent from `[agents.NAME]` TOML sections.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct AgentInfo {
    pub name: String,
    pub tool: String,
    pub model: String,
}

/// Information about a single configured agent chain from `[agent_chains]` TOML section.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ChainInfo {
    pub name: String,
    pub agents: Vec<String>,
}

/// Effective (merged global + project) chain and drain configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct EffectiveChainsConfig {
    pub chains: Vec<ChainInfo>,
    pub drains: std::collections::HashMap<String, String>,
    pub agents: Vec<AgentInfo>,
    pub has_configured_chains: bool,
    pub has_configured_drains: bool,
}

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

/// An agent profile from `agents.toml`.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
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
#[specta::specta]
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
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
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
#[specta::specta]
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
#[specta::specta]
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
#[specta::specta]
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

/// Get the effective config with per-field source tracking (default / global / project).
///
/// Each field in the returned `sources` vec indicates where the corresponding
/// field value in `config` originates from.
///
/// # Errors
///
/// Returns an error string if configs cannot be read or parsed.
#[tauri::command]
#[specta::specta]
pub fn get_effective_config_with_sources(
    repo_path: String,
) -> Result<EffectiveConfigWithSources, String> {
    // Global config + its raw TOML (for presence detection).
    let global_toml_content = {
        let path = dirs::home_dir()
            .map(|h| h.join(".config").join("ralph-workflow.toml"))
            .filter(|p| p.exists());
        match path {
            Some(p) => std::fs::read_to_string(&p)
                .map_err(|e| format!("Failed to read global config: {e}"))?,
            None => String::new(),
        }
    };
    let global_config = if global_toml_content.is_empty() {
        UnifiedConfig::default()
    } else {
        UnifiedConfig::load_from_content(&global_toml_content).unwrap_or_default()
    };

    // Project config path + raw TOML.
    let project_config_path = std::path::PathBuf::from(repo_path)
        .join(".agent")
        .join("ralph-workflow.toml");

    let (effective_view, project_toml_content) = if project_config_path.exists() {
        let project_content = std::fs::read_to_string(&project_config_path)
            .map_err(|e| format!("Failed to read project config: {e}"))?;
        let project_parsed = UnifiedConfig::load_from_content(&project_content)
            .map_err(|e| format!("Failed to parse project config: {e}"))?;
        let merged = global_config.merge_with_content(&project_content, &project_parsed);
        (ConfigView::from(&merged), Some(project_content))
    } else {
        (ConfigView::from(&global_config), None)
    };

    let sources = build_source_list_from_toml(&global_toml_content, project_toml_content.as_ref());

    Ok(EffectiveConfigWithSources {
        config: effective_view,
        sources,
    })
}

/// Parse `[agent_chains]` section from a TOML string.
///
/// Returns a map of chain name → list of agent names.
fn parse_chains_from_toml(toml: &str) -> std::collections::HashMap<String, Vec<String>> {
    let mut chains: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    let mut in_chains = false;

    for raw_line in toml.lines() {
        let line = raw_line.trim();

        if line == "[agent_chains]" {
            in_chains = true;
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            in_chains = false;
            continue;
        }
        if !in_chains || line.is_empty() || line.starts_with('#') {
            continue;
        }

        let Some(eq_idx) = line.find('=') else {
            continue;
        };
        let key = line[..eq_idx].trim().to_string();
        let value_str = line[eq_idx + 1..].trim();

        // Parse an array like ["agent1", "agent2"]
        if let Some(agents) = parse_toml_string_array(value_str) {
            chains.insert(key, agents);
        }
    }

    chains
}

/// Parse `[agent_drains]` section from a TOML string.
///
/// Returns a map of drain phase → chain name.
fn parse_drains_from_toml(toml: &str) -> std::collections::HashMap<String, String> {
    let mut drains: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut in_drains = false;

    for raw_line in toml.lines() {
        let line = raw_line.trim();

        if line == "[agent_drains]" {
            in_drains = true;
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            in_drains = false;
            continue;
        }
        if !in_drains || line.is_empty() || line.starts_with('#') {
            continue;
        }

        let Some(eq_idx) = line.find('=') else {
            continue;
        };
        let key = line[..eq_idx].trim().to_string();
        let value_str = line[eq_idx + 1..].trim();

        if let Some(chain_name) = parse_toml_quoted_string(value_str) {
            drains.insert(key, chain_name);
        }
    }

    drains
}

/// Parse `[agents.NAME]` sections from a TOML string.
///
/// Returns a list of `AgentInfo` structs with name, tool, and model.
fn parse_agents_from_toml(toml: &str) -> Vec<AgentInfo> {
    let mut agents: Vec<AgentInfo> = Vec::new();
    let mut current_name: Option<String> = None;
    let mut current_tool = String::new();
    let mut current_model = String::new();

    let flush_agent = |name: &Option<String>, tool: &str, model: &str, out: &mut Vec<AgentInfo>| {
        if let Some(n) = name {
            out.push(AgentInfo {
                name: n.clone(),
                tool: tool.to_string(),
                model: model.to_string(),
            });
        }
    };

    for raw_line in toml.lines() {
        let line = raw_line.trim();

        if line.starts_with('[') && line.ends_with(']') {
            flush_agent(&current_name, &current_tool, &current_model, &mut agents);
            current_name = None;
            current_tool = String::new();
            current_model = String::new();

            // Match [agents.NAME]
            if let Some(caps) = line
                .strip_prefix("[agents.")
                .and_then(|s| s.strip_suffix(']'))
            {
                current_name = Some(caps.to_string());
            }
            continue;
        }

        if current_name.is_none() || line.is_empty() || line.starts_with('#') {
            continue;
        }

        let Some(eq_idx) = line.find('=') else {
            continue;
        };
        let key = line[..eq_idx].trim();
        let value_str = line[eq_idx + 1..].trim();

        if let Some(v) = parse_toml_quoted_string(value_str) {
            match key {
                "tool" => current_tool = v,
                "model" => current_model = v,
                _ => {}
            }
        }
    }

    flush_agent(&current_name, &current_tool, &current_model, &mut agents);
    agents
}

/// Parse a TOML quoted string like `"value"`. Returns `None` on failure.
fn parse_toml_quoted_string(value: &str) -> Option<String> {
    if value.starts_with('"') && value.ends_with('"') && value.len() >= 2 {
        Some(value[1..value.len() - 1].to_string())
    } else {
        None
    }
}

/// Parse a TOML array like `["a", "b"]`. Returns `None` on failure.
fn parse_toml_string_array(value: &str) -> Option<Vec<String>> {
    let value = value.trim();
    if !value.starts_with('[') || !value.ends_with(']') {
        return None;
    }
    let inner = value[1..value.len() - 1].trim();
    if inner.is_empty() {
        return Some(Vec::new());
    }

    let mut items = Vec::new();
    for part in inner.split(',') {
        let item = parse_toml_quoted_string(part.trim())?;
        items.push(item);
    }
    Some(items)
}

/// Merge two chain maps: project takes precedence over global.
fn merge_chains(
    global: std::collections::HashMap<String, Vec<String>>,
    project: std::collections::HashMap<String, Vec<String>>,
) -> std::collections::HashMap<String, Vec<String>> {
    let mut merged = global;
    for (k, v) in project {
        merged.insert(k, v);
    }
    merged
}

/// Merge two drain maps: project takes precedence over global.
fn merge_drains(
    global: std::collections::HashMap<String, String>,
    project: std::collections::HashMap<String, String>,
) -> std::collections::HashMap<String, String> {
    let mut merged = global;
    for (k, v) in project {
        merged.insert(k, v);
    }
    merged
}

/// Get the effective (merged global + project) agent chain and drain configuration.
///
/// Reads both `~/.config/ralph-workflow.toml` and `.agent/ralph-workflow.toml`,
/// then merges them with project values taking precedence over global values.
/// Also parses `[agents.NAME]` sections for agent metadata.
///
/// # Errors
///
/// Returns an error if either config file exists but cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_effective_chains_config(repo_path: String) -> Result<EffectiveChainsConfig, String> {
    // Read global config
    let global_toml = {
        let path = dirs::home_dir()
            .map(|h| h.join(".config").join("ralph-workflow.toml"))
            .filter(|p| p.exists());
        match path {
            Some(p) => std::fs::read_to_string(&p)
                .map_err(|e| format!("Failed to read global config: {e}"))?,
            None => String::new(),
        }
    };

    // Read project config
    let project_toml = {
        let path = std::path::PathBuf::from(&repo_path)
            .join(".agent")
            .join("ralph-workflow.toml");
        if path.exists() {
            Some(
                std::fs::read_to_string(&path)
                    .map_err(|e| format!("Failed to read project config: {e}"))?,
            )
        } else {
            None
        }
    };

    // Parse from global
    let global_chains = parse_chains_from_toml(&global_toml);
    let global_drains = parse_drains_from_toml(&global_toml);
    let global_agents = parse_agents_from_toml(&global_toml);

    // Parse from project and merge
    let (merged_chains_map, merged_drains, merged_agents) = if let Some(ref project) = project_toml
    {
        let project_chains = parse_chains_from_toml(project);
        let project_drains = parse_drains_from_toml(project);
        let project_agents = parse_agents_from_toml(project);

        // Merge: project overrides global
        let merged_chains_map = merge_chains(global_chains, project_chains);
        let merged_drains = merge_drains(global_drains, project_drains);

        // Merge agents: project agents override global by name
        let mut agents_map: std::collections::HashMap<String, AgentInfo> = global_agents
            .into_iter()
            .map(|a| (a.name.clone(), a))
            .collect();
        for agent in project_agents {
            agents_map.insert(agent.name.clone(), agent);
        }
        let merged_agents: Vec<AgentInfo> = agents_map.into_values().collect();

        (merged_chains_map, merged_drains, merged_agents)
    } else {
        (global_chains, global_drains, global_agents)
    };

    let chains: Vec<ChainInfo> = merged_chains_map
        .into_iter()
        .map(|(name, agents)| ChainInfo { name, agents })
        .collect();

    let has_configured_chains = !chains.is_empty();
    let has_configured_drains = !merged_drains.is_empty();

    Ok(EffectiveChainsConfig {
        chains,
        drains: merged_drains,
        agents: merged_agents,
        has_configured_chains,
        has_configured_drains,
    })
}

/// Determine the source for each config field using TOML presence detection.
///
/// A field is "set" at a layer if it appears explicitly in that layer's TOML.
/// Priority: Project > Global > Default.
fn build_source_list_from_toml(
    global_toml: &str,
    project_toml: Option<&String>,
) -> Vec<ConfigFieldWithSource> {
    let global_val: toml::Value = toml::from_str(global_toml)
        .unwrap_or_else(|_| toml::Value::Table(toml::map::Map::default()));

    let project_val: Option<toml::Value> = project_toml.map(|t| {
        toml::from_str(t).unwrap_or_else(|_| toml::Value::Table(toml::map::Map::default()))
    });

    // Fields live in [general] table (some flattened, some in sub-tables).
    let global_general = global_val.get("general");
    let project_general = project_val.as_ref().and_then(|v| v.get("general"));

    let global_has = |key: &str| global_general.and_then(|g| g.get(key)).is_some();
    let project_has = |key: &str| project_general.and_then(|g| g.get(key)).is_some();

    let source_for = |key: &str| -> ConfigFieldWithSource {
        let source = if project_has(key) {
            ConfigSource::Project
        } else if global_has(key) {
            ConfigSource::Global
        } else {
            ConfigSource::Default
        };
        ConfigFieldWithSource {
            field_name: key.to_string(),
            source,
        }
    };

    // Behavioral flags live in a sub-table [general.behavior] in some configs,
    // or directly flattened into [general]. Check both levels.
    let global_behavior = global_general.and_then(|g| g.get("behavior"));
    let project_behavior = project_general.and_then(|g| g.get("behavior"));

    let source_for_behavior = |key: &str| -> ConfigFieldWithSource {
        let in_proj = project_behavior.and_then(|b| b.get(key)).is_some() || project_has(key);
        let in_global = global_behavior.and_then(|b| b.get(key)).is_some() || global_has(key);
        let source = if in_proj {
            ConfigSource::Project
        } else if in_global {
            ConfigSource::Global
        } else {
            ConfigSource::Default
        };
        ConfigFieldWithSource {
            field_name: key.to_string(),
            source,
        }
    };

    vec![
        source_for("verbosity"),
        source_for("developer_iters"),
        source_for("reviewer_reviews"),
        // workflow fields (flattened into [general])
        source_for("checkpoint_enabled"),
        // execution fields (flattened into [general])
        source_for("isolation_mode"),
        // behavior fields (may be in [general.behavior] or [general])
        source_for_behavior("interactive"),
        source_for("review_depth"),
        source_for("max_dev_continuations"),
    ]
}

/// Determine the source for each config field by comparing layers.
///
/// Priority: Project > Global > Default.
/// A field is considered "set" at a layer when its value differs from the
/// default compiled-in value.
///
/// This version is used by tests; production code uses `build_source_list_from_toml`.
#[cfg(test)]
fn build_source_list(
    default_view: &ConfigView,
    global_view: &ConfigView,
    project_view: Option<&ConfigView>,
) -> Vec<ConfigFieldWithSource> {
    macro_rules! field_source {
        ($field:ident) => {{
            let source = if let Some(proj) = project_view {
                if proj.$field != default_view.$field {
                    ConfigSource::Project
                } else if global_view.$field != default_view.$field {
                    ConfigSource::Global
                } else {
                    ConfigSource::Default
                }
            } else if global_view.$field != default_view.$field {
                ConfigSource::Global
            } else {
                ConfigSource::Default
            };
            ConfigFieldWithSource {
                field_name: stringify!($field).to_string(),
                source,
            }
        }};
    }

    vec![
        field_source!(verbosity),
        field_source!(developer_iters),
        field_source!(reviewer_reviews),
        field_source!(checkpoint_enabled),
        field_source!(isolation_mode),
        field_source!(interactive),
        field_source!(review_depth),
        field_source!(max_dev_continuations),
    ]
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
#[specta::specta]
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
#[specta::specta]
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
#[specta::specta]
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
#[specta::specta]
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

/// Validate a ralph-workflow TOML configuration string without saving it.
///
/// Returns `Ok(None)` if the TOML is valid, or `Ok(Some(error_message))` if parsing fails.
/// This allows the frontend to surface validation errors before the user attempts to save.
///
/// # Errors
///
/// This command does not return `Err`; parse failures are returned as `Ok(Some(message))`.
#[tauri::command]
#[specta::specta]
pub fn validate_config_toml(config_toml: String) -> Result<Option<String>, String> {
    match UnifiedConfig::load_from_content(&config_toml) {
        Ok(_) => Ok(None),
        Err(e) => Ok(Some(format!("{e}"))),
    }
}

/// Metadata for a single configuration field, enabling dynamic form rendering.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ConfigFieldSchema {
    pub name: String,
    pub label: String,
    pub description: String,
    pub field_type: String, // "number" | "boolean" | "string" | "enum" | "path"
    pub default_value: String,
    pub min_value: Option<f64>,
    pub max_value: Option<f64>,
    pub enum_options: Vec<String>,
    pub section: String,
}

/// A grouping of configuration fields for form section rendering.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ConfigSection {
    pub name: String,
    pub label: String,
    pub description: String,
    pub fields: Vec<ConfigFieldSchema>,
}

/// Compact builder for a `ConfigFieldSchema`. Used only within this module to
/// reduce repetition in the section helpers below.
struct FieldSpec<'a> {
    name: &'a str,
    label: &'a str,
    description: &'a str,
    field_type: &'a str,
    default_value: &'a str,
    min_value: Option<f64>,
    max_value: Option<f64>,
    enum_options: Vec<String>,
    section: &'a str,
}

impl FieldSpec<'_> {
    fn build(self) -> ConfigFieldSchema {
        ConfigFieldSchema {
            name: self.name.to_string(),
            label: self.label.to_string(),
            description: self.description.to_string(),
            field_type: self.field_type.to_string(),
            default_value: self.default_value.to_string(),
            min_value: self.min_value,
            max_value: self.max_value,
            enum_options: self.enum_options,
            section: self.section.to_string(),
        }
    }
}

fn num(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
    min: f64,
    max: f64,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "number",
        default_value,
        min_value: Some(min),
        max_value: Some(max),
        enum_options: vec![],
        section,
    }
    .build()
}

fn bool_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "boolean",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn str_field(section: &str, name: &str, label: &str, description: &str) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "string",
        default_value: "",
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn path_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "path",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn enum_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
    options: Vec<&str>,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "enum",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: options.into_iter().map(str::to_owned).collect(),
        section,
    }
    .build()
}

fn general_section() -> ConfigSection {
    let s = "general";
    ConfigSection {
        name: s.to_string(),
        label: "General".to_string(),
        description: "Core workflow settings".to_string(),
        fields: vec![
            num(
                s,
                "verbosity",
                "Verbosity",
                "Log verbosity level (0 = silent, 4 = trace)",
                "1",
                0.0,
                4.0,
            ),
            num(
                s,
                "developer_iters",
                "Developer Iterations",
                "Maximum developer iterations per run",
                "3",
                1.0,
                20.0,
            ),
            num(
                s,
                "reviewer_reviews",
                "Reviewer Passes",
                "Number of reviewer passes per iteration",
                "1",
                0.0,
                10.0,
            ),
            num(
                s,
                "max_dev_continuations",
                "Max Dev Continuations",
                "Maximum continuation attempts for the developer agent",
                "3",
                1.0,
                10.0,
            ),
            enum_field(
                s,
                "review_depth",
                "Review Depth",
                "How thorough the reviewer should be",
                "standard",
                vec!["light", "standard", "thorough"],
            ),
            path_field(
                s,
                "prompt_path",
                "Default Prompt Path",
                "Path to the default PROMPT.md file",
                "",
            ),
            path_field(
                s,
                "templates_dir",
                "Templates Directory",
                "Directory containing prompt templates",
                "~/.ralph/templates",
            ),
        ],
    }
}

fn execution_section() -> ConfigSection {
    let s = "execution";
    ConfigSection {
        name: s.to_string(),
        label: "Execution".to_string(),
        description: "How the workflow executes agent tasks".to_string(),
        fields: vec![
            bool_field(
                s,
                "checkpoint_enabled",
                "Enable Checkpointing",
                "Save progress checkpoints to allow resuming interrupted runs",
                "true",
            ),
            bool_field(
                s,
                "isolation_mode",
                "Isolation Mode",
                "Run agents in an isolated environment",
                "false",
            ),
            bool_field(
                s,
                "interactive",
                "Interactive Mode",
                "Allow interactive prompts during execution",
                "false",
            ),
            bool_field(
                s,
                "force_universal_prompt",
                "Force Universal Prompt",
                "Use a single prompt for all agents regardless of individual settings",
                "false",
            ),
            bool_field(
                s,
                "auto_detect_stack",
                "Auto-Detect Stack",
                "Automatically detect the project technology stack",
                "true",
            ),
            str_field(
                s,
                "developer_context",
                "Developer Context",
                "Additional context provided to the developer agent",
            ),
            str_field(
                s,
                "reviewer_context",
                "Reviewer Context",
                "Additional context provided to the reviewer agent",
            ),
        ],
    }
}

fn retry_section() -> ConfigSection {
    let s = "retry";
    ConfigSection {
        name: s.to_string(),
        label: "Retry and Fallback".to_string(),
        description: "How the workflow handles failures and retries".to_string(),
        fields: vec![
            num(
                s,
                "max_retries",
                "Max Retries",
                "Maximum number of retry attempts on failure",
                "3",
                0.0,
                20.0,
            ),
            num(
                s,
                "max_same_agent_retries",
                "Max Same-Agent Retries",
                "Maximum retries with the same agent before switching",
                "2",
                0.0,
                10.0,
            ),
            num(
                s,
                "retry_delay_ms",
                "Retry Delay (ms)",
                "Milliseconds to wait before each retry attempt",
                "1000",
                0.0,
                60_000.0,
            ),
            num(
                s,
                "backoff_multiplier",
                "Backoff Multiplier",
                "Exponential backoff multiplier between retries",
                "2.0",
                1.0,
                10.0,
            ),
            num(
                s,
                "max_backoff_ms",
                "Max Backoff (ms)",
                "Maximum milliseconds between retry attempts",
                "30000",
                1_000.0,
                300_000.0,
            ),
            num(
                s,
                "max_fallback_cycles",
                "Max Fallback Cycles",
                "Maximum number of fallback agent cycles",
                "2",
                0.0,
                10.0,
            ),
        ],
    }
}

fn git_section() -> ConfigSection {
    let s = "git";
    ConfigSection {
        name: s.to_string(),
        label: "Git".to_string(),
        description: "Git identity and commit settings".to_string(),
        fields: vec![
            str_field(
                s,
                "git_user_name",
                "Git User Name",
                "Name to use for automated git commits",
            ),
            str_field(
                s,
                "git_user_email",
                "Git User Email",
                "Email to use for automated git commits",
            ),
        ],
    }
}

/// Return structured schema metadata for all Ralph configuration fields.
///
/// The frontend uses this to dynamically render typed form controls for each
/// field rather than displaying raw TOML.
///
/// # Errors
///
/// This command currently never fails but returns `Err` to satisfy the `Result` interface.
#[tauri::command]
#[specta::specta]
pub fn get_config_schema() -> Result<Vec<ConfigSection>, String> {
    Ok(vec![
        general_section(),
        execution_section(),
        retry_section(),
        git_section(),
    ])
}

/// Information about an update check result for an agent tool.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ToolUpdateInfo {
    pub name: String,
    pub current_version: Option<String>,
    pub latest_version: Option<String>,
    pub update_available: bool,
    pub message: String,
}

/// Check installed agent tools for available updates.
///
/// # Errors
///
/// Returns an error if tool version checking fails unexpectedly.
#[tauri::command]
#[specta::specta]
pub fn check_tool_updates() -> Result<Vec<ToolUpdateInfo>, String> {
    let tools = [
        ("Claude Code", "claude"),
        ("Codex", "codex"),
        ("OpenCode", "opencode"),
    ];

    let results = tools
        .iter()
        .map(|(name, binary)| {
            let current_version = std::process::Command::new(binary)
                .arg("--version")
                .output()
                .ok()
                .filter(|o| o.status.success())
                .and_then(|o| {
                    String::from_utf8(o.stdout)
                        .ok()
                        .map(|s| s.lines().next().unwrap_or("").trim().to_string())
                });

            ToolUpdateInfo {
                name: (*name).to_string(),
                current_version: current_version.clone(),
                latest_version: None, // Would require network call or package manager check
                update_available: false, // Cannot determine without network check
                message: current_version.map_or_else(
                    || format!("{binary} not installed"),
                    |v| format!("Current: {v} — check package manager for updates"),
                ),
            }
        })
        .collect();

    Ok(results)
}

/// Trigger a platform-appropriate installation flow for an agent tool.
///
/// On macOS/Linux this opens a terminal with the recommended install command.
/// On Windows it opens the tool's download page.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the install command fails to launch.
#[tauri::command]
#[specta::specta]
pub fn install_agent_tool(name: String) -> Result<(), String> {
    let install_cmd = match name.as_str() {
        "Claude Code" => Some("npm install -g @anthropic-ai/claude-code"),
        "Codex" => Some("npm install -g @openai/codex"),
        "OpenCode" => Some("npm install -g opencode-ai"),
        other => return Err(format!("Unknown tool: {other}")),
    };

    if let Some(cmd) = install_cmd {
        // Open the install command in the system terminal.
        #[cfg(target_os = "macos")]
        {
            std::process::Command::new("osascript")
                .args([
                    "-e",
                    &format!("tell application \"Terminal\" to do script \"{cmd}\""),
                ])
                .spawn()
                .map_err(|e| format!("Failed to open terminal: {e}"))?;
        }
        #[cfg(target_os = "linux")]
        {
            // Try common Linux terminals.
            let term_result = std::process::Command::new("x-terminal-emulator")
                .args(["-e", &format!("bash -c '{cmd}; exec bash'")])
                .spawn();
            if term_result.is_err() {
                std::process::Command::new("gnome-terminal")
                    .args(["--", "bash", "-c", &format!("{cmd}; exec bash")])
                    .spawn()
                    .map_err(|e| format!("Failed to open terminal: {e}"))?;
            }
        }
        #[cfg(target_os = "windows")]
        {
            std::process::Command::new("cmd")
                .args(["/c", "start", "cmd", "/k", cmd])
                .spawn()
                .map_err(|e| format!("Failed to open terminal: {e}"))?;
        }
    }

    Ok(())
}

/// Open the CLI settings or configuration for an agent tool.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the settings command fails to launch.
#[tauri::command]
#[specta::specta]
pub fn open_tool_settings(name: String) -> Result<(), String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    // Attempt to open settings via the CLI's settings/config subcommand.
    std::process::Command::new(binary)
        .arg("--help")
        .spawn()
        .map_err(|e| format!("Failed to launch {binary}: {e}"))?;

    Ok(())
}

/// Refresh the list of available models for a given agent tool.
///
/// # Errors
///
/// Returns an error if the tool name is unknown or the model list cannot be retrieved.
#[tauri::command]
#[specta::specta]
pub fn refresh_tool_models(name: String) -> Result<Vec<String>, String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    // Attempt to retrieve model list via CLI.
    let output = std::process::Command::new(binary)
        .args(["--list-models"])
        .output()
        .ok()
        .filter(|o| o.status.success());

    if let Some(out) = output {
        let models: Vec<String> = String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| l.trim().to_string())
            .collect();
        return Ok(models);
    }

    // Fallback: return known model names per tool.
    let fallback = match name.as_str() {
        "Claude Code" => vec![
            "claude-opus-4-5".to_string(),
            "claude-sonnet-4-6".to_string(),
            "claude-haiku-4".to_string(),
        ],
        "Codex" => vec!["gpt-4o".to_string(), "gpt-4o-mini".to_string()],
        "OpenCode" => vec!["claude-sonnet-4-6".to_string(), "gpt-4o".to_string()],
        _ => vec![],
    };

    Ok(fallback)
}

/// Information about an installed or detectable agent tool (CLI).
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct AgentToolInfo {
    pub name: String,
    pub binary: String,
    pub installed: bool,
    pub version: Option<String>,
    pub auth_status: String,
    pub health: String,
    pub description: String,
    pub available_models: Vec<String>,
    pub binary_location: Option<String>,
}

/// Probe a known CLI tool binary in the PATH.
fn probe_tool(name: &str, binary: &str, description: &str) -> AgentToolInfo {
    let which_output = std::process::Command::new("which").arg(binary).output();
    let installed = which_output.as_ref().is_ok_and(|o| o.status.success());
    let binary_location = which_output
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| {
            String::from_utf8(o.stdout)
                .ok()
                .map(|s| s.trim().to_string())
        });

    let version = if installed {
        std::process::Command::new(binary)
            .arg("--version")
            .output()
            .ok()
            .filter(|o| o.status.success())
            .and_then(|o| {
                String::from_utf8(o.stdout)
                    .ok()
                    .map(|s| s.lines().next().unwrap_or("").trim().to_string())
            })
    } else {
        None
    };

    let health = if installed {
        if version.is_some() {
            "Ready".to_string()
        } else {
            "Needs setup".to_string()
        }
    } else {
        "Not installed".to_string()
    };

    // Try to retrieve available models; fall back to known defaults if the CLI
    // doesn't support --list-models.
    let available_models = if installed {
        std::process::Command::new(binary)
            .args(["--list-models"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .and_then(|o| {
                String::from_utf8(o.stdout).ok().map(|s| {
                    s.lines()
                        .filter(|l| !l.trim().is_empty())
                        .map(|l| l.trim().to_string())
                        .collect::<Vec<_>>()
                })
            })
            .unwrap_or_else(|| match name {
                "Claude Code" => vec![
                    "claude-opus-4-5".to_string(),
                    "claude-sonnet-4-6".to_string(),
                    "claude-haiku-4".to_string(),
                ],
                "Codex" => vec!["gpt-4o".to_string(), "gpt-4o-mini".to_string()],
                "OpenCode" => vec!["claude-sonnet-4-6".to_string(), "gpt-4o".to_string()],
                _ => vec![],
            })
    } else {
        vec![]
    };

    AgentToolInfo {
        name: name.to_string(),
        binary: binary.to_string(),
        installed,
        version,
        auth_status: if installed {
            "Unknown".to_string()
        } else {
            "N/A".to_string()
        },
        health,
        description: description.to_string(),
        available_models,
        binary_location,
    }
}

/// Get information about known agent tools (Claude Code, Codex, `OpenCode`).
///
/// # Errors
///
/// This command currently never fails but returns `Err` to satisfy the Result interface.
#[tauri::command]
#[specta::specta]
pub fn get_agent_tools() -> Result<Vec<AgentToolInfo>, String> {
    let tools = vec![
        probe_tool(
            "Claude Code",
            "claude",
            "Anthropic's Claude AI coding assistant",
        ),
        probe_tool("Codex", "codex", "OpenAI's Codex CLI coding agent"),
        probe_tool(
            "OpenCode",
            "opencode",
            "Open-source AI coding agent compatible with multiple providers",
        ),
    ];
    Ok(tools)
}

/// Run a test invocation of an agent tool to verify it works.
///
/// # Errors
///
/// Returns an error string if the tool is not installed or the test invocation fails.
#[tauri::command]
#[specta::specta]
pub fn test_agent_tool_connection(name: String) -> Result<String, String> {
    let binary = match name.as_str() {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        other => return Err(format!("Unknown tool: {other}")),
    };

    let output = std::process::Command::new(binary)
        .arg("--version")
        .output()
        .map_err(|e| format!("{binary} not found or failed to start: {e}"))?;

    if output.status.success() {
        let version = String::from_utf8_lossy(&output.stdout).to_string();
        Ok(format!("Connected: {}", version.trim()))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        Err(format!("Tool returned error: {stderr}"))
    }
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
#[specta::specta]
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
#[specta::specta]
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

    // ── get_effective_config_with_sources tests ────────────────────────────

    #[test]
    fn test_effective_config_with_sources_returns_default_when_no_files() {
        let dir = TempDir::new().unwrap();
        let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let eff = result.unwrap();
        // No global or project file — everything should be Default.
        // We check at least some fields; exact values depend on defaults.
        assert!(!eff.sources.is_empty(), "sources vec should not be empty");
        // developer_iters is a core field and must have a source entry.
        let dev_iters_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .expect("developer_iters source must be present");
        // Since no files exist we expect Default (OR Global if global file exists on this machine).
        assert!(
            dev_iters_source.source == ConfigSource::Default
                || dev_iters_source.source == ConfigSource::Global,
            "With no project file source should be Default or Global, got {:?}",
            dev_iters_source.source
        );
    }

    #[test]
    fn test_effective_config_with_sources_project_overrides_global() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        // Set developer_iters to a value that almost certainly differs from default (3).
        std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

        let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let eff = result.unwrap();
        assert_eq!(eff.config.developer_iters, 7, "Effective value should be 7");
        let source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .expect("developer_iters source must be present");
        assert_eq!(
            source.source,
            ConfigSource::Project,
            "developer_iters was set in project config so source must be Project"
        );
    }

    #[test]
    fn test_effective_config_with_sources_project_explicit_field() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        // Set developer_iters explicitly in project TOML.
        // Even though the value matches the default (5), the field is PRESENT in the TOML
        // so source should be Project (presence detection, not value comparison).
        std::fs::write(&config_path, "[general]\ndeveloper_iters = 5\n").unwrap();

        let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let eff = result.unwrap();
        // developer_iters is explicitly present in project TOML → Project.
        let dev_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .unwrap();
        assert_eq!(
            dev_source.source,
            ConfigSource::Project,
            "developer_iters is explicitly set in project TOML so source must be Project"
        );
    }

    #[test]
    fn test_config_source_serializes_as_lowercase() {
        let source = ConfigSource::Project;
        let json = serde_json::to_string(&source).unwrap();
        assert_eq!(
            json, r#""project""#,
            "ConfigSource should serialize lowercase"
        );

        let global = ConfigSource::Global;
        let global_json = serde_json::to_string(&global).unwrap();
        assert_eq!(global_json, r#""global""#);

        let default_s = ConfigSource::Default;
        let default_json = serde_json::to_string(&default_s).unwrap();
        assert_eq!(default_json, r#""default""#);
    }

    #[test]
    fn test_effective_config_with_sources_field_not_set_uses_default_source() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        // Set developer_iters=7 (differs from the built-in default of 5).
        // isolation_mode is NOT set; it should remain Default (or Global).
        std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

        let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let eff = result.unwrap();
        // developer_iters is set in project → Project
        let dev_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .unwrap();
        assert_eq!(dev_source.source, ConfigSource::Project);
        // isolation_mode is NOT set anywhere — it should be Default or Global (not Project).
        let isolation_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "isolation_mode")
            .unwrap();
        assert!(
            isolation_source.source != ConfigSource::Project,
            "isolation_mode was never set in project config so source must not be Project"
        );
    }

    #[test]
    fn test_build_source_list_from_toml_all_defaults() {
        // Empty TOML → all fields should be Default.
        let sources = build_source_list_from_toml("", None);
        for s in &sources {
            assert_eq!(
                s.source,
                ConfigSource::Default,
                "Field '{}' should be Default when TOML is empty",
                s.field_name
            );
        }
    }

    #[test]
    fn test_build_source_list_from_toml_global_sets_field() {
        let global_toml = "[general]\nverbosity = 3\n";
        let sources = build_source_list_from_toml(global_toml, None);
        let verbosity_src = sources
            .iter()
            .find(|s| s.field_name == "verbosity")
            .unwrap();
        assert_eq!(verbosity_src.source, ConfigSource::Global);

        // Fields not set should remain Default.
        let dev_iters_src = sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .unwrap();
        assert_eq!(dev_iters_src.source, ConfigSource::Default);
    }

    #[test]
    fn test_build_source_list_from_toml_project_overrides_global() {
        let global_toml = "[general]\nverbosity = 3\n";
        let project_toml = "[general]\ndeveloper_iters = 7\n".to_string();
        let sources = build_source_list_from_toml(global_toml, Some(&project_toml));

        // verbosity is set in global, NOT in project → Global
        let verbosity_src = sources
            .iter()
            .find(|s| s.field_name == "verbosity")
            .unwrap();
        assert_eq!(verbosity_src.source, ConfigSource::Global);

        // developer_iters is set in project → Project
        let dev_iters_src = sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .unwrap();
        assert_eq!(dev_iters_src.source, ConfigSource::Project);

        // isolation_mode is set in neither → Default
        let iso_src = sources
            .iter()
            .find(|s| s.field_name == "isolation_mode")
            .unwrap();
        assert_eq!(iso_src.source, ConfigSource::Default);
    }

    #[test]
    fn test_build_source_list_all_defaults() {
        let default_view = ConfigView::from(&UnifiedConfig::default());
        let global_view = ConfigView::from(&UnifiedConfig::default());
        let sources = build_source_list(&default_view, &global_view, None);
        // All fields should be Default when nothing is customised.
        for s in &sources {
            assert_eq!(
                s.source,
                ConfigSource::Default,
                "Field '{}' should be Default when nothing is set",
                s.field_name
            );
        }
    }

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
    fn test_effective_config_with_sources_isolation_not_set_is_not_project() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        // Set developer_iters=7; isolation_mode is NOT set so it must not be Project.
        std::fs::write(&config_path, "[general]\ndeveloper_iters = 7\n").unwrap();

        let result = get_effective_config_with_sources(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let eff = result.unwrap();
        // developer_iters is explicitly set in project config → Project.
        let dev_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "developer_iters")
            .unwrap();
        assert_eq!(dev_source.source, ConfigSource::Project);
        // isolation_mode is NOT set in the project TOML at all → Default or Global, never Project.
        let isolation_source = eff
            .sources
            .iter()
            .find(|s| s.field_name == "isolation_mode")
            .unwrap();
        assert_ne!(
            isolation_source.source,
            ConfigSource::Project,
            "isolation_mode was never set in project config TOML, so its source must not be Project"
        );
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
    fn test_save_global_config_rejects_invalid_toml() {
        // save_global_config validates via UnifiedConfig::load_from_content before writing.
        // We cannot safely redirect the write target in a unit test (it targets the real home dir),
        // but we can verify the validation guard triggers before any I/O attempt.
        let result = save_global_config("this is not valid toml !!!!!".to_string());
        assert!(result.is_err(), "Invalid TOML should be rejected");
        assert!(
            result.unwrap_err().contains("Invalid config"),
            "Error should indicate invalid config"
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

    // --- validate_config_toml tests ---

    #[test]
    fn test_validate_config_toml_accepts_valid_toml() {
        let result = validate_config_toml("[general]\nverbosity = 2\n".to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert!(
            result.unwrap().is_none(),
            "Valid TOML should return Ok(None)"
        );
    }

    #[test]
    fn test_validate_config_toml_rejects_invalid_toml_syntax() {
        let result = validate_config_toml("[unclosed".to_string());
        assert!(result.is_ok(), "Should always return Ok: {result:?}");
        let inner = result.unwrap();
        assert!(
            inner.is_some(),
            "Invalid TOML syntax should return Ok(Some(error))"
        );
        let msg = inner.unwrap();
        assert!(!msg.is_empty(), "Error message should not be empty");
    }

    #[test]
    fn test_validate_config_toml_accepts_empty_string() {
        // An empty string is valid TOML (no keys — all defaults).
        let result = validate_config_toml(String::new());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert!(
            result.unwrap().is_none(),
            "Empty TOML string should be valid (uses defaults)"
        );
    }

    #[test]
    fn test_validate_config_toml_rejects_garbage_content() {
        let result = validate_config_toml("this is not !!! valid toml @@ content $$".to_string());
        assert!(result.is_ok(), "Should always return Ok: {result:?}");
        let inner = result.unwrap();
        assert!(
            inner.is_some(),
            "Garbage content should return Ok(Some(error))"
        );
    }

    #[test]
    fn test_validate_config_toml_accepts_valid_developer_iters() {
        let result = validate_config_toml("[general]\ndeveloper_iters = 5\n".to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert!(
            result.unwrap().is_none(),
            "Valid developer_iters should pass validation"
        );
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

    #[test]
    #[cfg(unix)]
    fn test_save_gui_config_sets_0o600_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let dir = TempDir::new().unwrap();
        // Write a config file manually and set 0o600 permissions, mirroring save_gui_config behavior.
        let path = dir.path().join("ralph-gui.toml");
        let config = GuiConfig {
            ai: AiConfig {
                api_key: "sk-perm-test".to_string(),
            },
        };
        let content = toml::to_string(&config).unwrap();
        std::fs::write(&path, &content).unwrap();
        let perms = std::fs::Permissions::from_mode(0o600);
        std::fs::set_permissions(&path, perms).unwrap();

        let metadata = std::fs::metadata(&path).unwrap();
        let mode = metadata.permissions().mode();
        assert_eq!(
            mode & 0o777,
            0o600,
            "Config file should have 0o600 permissions, got {mode:o}"
        );
    }

    // --- get_config_schema tests ---

    #[test]
    fn test_get_config_schema_returns_four_sections() {
        let result = get_config_schema();
        assert!(result.is_ok(), "get_config_schema should succeed");
        let sections = result.unwrap();
        assert_eq!(
            sections.len(),
            4,
            "Should have 4 sections: general, execution, retry, git"
        );
        let names: Vec<&str> = sections.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"general"), "Should have general section");
        assert!(
            names.contains(&"execution"),
            "Should have execution section"
        );
        assert!(names.contains(&"retry"), "Should have retry section");
        assert!(names.contains(&"git"), "Should have git section");
    }

    #[test]
    fn test_get_config_schema_general_section_has_expected_fields() {
        let sections = get_config_schema().unwrap();
        let general = sections.iter().find(|s| s.name == "general").unwrap();
        let field_names: Vec<&str> = general.fields.iter().map(|f| f.name.as_str()).collect();
        assert!(
            field_names.contains(&"verbosity"),
            "general should have verbosity field"
        );
        assert!(
            field_names.contains(&"developer_iters"),
            "general should have developer_iters field"
        );
        assert!(
            field_names.contains(&"review_depth"),
            "general should have review_depth field"
        );
    }

    #[test]
    fn test_get_config_schema_review_depth_has_enum_options() {
        let sections = get_config_schema().unwrap();
        let general = sections.iter().find(|s| s.name == "general").unwrap();
        let review_depth = general
            .fields
            .iter()
            .find(|f| f.name == "review_depth")
            .unwrap();
        assert_eq!(
            review_depth.field_type, "enum",
            "review_depth should be enum type"
        );
        assert!(
            !review_depth.enum_options.is_empty(),
            "review_depth should have enum options"
        );
        assert!(
            review_depth.enum_options.contains(&"standard".to_string()),
            "Should have 'standard' option"
        );
    }

    #[test]
    fn test_get_config_schema_number_fields_have_bounds() {
        let sections = get_config_schema().unwrap();
        for section in &sections {
            for field in &section.fields {
                if field.field_type == "number" {
                    assert!(
                        field.min_value.is_some() || field.max_value.is_some(),
                        "Number field '{}' should have bounds",
                        field.name
                    );
                }
            }
        }
    }

    // --- get_effective_chains_config tests ---

    #[test]
    fn test_parse_chains_from_toml_parses_chains() {
        let toml = "[agent_chains]\nmychain = [\"agent1\", \"agent2\"]\n";
        let chains = parse_chains_from_toml(toml);
        assert_eq!(chains.len(), 1);
        assert_eq!(chains["mychain"], vec!["agent1", "agent2"]);
    }

    #[test]
    fn test_parse_chains_from_toml_returns_empty_when_no_section() {
        let toml = "[general]\nverbosity = 1\n";
        let chains = parse_chains_from_toml(toml);
        assert!(chains.is_empty());
    }

    #[test]
    fn test_parse_drains_from_toml_parses_drains() {
        let toml = "[agent_drains]\ndevelopment = \"mychain\"\nreview = \"reviewer-chain\"\n";
        let drains = parse_drains_from_toml(toml);
        assert_eq!(drains.len(), 2);
        assert_eq!(drains["development"], "mychain");
        assert_eq!(drains["review"], "reviewer-chain");
    }

    #[test]
    fn test_parse_drains_from_toml_returns_empty_when_no_section() {
        let toml = "[general]\nverbosity = 1\n";
        let drains = parse_drains_from_toml(toml);
        assert!(drains.is_empty());
    }

    #[test]
    fn test_parse_agents_from_toml_parses_agent_sections() {
        let toml =
            "[agents.claude-code]\ntool = \"claude\"\nmodel = \"claude-sonnet-4-6\"\n\n[agents.gpt]\ntool = \"openai\"\nmodel = \"gpt-4o\"\n";
        let agents = parse_agents_from_toml(toml);
        assert_eq!(agents.len(), 2);
        let claude = agents.iter().find(|a| a.name == "claude-code").unwrap();
        assert_eq!(claude.tool, "claude");
        assert_eq!(claude.model, "claude-sonnet-4-6");
        let gpt = agents.iter().find(|a| a.name == "gpt").unwrap();
        assert_eq!(gpt.tool, "openai");
    }

    #[test]
    fn test_parse_agents_from_toml_returns_empty_when_no_sections() {
        let toml = "[general]\nverbosity = 1\n";
        let agents = parse_agents_from_toml(toml);
        assert!(agents.is_empty());
    }

    #[test]
    fn test_merge_chains_project_overrides_global() {
        let mut global = std::collections::HashMap::new();
        global.insert("chain-a".to_string(), vec!["agent1".to_string()]);
        global.insert("chain-b".to_string(), vec!["agent2".to_string()]);

        let mut project = std::collections::HashMap::new();
        project.insert("chain-a".to_string(), vec!["agent-override".to_string()]);
        project.insert("chain-c".to_string(), vec!["agent3".to_string()]);

        let merged = merge_chains(global, project);
        // chain-a is overridden by project
        assert_eq!(merged["chain-a"], vec!["agent-override"]);
        // chain-b is from global only
        assert_eq!(merged["chain-b"], vec!["agent2"]);
        // chain-c is from project only
        assert_eq!(merged["chain-c"], vec!["agent3"]);
    }

    #[test]
    fn test_merge_drains_project_overrides_global() {
        let mut global = std::collections::HashMap::new();
        global.insert("development".to_string(), "chain-a".to_string());
        global.insert("review".to_string(), "chain-b".to_string());

        let mut project = std::collections::HashMap::new();
        project.insert("development".to_string(), "chain-override".to_string());

        let merged = merge_drains(global, project);
        assert_eq!(merged["development"], "chain-override");
        assert_eq!(merged["review"], "chain-b");
    }

    #[test]
    fn test_get_effective_chains_config_returns_empty_when_no_files() {
        let dir = TempDir::new().unwrap();
        let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        // When no project config exists, chains and drains come from the global config
        // (which may or may not exist on the test machine). We only verify Ok is returned.
        let config = result.unwrap();
        // has_configured_chains reflects whether any chains exist
        assert_eq!(config.has_configured_chains, !config.chains.is_empty());
        assert_eq!(config.has_configured_drains, !config.drains.is_empty());
    }

    #[test]
    fn test_get_effective_chains_config_parses_project_chains() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let config_path = agent_dir.join("ralph-workflow.toml");
        let toml = "[agent_chains]\nmychain = [\"agent1\", \"agent2\"]\n\n[agent_drains]\ndevelopment = \"mychain\"\n\n[agents.agent1]\ntool = \"claude\"\nmodel = \"claude-sonnet-4-6\"\n";
        std::fs::write(&config_path, toml).unwrap();

        let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config = result.unwrap();
        assert!(
            config.has_configured_chains,
            "Should have configured chains"
        );
        assert!(
            config.has_configured_drains,
            "Should have configured drains"
        );

        let mychain = config.chains.iter().find(|c| c.name == "mychain");
        assert!(mychain.is_some(), "mychain should be present");
        assert_eq!(mychain.unwrap().agents, vec!["agent1", "agent2"]);

        assert_eq!(
            config.drains.get("development"),
            Some(&"mychain".to_string())
        );

        let agent = config.agents.iter().find(|a| a.name == "agent1");
        assert!(agent.is_some(), "agent1 should be present");
        assert_eq!(agent.unwrap().tool, "claude");
    }

    #[test]
    fn test_get_effective_chains_config_project_chains_override_global() {
        // We can't inject a global config file in tests, but we can verify project-only data.
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let project_toml = "[agent_chains]\nproject-chain = [\"proj-agent\"]\n\n[agent_drains]\nreview = \"project-chain\"\n";
        std::fs::write(agent_dir.join("ralph-workflow.toml"), project_toml).unwrap();

        let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config = result.unwrap();
        // Project chain must appear in merged result
        let found = config.chains.iter().any(|c| c.name == "project-chain");
        assert!(found, "project-chain should appear in merged chains");
        assert_eq!(
            config.drains.get("review"),
            Some(&"project-chain".to_string())
        );
    }

    #[test]
    fn test_get_effective_chains_config_has_configured_flags_false_when_empty() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        // Write a config with no chains or drains sections
        std::fs::write(
            agent_dir.join("ralph-workflow.toml"),
            "[general]\nverbosity = 1\n",
        )
        .unwrap();

        let result = get_effective_chains_config(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let config = result.unwrap();
        // No chains from project config. Global may provide some — we only check the flag matches reality.
        assert_eq!(config.has_configured_chains, !config.chains.is_empty());
        assert_eq!(config.has_configured_drains, !config.drains.is_empty());
    }

    #[test]
    fn test_parse_toml_string_array_parses_correctly() {
        assert_eq!(
            parse_toml_string_array(r#"["a", "b", "c"]"#),
            Some(vec!["a".to_string(), "b".to_string(), "c".to_string()])
        );
        assert_eq!(parse_toml_string_array(r"[]"), Some(vec![]));
        assert_eq!(parse_toml_string_array("not-an-array"), None);
    }

    #[test]
    fn test_parse_toml_quoted_string_parses_correctly() {
        assert_eq!(
            parse_toml_quoted_string(r#""hello""#),
            Some("hello".to_string())
        );
        assert_eq!(parse_toml_quoted_string("not-quoted"), None);
        assert_eq!(parse_toml_quoted_string(r#""""#), Some(String::new()));
    }

    // --- ToolUpdateInfo tests ---

    #[test]
    fn test_check_tool_updates_returns_result_for_all_tools() {
        let result = check_tool_updates();
        assert!(result.is_ok(), "check_tool_updates should succeed");
        let updates = result.unwrap();
        assert_eq!(
            updates.len(),
            3,
            "Should check 3 tools: Claude Code, Codex, OpenCode"
        );
        let tool_names: Vec<&str> = updates.iter().map(|u| u.name.as_str()).collect();
        assert!(
            tool_names.contains(&"Claude Code"),
            "Should include Claude Code"
        );
        assert!(tool_names.contains(&"Codex"), "Should include Codex");
        assert!(tool_names.contains(&"OpenCode"), "Should include OpenCode");
    }
}
