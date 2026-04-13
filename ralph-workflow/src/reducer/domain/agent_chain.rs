use crate::agents::{AgentDrain, AgentRegistry};
use crate::common::domain_types::AgentName;
use std::collections::HashMap;

pub fn commit_drain_agent_supported(
    registry: &AgentRegistry,
    drain: AgentDrain,
    name: &str,
) -> bool {
    if drain != AgentDrain::Commit {
        return true;
    }

    registry
        .resolve_config(name)
        .is_some_and(|cfg| cfg.can_commit && !is_opencode_command(&cfg.cmd))
}

pub fn resolve_models_for_agents(
    provider_fallback: &HashMap<String, Vec<String>>,
    agents: &[AgentName],
) -> Vec<Vec<String>> {
    agents
        .iter()
        .map(|agent| provider_key(agent.as_str()))
        .map(|provider| {
            provider
                .and_then(|key| provider_fallback.get(key))
                .cloned()
                .unwrap_or_default()
        })
        .collect()
}

fn provider_key(agent_name: &str) -> Option<&str> {
    if agent_name.contains('/') {
        agent_name.split('/').next()
    } else {
        Some(agent_name)
    }
}

fn is_opencode_command(cmd: &str) -> bool {
    cmd.to_ascii_lowercase().contains("opencode")
}
