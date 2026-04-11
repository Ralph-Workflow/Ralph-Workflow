//! Pure audit types for the MCP dispatch layer.
//!
//! `AuditRecord` uses `u64` nanos for the timestamp to stay free of I/O
//! dependencies, keeping this module in the pure dispatch layer.

use crate::dispatch::access::{AccessDecision, McpCapability, PolicyMode};
use std::path::PathBuf;

/// Type of event being recorded.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AuditEventType {
    /// A tipical tool invocation.
    #[default]
    Tool,
    /// A denial decision (fails policy/access checks).
    Denial,
    /// A runtime mode transition (drain/policy update).
    ModeTransition,
    /// Heartbeat health signal (grace window/termination).
    Heartbeat,
    /// Self-termination triggered by heartbeat loss.
    SelfTermination,
}

/// Correlation data sent with each audit record.
#[derive(Debug, Clone, Default)]
pub struct AuditCorrelation {
    /// Run identifier for the session that emitted the audit record.
    pub run_id: Option<String>,
    /// Generation counter for the MCP endpoint lease.
    pub generation: Option<u32>,
    /// Drain identity associated with the session.
    pub drain: Option<String>,
    /// Active policy mode at the time of the event.
    pub policy_mode: Option<PolicyMode>,
}

/// Extended metadata attached to audit events.
#[derive(Debug, Clone, Default)]
pub struct AuditMetadata {
    /// Type of audit event being recorded.
    pub event_type: AuditEventType,
    /// Optional human-readable details attached to the event.
    pub details: Option<String>,
    /// Correlation metadata for the event.
    pub correlation: AuditCorrelation,
}

/// Immutable record of a single access decision or tool invocation.
///
/// Uses `timestamp_nanos: u64` instead of `SystemTime` to stay in the
/// pure dispatch layer. The I/O layer sets the real timestamp on emit.
#[derive(Debug, Clone)]
pub struct AuditRecord {
    /// Nanoseconds since UNIX_EPOCH. Set by the io layer when the record is emitted.
    pub timestamp_nanos: u64,
    /// Unique session identifier for the request.
    pub session_id: String,
    /// Name of the tool or method invoked.
    pub tool_name: String,
    /// The access decision made for this invocation.
    pub decision: AccessDecision,
    /// Path involved in the operation, if applicable.
    pub path: Option<PathBuf>,
    /// Capability required for the operation, if applicable.
    pub capability: Option<McpCapability>,
    /// Auxiliary metadata describing the audit event.
    pub metadata: AuditMetadata,
}

impl AuditRecord {
    /// Create a new audit record with timestamp_nanos set to 0 (io layer stamps on emit).
    pub fn new(session_id: String, tool_name: String, decision: AccessDecision) -> Self {
        Self {
            timestamp_nanos: 0,
            session_id,
            tool_name,
            decision,
            path: None,
            capability: None,
            metadata: AuditMetadata::default(),
        }
    }

    /// Set the path for this audit record.
    pub fn with_path(mut self, path: PathBuf) -> Self {
        self.path = Some(path);
        self
    }

    /// Set the capability for this audit record.
    pub fn with_capability(mut self, capability: McpCapability) -> Self {
        self.capability = Some(capability);
        self
    }

    /// Attach metadata to the audit record.
    pub fn with_metadata(mut self, metadata: AuditMetadata) -> Self {
        self.metadata = metadata;
        self
    }
}
