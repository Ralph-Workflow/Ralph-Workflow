//! Audit trail persistence for RFC-009 Phase 2.
//!
//! This module provides runtime recording and workspace persistence for audit trails:
//! - Record effect capability checks (approved/denied)
//! - Record command policy checks
//! - Serialize audit trail to NDJSON format
//! - Persist to workspace at `.agent/audit/{session_id}.json`
//!
//! # Audit Record Format
//!
//! Each audit record serializes to JSON with these fields:
//! - `session_id`: Session identifier
//! - `timestamp`: Unix seconds since epoch
//! - `record_type`: "capability_check" | "command_check" | "capability_injection"
//! - `capability`: Capability identifier string
//! - `outcome`: "approved" | "denied" | "approved_with_restriction"
//! - `description`: Human-readable description
//! - `effect_name`: Optional, for capability checks
//! - `command`: Optional, for command checks
//!
//! # Persistence Location
//!
//! Audit trails persist to `.agent/audit/{session_id}.json` as NDJSON
//! (one record per line). This allows streaming writes and easy post-run analysis.

use crate::agents::session::{AgentSessionId, AuditRecord, AuditTrail, Capability, PolicyOutcome};
use crate::workspace::Workspace;
use serde::{Deserialize, Serialize};
use std::path::Path;

/// Record type for audit entries.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuditRecordType {
    /// A capability check performed during effect execution.
    CapabilityCheck,
    /// A command policy check for shell execution.
    CommandCheck,
    /// A capability injection into prompt template variables.
    CapabilityInjection,
}

/// Extended audit record with type information for persistence.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedAuditRecord {
    /// Session this record belongs to.
    pub session_id: String,
    /// UTC timestamp of the event (Unix seconds since epoch).
    pub timestamp: u64,
    /// The type of audit record.
    pub record_type: AuditRecordType,
    /// The capability that was exercised or checked (identifier string).
    pub capability: String,
    /// The policy outcome for this interaction.
    pub outcome: String,
    /// Human-readable description of what was attempted.
    pub description: String,
    /// The effect name for capability checks (optional).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub effect_name: Option<String>,
    /// The command for command checks (optional).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
}

impl From<&AuditRecord> for PersistedAuditRecord {
    fn from(record: &AuditRecord) -> Self {
        let outcome_str = match &record.outcome {
            PolicyOutcome::Approved => "approved",
            PolicyOutcome::Denied { .. } => "denied",
            PolicyOutcome::ApprovedWithRestriction { .. } => "approved_with_restriction",
        };

        Self {
            session_id: record.session_id.as_str().to_string(),
            timestamp: record.timestamp,
            record_type: AuditRecordType::CapabilityCheck,
            capability: record.capability.identifier().to_string(),
            outcome: outcome_str.to_string(),
            description: record.description.clone(),
            effect_name: None,
            command: None,
        }
    }
}

/// Record a capability check for an effect execution.
///
/// This creates a new audit record documenting that a capability check
/// was performed during effect handling.
///
/// # Arguments
///
/// * `trail` - The current audit trail
/// * `session_id` - The session this check belongs to
/// * `timestamp` - Unix timestamp when the check occurred
/// * `effect_name` - Name of the effect being checked
/// * `capabilities_checked` - The capabilities that were required for the effect
/// * `outcome` - The policy outcome (approved or denied)
///
/// # Returns
///
/// A new audit trail with the appended record.
#[must_use]
pub fn record_effect_check(
    trail: &AuditTrail,
    session_id: &AgentSessionId,
    timestamp: u64,
    effect_name: &str,
    capabilities_checked: &[Capability],
    outcome: &PolicyOutcome,
) -> AuditTrail {
    // Create one record per capability checked
    let new_records: Vec<AuditRecord> = capabilities_checked
        .iter()
        .map(|cap| {
            let description = match outcome {
                PolicyOutcome::Approved => {
                    format!(
                        "Capability {} approved for effect {} in session {}",
                        cap.identifier(),
                        effect_name,
                        session_id.as_str()
                    )
                }
                PolicyOutcome::Denied { ref reason } => {
                    format!(
                        "Capability {} denied for effect {} in session {}: {}",
                        cap.identifier(),
                        effect_name,
                        session_id.as_str(),
                        reason
                    )
                }
                PolicyOutcome::ApprovedWithRestriction { ref restriction } => {
                    format!(
                        "Capability {} approved with restriction '{}' for effect {} in session {}",
                        cap.identifier(),
                        restriction,
                        effect_name,
                        session_id.as_str()
                    )
                }
            };

            AuditRecord::new(
                session_id.clone(),
                timestamp,
                *cap,
                outcome.clone(),
                description,
            )
        })
        .collect();

    AuditTrail::from_records(trail.records().iter().cloned().chain(new_records))
}

