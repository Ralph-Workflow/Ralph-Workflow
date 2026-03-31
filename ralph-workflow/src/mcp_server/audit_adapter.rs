//! Audit sink adapter bridging MCP audit records into Ralph's AuditTrail.
//!
//! Translates `mcp_server::dispatch::audit::AuditRecord` (with `timestamp_nanos`,
//! `session_id`, `tool_name`, `decision`, `path`, `capability`) into Ralph's
//! `AuditRecord` format (with `session_id: AgentSessionId`, `timestamp: u64` in
//! seconds, `capability: Capability`, `outcome: PolicyOutcome`, `description: String`).

use crate::agents::session::{
    AgentSessionId, AuditRecord as RalphAuditRecord, Capability, PolicyOutcome,
};
use mcp_server::dispatch::access::{AccessDecision, AuditSink, McpCapability};
use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;
use std::sync::Mutex;

/// Policy: map McpCapability back to Ralph Capability for audit record emission.
///
/// This is the inverse of `map_mcp_capability` used in `RalphHostSessionAdapter`.
pub(crate) fn map_capability_to_ralph(cap: McpCapability) -> Capability {
    match cap {
        McpCapability::WorkspaceRead => Capability::WorkspaceRead,
        McpCapability::WorkspaceWriteEphemeral => Capability::WorkspaceWriteEphemeral,
        McpCapability::WorkspaceWriteTracked => Capability::WorkspaceWriteTracked,
        McpCapability::WorkspaceWriteAny => Capability::WorkspaceWriteTracked,
        McpCapability::GitStatusRead => Capability::GitStatusRead,
        McpCapability::GitWrite => Capability::GitWrite,
        McpCapability::EnvRead => Capability::EnvRead,
        McpCapability::EnvWrite => Capability::EnvWrite,
        McpCapability::ProcessExecBounded => Capability::ProcessExecBounded,
        McpCapability::ProcessExecUnbounded => Capability::ProcessExecUnbounded,
        McpCapability::ArtifactSubmit => Capability::ArtifactSubmit,
        McpCapability::RunReportProgress => Capability::RunReportProgress,
        // #[non_exhaustive] McpCapability — fail-closed for authorization (handled by
        // map_mcp_capability above). For audit records, use WorkspaceRead as safe
        // fallback to preserve audit functionality rather than crashing.
        _ => Capability::WorkspaceRead,
    }
}

/// Policy: convert McpAuditRecord decision to Ralph PolicyOutcome.
pub(crate) fn outcome_from_decision(decision: &AccessDecision) -> PolicyOutcome {
    match decision {
        AccessDecision::Allow => PolicyOutcome::Approved,
        AccessDecision::Deny { .. } => PolicyOutcome::Denied {
            reason: decision.to_error_string(),
        },
    }
}

/// Adapter that implements `mcp_server::dispatch::access::AuditSink` to bridge
/// MCP audit records into Ralph's `AuditTrail`.
///
/// Stores records in an internal buffer that can be drained via `drain_records()`.
pub(crate) struct RalphAuditSinkAdapter {
    records: Mutex<Vec<RalphAuditRecord>>,
}

impl RalphAuditSinkAdapter {
    /// Create a new empty audit sink adapter.
    pub(crate) fn new() -> Self {
        Self {
            records: Mutex::new(Vec::new()),
        }
    }

    /// Drain all accumulated audit records, returning them and clearing the buffer.
    pub(crate) fn drain_records(&self) -> Vec<RalphAuditRecord> {
        let mut records = self.records.lock().unwrap();
        std::mem::take(&mut records)
    }
}

impl Default for RalphAuditSinkAdapter {
    fn default() -> Self {
        Self::new()
    }
}

impl AuditSink for RalphAuditSinkAdapter {
    fn emit(&self, record: McpAuditRecord) {
        let capability = record
            .capability
            .map(map_capability_to_ralph)
            .unwrap_or(Capability::WorkspaceRead);

        // Convert nanoseconds timestamp to seconds
        let timestamp_secs = record.timestamp_nanos / 1_000_000_000;

        // Build description from tool name and decision
        let description = if record.decision.is_allowed() {
            format!("MCP tool '{}' executed successfully", record.tool_name)
        } else {
            format!(
                "MCP tool '{}' access denied: {}",
                record.tool_name,
                record.decision.to_error_string()
            )
        };

        let ralph_record = RalphAuditRecord::new(
            AgentSessionId::from_string(record.session_id.clone()),
            timestamp_secs,
            capability,
            outcome_from_decision(&record.decision),
            description,
        );

        self.records.lock().unwrap().push(ralph_record);
    }

    fn flush(&self) {
        // No-op: records are already stored in memory
    }
}
