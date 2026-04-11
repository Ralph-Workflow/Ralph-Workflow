//! Audit sink adapter bridging MCP audit records into Ralph's AuditTrail.
//!
//! Translates `mcp_server::dispatch::audit::AuditRecord` (with `timestamp_nanos`,
//! `session_id`, `tool_name`, `decision`, `path`, `capability`) into Ralph's
//! `AuditRecord` format (with `session_id: AgentSessionId`, `timestamp: u64` in
//! seconds, `capability: Capability`, `outcome: PolicyOutcome`, `description: String`).

use crate::agents::session::{
    AgentSessionId, AuditCorrelation, AuditRecord as RalphAuditRecord, Capability, PolicyOutcome,
};
use crate::mcp_server::capability_mapping::lookup_ralph_capability;
use mcp_server::dispatch::access::{AccessDecision, AuditSink};
use mcp_server::dispatch::audit::{
    AuditEventType as McpAuditEventType, AuditRecord as McpAuditRecord,
};
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

fn resolve_audit_capability(record: &McpAuditRecord) -> Capability {
    record
        .capability
        .and_then(lookup_ralph_capability)
        .unwrap_or(Capability::WorkspaceRead)
}

fn event_type_label(event_type: McpAuditEventType) -> &'static str {
    match event_type {
        McpAuditEventType::Tool => "tool",
        McpAuditEventType::Denial => "denial",
        McpAuditEventType::ModeTransition => "mode_transition",
        McpAuditEventType::Heartbeat => "heartbeat",
        McpAuditEventType::SelfTermination => "self_termination",
    }
}

fn default_description(record: &McpAuditRecord) -> String {
    if record.decision.is_allowed() {
        format!("MCP tool '{}' executed successfully", record.tool_name)
    } else {
        format!(
            "MCP tool '{}' access denied: {}",
            record.tool_name,
            record.decision.to_error_string()
        )
    }
}

fn resolve_description(record: &McpAuditRecord) -> String {
    record
        .metadata
        .details
        .clone()
        .unwrap_or_else(|| default_description(record))
}

fn resolve_correlation(record: &McpAuditRecord) -> Option<AuditCorrelation> {
    let corr = record.metadata.correlation.clone();
    let policy_mode = corr.policy_mode.map(|mode| format!("{:?}", mode));
    (corr.run_id.is_some()
        || corr.generation.is_some()
        || corr.drain.is_some()
        || policy_mode.is_some())
    .then_some(AuditCorrelation {
        run_id: corr.run_id,
        generation: corr.generation,
        drain: corr.drain,
        policy_mode,
    })
}

fn to_ralph_record(record: &McpAuditRecord) -> RalphAuditRecord {
    RalphAuditRecord {
        event_type: Some(event_type_label(record.metadata.event_type).to_string()),
        correlation: resolve_correlation(record),
        ..RalphAuditRecord::new(
            AgentSessionId::from_string(record.session_id.clone()),
            record.timestamp_nanos / 1_000_000_000,
            resolve_audit_capability(record),
            outcome_from_decision(&record.decision),
            resolve_description(record),
        )
    }
}

impl AuditSink for RalphAuditSinkAdapter {
    fn emit(&self, record: McpAuditRecord) {
        self.records.lock().unwrap().push(to_ralph_record(&record));
    }

    fn flush(&self) {
        // No-op: records are already stored in memory
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mcp_server::dispatch::access::{AccessDecision, McpCapability, PolicyMode};
    use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;
    use mcp_server::dispatch::audit::{AuditCorrelation as McpAuditCorrelation, AuditMetadata};

    #[test]
    fn test_drain_records_non_empty() {
        let adapter = RalphAuditSinkAdapter::new();

        // Emit a record representing a successful tool call
        let mcp_record = McpAuditRecord::new(
            "test-session-123".to_string(),
            "read_file".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::WorkspaceRead);

        adapter.emit(mcp_record);

        // Drain and verify
        let drained = adapter.drain_records();
        assert_eq!(drained.len(), 1);
        let record = &drained[0];
        assert_eq!(record.session_id.as_str(), "test-session-123");
        assert!(record.description.contains("read_file"));
        assert!(matches!(record.outcome, PolicyOutcome::Approved));
    }

    #[test]
    fn test_drain_records_multiple() {
        let adapter = RalphAuditSinkAdapter::new();

        // Emit multiple records
        let record1 = McpAuditRecord::new(
            "session-1".to_string(),
            "read_file".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::WorkspaceRead);

        let record2 = McpAuditRecord::new(
            "session-1".to_string(),
            "write_file".to_string(),
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
            "git_status".to_string(),
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

    #[test]
    fn test_emit_includes_correlation_and_event_type() {
        let adapter = RalphAuditSinkAdapter::new();
        let mut record = McpAuditRecord::new(
            "session-1".to_string(),
            "heartbeat".to_string(),
            AccessDecision::Allow,
        )
        .with_capability(McpCapability::WorkspaceRead);

        record.metadata = AuditMetadata {
            event_type: McpAuditEventType::Heartbeat,
            details: Some("grace window".to_string()),
            correlation: McpAuditCorrelation {
                run_id: Some("run-abc".to_string()),
                generation: Some(5),
                drain: Some("development".to_string()),
                policy_mode: Some(PolicyMode::Dev),
            },
        };

        adapter.emit(record);

        let drained = adapter.drain_records();
        assert_eq!(drained.len(), 1);
        let r = &drained[0];
        assert_eq!(r.event_type.as_deref(), Some("heartbeat"));
        let corr = r.correlation.as_ref().expect("correlation present");
        assert_eq!(corr.run_id.as_deref(), Some("run-abc"));
        assert_eq!(corr.drain.as_deref(), Some("development"));
    }
}