/// Record a command policy check.
///
/// This creates a new audit record documenting that a shell command
/// was checked against the command policy.
///
/// # Arguments
///
/// * `trail` - The current audit trail
/// * `session_id` - The session this check belongs to
/// * `timestamp` - Unix timestamp when the check occurred
/// * `command` - The command that was checked
/// * `outcome` - The policy outcome (approved or denied)
///
/// # Returns
///
/// A new audit trail with the appended record.
#[must_use]
pub fn record_command_check(
    trail: &AuditTrail,
    session_id: &AgentSessionId,
    timestamp: u64,
    command: &str,
    outcome: &PolicyOutcome,
) -> AuditTrail {
    let capability = Capability::ProcessExecBounded;
    let description = match outcome {
        PolicyOutcome::Approved => {
            format!(
                "Command '{}' approved by policy in session {}",
                command,
                session_id.as_str()
            )
        }
        PolicyOutcome::Denied { ref reason } => {
            format!(
                "Command '{}' denied by policy in session {}: {}",
                command,
                session_id.as_str(),
                reason
            )
        }
        PolicyOutcome::ApprovedWithRestriction { ref restriction } => {
            format!(
                "Command '{}' approved with restriction '{}' in session {}",
                command,
                restriction,
                session_id.as_str()
            )
        }
    };

    let record = AuditRecord::new(
        session_id.clone(),
        timestamp,
        capability,
        outcome.clone(),
        description,
    );

    AuditTrail::from_records(
        trail
            .records()
            .iter()
            .cloned()
            .chain(std::iter::once(record)),
    )
}

