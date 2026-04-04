use std::collections::HashMap;

use toml::value::Table;

use super::config_chains::AgentInfo;

pub(super) fn parse_chains_from_toml(toml: &str) -> HashMap<String, Vec<String>> {
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

pub(super) fn parse_drains_from_toml(toml: &str) -> HashMap<String, String> {
    parse_table(toml, "agent_drains", |table| {
        table
            .iter()
            .filter_map(|(key, value)| value.as_str().map(|v| (key.clone(), v.to_string())))
            .collect()
    })
}

pub(super) fn parse_agents_from_toml(toml: &str) -> Vec<AgentInfo> {
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

pub(super) fn merge_chains(
    global: HashMap<String, Vec<String>>,
    project: HashMap<String, Vec<String>>,
) -> HashMap<String, Vec<String>> {
    global.into_iter().chain(project.into_iter()).collect()
}

pub(super) fn merge_drains(
    global: HashMap<String, String>,
    project: HashMap<String, String>,
) -> HashMap<String, String> {
    global.into_iter().chain(project.into_iter()).collect()
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
