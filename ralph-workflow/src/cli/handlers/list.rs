//! Agent listing handlers.
//!
//! This module provides handlers for listing agents and their configurations.

use crate::agents::{is_ccs_ref, AgentRegistry};
use itertools::Itertools;
use std::io::Write;

/// Handle --list-agents command.
///
/// Lists all registered agents with their configuration details including:
/// - Agent name
/// - Command to invoke the agent
/// - JSON parser type
/// - Whether the agent can create commits (`can_commit` flag)
///
/// CCS aliases (ccs/...) are displayed separately for clarity.
/// Output is sorted alphabetically by agent name within each section.
pub fn handle_list_agents(registry: &AgentRegistry) {
    let items: Vec<_> = registry
        .list()
        .into_iter()
        .sorted_by(|(a, _), (b, _)| a.cmp(b))
        .collect();

    let (ccs_aliases, regular_agents): (Vec<_>, Vec<_>) =
        items.into_iter().partition(|(name, _)| is_ccs_ref(name));

    if !regular_agents.is_empty() {
        let _ = writeln!(std::io::stdout(), "Agents:");
        for (name, cfg) in regular_agents {
            let display_name = registry.display_name(name);
            let _ = writeln!(
                std::io::stdout(),
                "  {}\tcmd={}\tparser={}\tcan_commit={}",
                display_name,
                cfg.cmd,
                cfg.json_parser,
                cfg.can_commit
            );
        }
    }

    if !ccs_aliases.is_empty() {
        let _ = writeln!(std::io::stdout(), "\nCCS Aliases:");
        for (name, cfg) in ccs_aliases {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {}\t→ \"{}\"", display_name, cfg.cmd);
        }
    }
}

/// Handle --list-available-agents command.
///
/// Lists only agents whose commands are available on the system PATH.
/// This helps users quickly identify which agents they can use without
/// additional setup.
///
/// CCS aliases are shown separately to distinguish them from regular agents.
/// Output is sorted alphabetically by agent name within each section.
pub fn handle_list_available_agents(registry: &AgentRegistry) {
    let items: Vec<_> = registry
        .list_available()
        .into_iter()
        .sorted_unstable()
        .collect();

    let (ccs_aliases, regular_agents): (Vec<_>, Vec<_>) =
        items.into_iter().partition(|name| is_ccs_ref(name));

    if !regular_agents.is_empty() {
        let _ = writeln!(std::io::stdout(), "Available agents:");
        for name in regular_agents {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {display_name}");
        }
    }

    if !ccs_aliases.is_empty() {
        let _ = writeln!(std::io::stdout(), "\nAvailable CCS aliases:");
        for name in ccs_aliases {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {display_name}");
        }
    }
}
