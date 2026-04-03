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

#[cfg(test)]
mod tests {
    use super::*;
    use mcp_server::dispatch::access::{AccessDecision, McpCapability};
    use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;

    #[test]
    fn test_drain_records_non_empty() {
        let adapter = RalphAuditSinkAdapter::new();

        // Emit a record representing a successful tool call
        let mcp_record = McpAuditRecord::new(
            "test-session-123".to_string(),
            "ralph_read_file".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::WorkspaceRead);

        adapter.emit(mcp_record);

        // Drain and verify
        let drained = adapter.drain_records();
        assert_eq!(drained.len(), 1);
        let record = &drained[0];
        assert_eq!(record.session_id.as_str(), "test-session-123");
        assert!(record.description.contains("ralph_read_file"));
        assert!(matches!(record.outcome, PolicyOutcome::Approved));
    }

    #[test]
    fn test_drain_records_multiple() {
        let adapter = RalphAuditSinkAdapter::new();

        // Emit multiple records
        let record1 = McpAuditRecord::new(
            "session-1".to_string(),
            "ralph_read_file".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::WorkspaceRead);

        let record2 = McpAuditRecord::new(
            "session-1".to_string(),
            "ralph_write_file".to_string(),
            AccessDecision::Deny {
                reason: "ReadOnly mode".to_string(),
                code: mcp_server::dispatch::access::AccessDeniedCode::ReadOnlyMode,
            },
        )
        .with_capability(McpCapability::WorkspaceWriteAny);

        adapter.emit(record1);
        adapter.emit(record2);

        let drained = adapter.drain_records();
        assert_eq!(drained.len(), 2);

        // First record should be allowed
        assert!(matches!(&drained[0].outcome, PolicyOutcome::Approved));

        // Second record should be denied
        assert!(matches!(&drained[1].outcome, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn test_drain_clears_records() {
        let adapter = RalphAuditSinkAdapter::new();

        let record = McpAuditRecord::new(
            "session".to_string(),
            "ralph_git_status".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::GitStatusRead);

        adapter.emit(record);

        // First drain returns the record
        let first = adapter.drain_records();
        assert_eq!(first.len(), 1);

        // Second drain is empty
        let second = adapter.drain_records();
        assert!(second.is_empty());
    }
}
