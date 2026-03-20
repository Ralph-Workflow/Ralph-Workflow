//! Agent listing handlers.
//!
//! This module provides handlers for listing agents and their configurations.

use crate::agents::{is_ccs_ref, AgentRegistry};
use itertools::Itertools;

trait StdIoWriteCompat {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()>;
}

impl<T: std::io::Write> StdIoWriteCompat for T {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()> {
        std::io::Write::write_fmt(self, args)
    }
}

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
    let agents: Vec<(&str, _)> = registry.list();
    let (ccs_aliases, regular_agents): (Vec<_>, Vec<_>) =
        agents.into_iter().partition(|(name, _)| is_ccs_ref(name));

    let ccs_aliases = ccs_aliases
        .into_iter()
        .sorted_by(|(a, _): &(&str, _), (b, _): &(&str, _)| a.cmp(b))
        .collect::<Vec<_>>();
    let regular_agents = regular_agents
        .into_iter()
        .sorted_by(|(a, _): &(&str, _), (b, _): &(&str, _)| a.cmp(b))
        .collect::<Vec<_>>();

    if !regular_agents.is_empty() {
        let _ = writeln!(std::io::stdout(), "Agents:");
        regular_agents.iter().for_each(|(name, cfg)| {
            let display_name = registry.display_name(name);
            let _ = writeln!(
                std::io::stdout(),
                "  {}\tcmd={}\tparser={}\tcan_commit={}",
                display_name,
                cfg.cmd,
                cfg.json_parser,
                cfg.can_commit
            );
        });
    }

    if !ccs_aliases.is_empty() {
        let _ = writeln!(std::io::stdout(), "\nCCS Aliases:");
        ccs_aliases.iter().for_each(|(name, cfg)| {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {}\t→ \"{}\"", display_name, cfg.cmd);
        });
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
    let available: Vec<&str> = registry.list_available();
    let (ccs_aliases, regular_agents): (Vec<_>, Vec<_>) =
        available.into_iter().partition(|name| is_ccs_ref(name));

    let ccs_aliases = ccs_aliases
        .into_iter()
        .sorted_unstable()
        .collect::<Vec<_>>();
    let regular_agents = regular_agents
        .into_iter()
        .sorted_unstable()
        .collect::<Vec<_>>();

    if !regular_agents.is_empty() {
        let _ = writeln!(std::io::stdout(), "Available agents:");
        regular_agents.iter().for_each(|name| {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {display_name}");
        });
    }

    if !ccs_aliases.is_empty() {
        let _ = writeln!(std::io::stdout(), "\nAvailable CCS aliases:");
        ccs_aliases.iter().for_each(|name| {
            let display_name = registry.display_name(name);
            let _ = writeln!(std::io::stdout(), "  {display_name}");
        });
    }
}
