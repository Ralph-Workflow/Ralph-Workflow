use std::collections::HashMap;

use toml::value::Table;

use super::config_chains::AgentInfo;
use super::config_helpers::{
    ConfigFieldWithSource, ConfigSource, ConfigView, EffectiveConfigWithSources,
};
use ralph_workflow::config::unified::UnifiedConfig;

pub(crate) fn parse_chains_from_toml(toml: &str) -> HashMap<String, Vec<String>> {
    parse_table(toml, "agent_chains", |table| {
        table
            .iter()
            .filter_map(|(key, value)| {
                value.as_array().map(|arr| {
                    let agents = arr
                        .iter()
                        .filter_map(|item| item.as_str().map(str::to_string))
                        .collect::<Vec<_>>();
                    (key.clone(), agents)
                })
            })
            .collect()
    })
}

pub(crate) fn parse_drains_from_toml(toml: &str) -> HashMap<String, String> {
    parse_table(toml, "agent_drains", |table| {
        table
            .iter()
            .filter_map(|(key, value)| value.as_str().map(|v| (key.clone(), v.to_string())))
            .collect()
    })
}

pub(crate) fn parse_agents_from_toml(toml: &str) -> Vec<AgentInfo> {
    parse_table(toml, "agents", |table| {
        table
            .iter()
            .filter_map(|(name, value)| {
                value.as_table().and_then(|agent_table| {
                    let tool = agent_table.get("tool").and_then(|v| v.as_str())?;
                    let model = agent_table.get("model").and_then(|v| v.as_str())?;
                    Some(AgentInfo {
                        name: name.clone(),
                        tool: tool.to_string(),
                        model: model.to_string(),
                    })
                })
            })
            .collect()
    })
}

pub(crate) fn merge_chains(
    global: HashMap<String, Vec<String>>,
    project: HashMap<String, Vec<String>>,
) -> HashMap<String, Vec<String>> {
    global.into_iter().chain(project).collect()
}

pub(crate) fn merge_drains(
    global: HashMap<String, String>,
    project: HashMap<String, String>,
) -> HashMap<String, String> {
    global.into_iter().chain(project).collect()
}

fn parse_table<T>(toml: &str, section: &str, parser: impl FnOnce(&Table) -> T) -> T
where
    T: Default,
{
    if let Ok(value) = toml::from_str::<toml::Value>(toml) {
        if let Some(table) = value.get(section).and_then(|v| v.as_table()) {
            return parser(table);
        }
    }

    T::default()
}

/// Determine the source for each config field using TOML presence detection.
///
/// A field is "set" at a layer if it appears explicitly in that layer's TOML.
/// Priority: Project > Global > Default.
pub(crate) fn build_source_list_from_toml(
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

/// Parse the effective config with per-field source tracking from raw TOML strings.
///
/// # Errors
///
/// Returns an error string if the project TOML cannot be parsed.
pub(crate) fn parse_effective_config_with_sources(
    global_toml: &str,
    project_toml: &str,
) -> Result<EffectiveConfigWithSources, String> {
    let global_config = if global_toml.is_empty() {
        UnifiedConfig::default()
    } else {
        UnifiedConfig::load_from_content(global_toml).unwrap_or_default()
    };

    let (effective_view, opt_project_toml): (ConfigView, Option<String>) =
        if project_toml.is_empty() {
            (ConfigView::from(&global_config), None)
        } else {
            let project_parsed = UnifiedConfig::load_from_content(project_toml)
                .map_err(|e| format!("Failed to parse project config: {e}"))?;
            let merged = global_config.merge_with_content(project_toml, &project_parsed);
            (ConfigView::from(&merged), Some(project_toml.to_string()))
        };

    let sources = build_source_list_from_toml(global_toml, opt_project_toml.as_ref());

    Ok(EffectiveConfigWithSources {
        config: effective_view,
        sources,
    })
}
