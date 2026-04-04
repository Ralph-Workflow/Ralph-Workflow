use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use specta::Type;

use super::config_parsing::{
    merge_chains, merge_drains, parse_agents_from_toml, parse_chains_from_toml,
    parse_drains_from_toml,
};
use super::config_storage;
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct AgentInfo {
    pub name: String,
    pub tool: String,
    pub model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ChainInfo {
    pub name: String,
    pub agents: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct EffectiveChainsConfig {
    pub chains: Vec<ChainInfo>,
    pub drains: HashMap<String, String>,
    pub agents: Vec<AgentInfo>,
    pub has_configured_chains: bool,
    pub has_configured_drains: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct AgentProfile {
    pub name: String,
    pub developer_agent: String,
    pub reviewer_agent: String,
}

pub fn get_effective_chains_config(repo_path: String) -> Result<EffectiveChainsConfig, String> {
    let global_toml = config_storage::get_raw_global_config_toml()?;
    let project_toml = config_storage::get_raw_project_config_toml(repo_path)?;

    let global_chains = parse_chains_from_toml(&global_toml);
    let project_chains = parse_chains_from_toml(&project_toml);
    let merged_chains_map = merge_chains(global_chains, project_chains);

    let global_drains = parse_drains_from_toml(&global_toml);
    let project_drains = parse_drains_from_toml(&project_toml);
    let merged_drains = merge_drains(global_drains, project_drains);

    let global_agents = parse_agents_from_toml(&global_toml);
    let project_agents = parse_agents_from_toml(&project_toml);
    let merged_agents = global_agents
        .into_iter()
        .chain(project_agents.into_iter())
        .map(|agent| (agent.name.clone(), agent))
        .collect::<HashMap<_, _>>()
        .into_values()
        .collect();

    let chains = merged_chains_map
        .into_iter()
        .map(|(name, agents)| ChainInfo { name, agents })
        .collect::<Vec<_>>();
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

pub fn parse_agent_profiles_from_toml(toml: &str) -> Vec<AgentProfile> {
    if let Ok(value) = toml::from_str::<toml::Value>(toml) {
        if let Some(agents) = value.get("agents").and_then(|v| v.as_array()) {
            return agents
                .iter()
                .filter_map(|agent| {
                    let name = agent.get("name")?.as_str()?.to_string();
                    let developer_agent = agent.get("developer_agent")?.as_str()?.to_string();
                    let reviewer_agent = agent.get("reviewer_agent")?.as_str()?.to_string();
                    Some(AgentProfile {
                        name,
                        developer_agent,
                        reviewer_agent,
                    })
                })
                .collect();
        }
    }

    Vec::new()
}
