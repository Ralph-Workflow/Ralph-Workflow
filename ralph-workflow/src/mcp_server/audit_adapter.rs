//! Audit sink adapter bridging MCP audit records into Ralph's AuditTrail.
//!
//! Translates `mcp_server::dispatch::audit::AuditRecord` (with `timestamp_nanos`,
//! `session_id`, `tool_name`, `decision`, `path`, `capability`) into Ralph's
//! `AuditRecord` format (with `session_id: AgentSessionId`, `timestamp: u64` in
//! seconds, `capability: Capability`, `outcome: PolicyOutcome`, `description: String`).

use crate::agents::session::{
    AgentSessionId, AuditRecord as RalphAuditRecord, Capability, PolicyOutcome,
};
use crate::mcp_server::capability_mapping::lookup_ralph_capability;
use mcp_server::dispatch::access::{AccessDecision, AuditSink};
use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;
use std::sync::Mutex;

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
            .and_then(lookup_ralph_capability)
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
