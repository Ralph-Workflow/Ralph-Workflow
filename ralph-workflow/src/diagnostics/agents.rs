//! Agent diagnostics and availability testing.

use crate::agents::AgentRegistry;
use itertools::Itertools;

/// Agent diagnostics.
#[derive(Debug)]
pub struct AgentDiagnostics {
    pub total_agents: usize,
    pub available_agents: usize,
    pub unavailable_agents: usize,
    pub agent_status: Vec<AgentStatus>,
}

/// Individual agent status.
#[derive(Debug)]
pub struct AgentStatus {
    pub name: String,
    pub display_name: String,
    pub available: bool,
    pub json_parser: String,
    pub command: String,
}

impl AgentDiagnostics {
    /// Test agent availability.
    #[must_use]
    pub fn test(registry: &AgentRegistry) -> Self {
        let all_agents = registry.list();

        // Build agent status entries using iterator pipeline - functional style
        let agent_status: Vec<AgentStatus> = all_agents
            .iter()
            .map(|(name, cfg)| {
                let available = registry.is_agent_available(name);
                AgentStatus {
                    name: name.to_string(),
                    display_name: registry.display_name(name),
                    available,
                    json_parser: format!("{:?}", cfg.json_parser),
                    command: cfg
                        .cmd
                        .split_whitespace()
                        .next()
                        .unwrap_or(&cfg.cmd)
                        .to_string(),
                }
            })
            .collect();

        // Calculate counts using iterator - functional style
        let available_count = agent_status
            .iter()
            .filter(|status| status.available)
            .count();

        let total_agents = all_agents.len();
        let unavailable_agents = total_agents - available_count;

        // Sort by name for consistent output using functional pipeline
        let agent_status = agent_status
            .into_iter()
            .sorted_by(|a, b| a.name.cmp(&b.name))
            .collect();

        Self {
            total_agents,
            available_agents: available_count,
            unavailable_agents,
            agent_status,
        }
    }
}
