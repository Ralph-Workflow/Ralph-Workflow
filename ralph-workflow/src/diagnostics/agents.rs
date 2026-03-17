//! Agent diagnostics and availability testing.

use crate::agents::AgentRegistry;

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
        let mut agent_status = Vec::new();
        let mut available_count: usize = 0;

        for (name, cfg) in &all_agents {
            let available = registry.is_agent_available(name);
            if available {
                available_count = available_count.saturating_add(1);
            }

            agent_status.push(AgentStatus {
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
            });
        }

        let total_agents = all_agents.len();
        let unavailable_agents = total_agents - available_count;

        // Sort by name for consistent output
        agent_status.sort_by(|a, b| a.name.cmp(&b.name));

        Self {
            total_agents,
            available_agents: available_count,
            unavailable_agents,
            agent_status,
        }
    }
}