/// Serialize an audit trail to NDJSON format.
///
/// Each line is a valid JSON object representing one audit record.
/// This format allows streaming writes and easy post-run analysis.
///
/// # Arguments
///
/// * `trail` - The audit trail to serialize
///
/// # Returns
///
/// A string containing NDJSON records (one per line).
#[must_use]
pub fn serialize_audit_trail(trail: &AuditTrail) -> String {
    trail
        .records()
        .iter()
        .map(|record| {
            let persisted: PersistedAuditRecord = record.into();
            serde_json::to_string(&persisted).unwrap_or_else(|_| {
                // Fallback for serialization errors - shouldn't happen with valid data
                r#"{"error":"serialization_failed"}"#.to_string()
            })
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Write audit trail to workspace at `.agent/audit/{session_id}.json`.
///
/// The audit directory is created if it doesn't exist.
/// The file is written as NDJSON (one record per line).
///
/// # Arguments
///
/// * `workspace` - The workspace to write to
/// * `session_id` - The session ID for naming the audit file
/// * `trail` - The audit trail to persist
///
/// # Errors
///
/// Returns error if directory creation or file write fails.
pub fn persist_audit_trail(
    workspace: &dyn Workspace,
    session_id: &AgentSessionId,
    trail: &AuditTrail,
) -> anyhow::Result<()> {
    let audit_dir = Path::new(".agent").join("audit");
    workspace
        .create_dir_all(&audit_dir)
        .map_err(|e| anyhow::anyhow!("Failed to create audit directory: {}", e))?;

    let audit_file = audit_dir.join(format!("{}.jsonl", session_id.as_str()));
    let content = serialize_audit_trail(trail);

    workspace
        .write(&audit_file, &content)
        .map_err(|e| anyhow::anyhow!("Failed to write audit trail: {}", e))?;

    Ok(())
}

/// Persist a session handshake as the first record in the audit trail.
///
/// This should be called at the start of agent invocation to record
/// the session's declared capabilities and policy flags.
///
/// # Arguments
///
/// * `workspace` - The workspace to write to
/// * `session_id` - The session ID for naming the audit file
/// * `timestamp` - Unix timestamp when the handshake was issued
/// * `drain` - The drain identity
/// * `protocol_version` - The protocol version string
/// * `capabilities` - Comma-separated capability identifiers
/// * `policy_flags` - Comma-separated policy flag identifiers
///
/// # Errors
///
/// Returns error if directory creation or file write fails.
pub fn persist_session_handshake(
    workspace: &dyn Workspace,
    session_id: &AgentSessionId,
    timestamp: u64,
    drain: &str,
    protocol_version: &str,
    capabilities: &str,
    policy_flags: &str,
) -> anyhow::Result<()> {
    let audit_dir = Path::new(".agent").join("audit");
    workspace
        .create_dir_all(&audit_dir)
        .map_err(|e| anyhow::anyhow!("Failed to create audit directory: {}", e))?;

    let audit_file = audit_dir.join(format!("{}.jsonl", session_id.as_str()));

    // Create handshake record as a special audit entry
    let handshake_record = PersistedAuditRecord {
        session_id: session_id.as_str().to_string(),
        timestamp,
        record_type: AuditRecordType::CapabilityInjection,
        capability: "session_handshake".to_string(),
        outcome: "approved".to_string(),
        description: format!(
            "Session handshake: drain={}, protocol={}, capabilities=[{}], policy_flags=[{}]",
            drain, protocol_version, capabilities, policy_flags
        ),
        effect_name: None,
        command: None,
    };

    let content = serde_json::to_string(&handshake_record)
        .map_err(|e| anyhow::anyhow!("Failed to serialize handshake: {}", e))?;

    // Append to existing file or create new
    let existing = workspace.read(&audit_file).unwrap_or_default();
    let new_content = if existing.is_empty() {
        content
    } else {
        format!("{}\n{}", existing, content)
    };

    workspace
        .write(&audit_file, &new_content)
        .map_err(|e| anyhow::anyhow!("Failed to write session handshake: {}", e))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{Capability, SessionDrain};

    fn test_session_id() -> AgentSessionId {
        AgentSessionId::new("test-run", &SessionDrain::Planning, 1)
    }

    fn test_timestamp() -> u64 {
        1700000000
    }

    #[test]
    fn record_effect_check_creates_records() {
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let caps = vec![Capability::WorkspaceRead, Capability::GitStatusRead];
        let outcome = PolicyOutcome::Approved;

        let new_trail = record_effect_check(
            &trail,
            &session_id,
            test_timestamp(),
            "TestEffect",
            &caps,
            &outcome,
        );

        assert_eq!(new_trail.len(), 2);
    }

    #[test]
    fn record_effect_check_with_denial() {
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let caps = vec![Capability::WorkspaceWriteTracked];
        let outcome = PolicyOutcome::Denied {
            reason: "Not granted for planning drain".to_string(),
        };

        let new_trail = record_effect_check(
            &trail,
            &session_id,
            test_timestamp(),
            "TestEffect",
            &caps,
            &outcome,
        );

        assert_eq!(new_trail.len(), 1);
        let record = &new_trail.records()[0];
        assert!(matches!(record.outcome, PolicyOutcome::Denied { .. }));
    }

    #[test]
    fn record_command_check_creates_record() {
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let outcome = PolicyOutcome::Approved;

        let new_trail = record_command_check(
            &trail,
            &session_id,
            test_timestamp(),
            "cargo test",
            &outcome,
        );

        assert_eq!(new_trail.len(), 1);
        let record = &new_trail.records()[0];
        assert_eq!(record.capability, Capability::ProcessExecBounded);
    }

    #[test]
    fn serialize_audit_trail_ndjson_format() {
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let caps = vec![Capability::WorkspaceRead];
        let outcome = PolicyOutcome::Approved;

        let new_trail = record_effect_check(
            &trail,
            &session_id,
            test_timestamp(),
            "TestEffect",
            &caps,
            &outcome,
        );

        let serialized = serialize_audit_trail(&new_trail);

        // Should be valid NDJSON (one JSON object per line)
        let lines: Vec<&str> = serialized.lines().collect();
        assert_eq!(lines.len(), 1);

        // Each line should be valid JSON
        let parsed: serde_json::Value = serde_json::from_str(lines[0]).expect("Valid JSON");
        assert_eq!(parsed["session_id"], "test-run-planning-1");
        assert_eq!(parsed["capability"], "workspace.read");
        assert_eq!(parsed["outcome"], "approved");
    }

    #[test]
    fn serialize_empty_trail() {
        let trail = AuditTrail::new();
        let serialized = serialize_audit_trail(&trail);
        assert!(serialized.is_empty());
    }

    #[test]
    fn persisted_audit_record_from_audit_record() {
        let session_id = test_session_id();
        let record = AuditRecord::new(
            session_id.clone(),
            test_timestamp(),
            Capability::GitStatusRead,
            PolicyOutcome::Approved,
            "Test description".to_string(),
        );

        let persisted: PersistedAuditRecord = (&record).into();

        assert_eq!(persisted.session_id, "test-run-planning-1");
        assert_eq!(persisted.capability, "git.status_read");
        assert_eq!(persisted.outcome, "approved");
        assert_eq!(persisted.description, "Test description");
    }

    #[test]
    fn persisted_audit_record_denied_outcome() {
        let session_id = test_session_id();
        let record = AuditRecord::new(
            session_id.clone(),
            test_timestamp(),
            Capability::GitWrite,
            PolicyOutcome::Denied {
                reason: "Test denial".to_string(),
            },
            "Denied description".to_string(),
        );

        let persisted: PersistedAuditRecord = (&record).into();

        assert_eq!(persisted.outcome, "denied");
    }

    #[test]
    fn persisted_audit_record_restriction_outcome() {
        let session_id = test_session_id();
        let record = AuditRecord::new(
            session_id.clone(),
            test_timestamp(),
            Capability::ProcessExecBounded,
            PolicyOutcome::ApprovedWithRestriction {
                restriction: "timeout=60s".to_string(),
            },
            "Restricted".to_string(),
        );

        let persisted: PersistedAuditRecord = (&record).into();

        assert_eq!(persisted.outcome, "approved_with_restriction");
    }

    #[test]
    fn record_effect_check_immutable() {
        // Verify that recording returns a new trail, doesn't modify original
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let caps = vec![Capability::WorkspaceRead];
        let outcome = PolicyOutcome::Approved;

        let _new_trail = record_effect_check(
            &trail,
            &session_id,
            test_timestamp(),
            "TestEffect",
            &caps,
            &outcome,
        );

        assert!(trail.is_empty());
    }

    #[test]
    fn record_command_check_immutable() {
        let trail = AuditTrail::new();
        let session_id = test_session_id();
        let outcome = PolicyOutcome::Approved;

        let _new_trail = record_command_check(
            &trail,
            &session_id,
            test_timestamp(),
            "cargo test",
            &outcome,
        );

        assert!(trail.is_empty());
    }
}
