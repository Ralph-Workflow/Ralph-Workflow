//! System and agent diagnostics.
//!
//! This module provides comprehensive diagnostic information for troubleshooting
//! Ralph configuration and environment issues.

pub mod runtime;
mod agents;
mod system;

pub use agents::AgentDiagnostics;
pub use system::SystemInfo;

use crate::agents::AgentRegistry;

/// Complete diagnostic report.
#[derive(Debug)]
pub struct DiagnosticReport {
    pub system: SystemInfo,
    pub agents: AgentDiagnostics,
}

/// Run all diagnostics and return the report.
///
/// This function gathers all diagnostic information without printing.
/// The CLI handler is responsible for formatting and displaying the results.
#[must_use]
pub fn run_diagnostics(registry: &AgentRegistry) -> DiagnosticReport {
    let system = SystemInfo::gather();
    let agents = AgentDiagnostics::test(registry);

    DiagnosticReport { system, agents }
}
