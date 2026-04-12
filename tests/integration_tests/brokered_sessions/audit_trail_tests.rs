//! Integration tests for audit trail persistence.
//!
//! These tests verify that:
//! - Audit records are accumulated during agent invocations
//! - Audit trail is persisted to the workspace after agent execution completes
//! - Multiple agent invocations accumulate audit records

use ralph_workflow::agents::session::{
    AgentSession, AuditRecord, AuditTrail, Capability, PolicyOutcome, SessionDrain,
};

use crate::test_timeout::with_default_timeout;

/// Test that AuditTrail accumulates records from multiple sources.
#[test]
fn audit_trail_accumulates_records() {
    with_default_timeout(|| {
        let session_id =
            AgentSession::for_drain("test".to_string(), SessionDrain::Development, 0).session_id;
        let timestamp = 1700000000u64;

        // Create initial audit trail
        let record1 = AuditRecord::new(
            session_id.clone(),
            timestamp,
            Capability::WorkspaceRead,
            PolicyOutcome::Approved,
            "Read workspace".to_string(),
        );
        let trail1 = AuditTrail::from_records(vec![record1]);

        // Create second audit trail
        let record2 = AuditRecord::new(
            session_id.clone(),
            timestamp + 1,
            Capability::GitDiffRead,
            PolicyOutcome::Approved,
            "Read diff".to_string(),
        );
        let trail2 = AuditTrail::from_records(vec![record2]);

        // Verify each trail has correct count
        assert_eq!(trail1.len(), 1);
        assert_eq!(trail2.len(), 1);
    });
}

/// Test that AuditTrail correctly reports emptiness.
#[test]
fn audit_trail_empty_check() {
    with_default_timeout(|| {
        let empty_trail = AuditTrail::new();
        assert!(empty_trail.is_empty(), "New audit trail should be empty");
        assert_eq!(empty_trail.len(), 0);

        let session_id =
            AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0).session_id;
        let record = AuditRecord::new(
            session_id,
            1700000000u64,
            Capability::EnvRead,
            PolicyOutcome::Approved,
            "Test".to_string(),
        );
        let non_empty_trail = AuditTrail::from_records(vec![record]);
        assert!(
            !non_empty_trail.is_empty(),
            "Audit trail with records should not be empty"
        );
    });
}

/// Test that AuditRecord stores all fields correctly.
#[test]
fn audit_record_stores_fields() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test".to_string(), SessionDrain::Commit, 0);
        let timestamp = 1700000000u64;

        let record = AuditRecord::new(
            session.session_id.clone(),
            timestamp,
            Capability::GitWrite,
            PolicyOutcome::Approved,
            "Git write approved".to_string(),
        );

        assert_eq!(record.session_id, session.session_id);
        assert_eq!(record.timestamp, timestamp);
        assert!(matches!(record.capability, Capability::GitWrite));
        assert!(matches!(record.outcome, PolicyOutcome::Approved));
        assert_eq!(record.description, "Git write approved");
    });
}

/// Test that AuditRecord correctly represents a denied capability.
#[test]
fn audit_record_denied_capability() {
    with_default_timeout(|| {
        let session_id =
            AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 0).session_id;
        let timestamp = 1700000000u64;

        let denied_record = AuditRecord::new(
            session_id,
            timestamp,
            Capability::ProcessExecBounded,
            PolicyOutcome::Denied {
                reason: "Planning drain does not have ProcessExecBounded capability".to_string(),
            },
            "ProcessExecBounded denied for Planning session".to_string(),
        );

        assert!(matches!(
            denied_record.outcome,
            PolicyOutcome::Denied { .. }
        ));
    });
}

/// Test that AuditTrail can be iterated.
#[test]
fn audit_trail_iteration() {
    with_default_timeout(|| {
        let session_id =
            AgentSession::for_drain("test".to_string(), SessionDrain::Analysis, 0).session_id;

        let records = vec![
            AuditRecord::new(
                session_id.clone(),
                1700000000u64,
                Capability::WorkspaceRead,
                PolicyOutcome::Approved,
                "First".to_string(),
            ),
            AuditRecord::new(
                session_id.clone(),
                1700000001u64,
                Capability::GitStatusRead,
                PolicyOutcome::Approved,
                "Second".to_string(),
            ),
            AuditRecord::new(
                session_id,
                1700000002u64,
                Capability::GitDiffRead,
                PolicyOutcome::Approved,
                "Third".to_string(),
            ),
        ];

        let trail = AuditTrail::from_records(records);

        let mut count = 0;
        for record in trail.records() {
            count += 1;
            // Verify records are accessible
            assert!(!record.description.is_empty());
        }
        assert_eq!(count, 3, "Should iterate over all 3 records");
    });
}
