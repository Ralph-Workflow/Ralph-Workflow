//! Integration tests for RFC-009 session model.
//!
//! These tests verify the RFC-009 V1 session model implementation:
//! - Session creation with drain-specific capabilities
//! - Policy flags per drain
//! - SessionHandshake construction
//! - AuditTrail and AuditRecord types
//! - Session ID uniqueness
//!
//! # Integration Test Style Guide Compliance
//!
//! This module follows the integration test style guide defined in
//! **[`INTEGRATION_TESTS.md`](../INTEGRATION_TESTS.md)**:
//!
//! - **Behavior-based testing:** Tests observable behavior of session types
//! - **No process spawning:** Uses only in-memory types, no mock process execution
//! - **Architectural boundary mocking:** Tests the session data model layer

use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::session::{
    AgentSession, AgentSessionId, AuditRecord, AuditTrail, Capability, PolicyFlag, PolicyOutcome,
    SessionDrain, SessionHandshake,
};

/// Test that Planning drain creates a session with read-only capabilities.
#[test]
fn session_drain_planning_has_readonly_capabilities() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-planning-test".to_string(), SessionDrain::Planning, 0);

        // Planning should have read capabilities
        assert!(
            session.capabilities.contains(Capability::WorkspaceRead),
            "Planning should have WorkspaceRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitStatusRead),
            "Planning should have GitStatusRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitDiffRead),
            "Planning should have GitDiffRead"
        );
        assert!(
            session.capabilities.contains(Capability::ArtifactSubmit),
            "Planning should have ArtifactSubmit"
        );

        // Planning should NOT have write capabilities
        assert!(
            !session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Planning should NOT have WorkspaceWriteTracked"
        );
        assert!(
            !session.capabilities.contains(Capability::GitWrite),
            "Planning should NOT have GitWrite"
        );
        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Planning should NOT have ProcessExecBounded"
        );
    });
}

/// Test that Development drain includes write capabilities.
#[test]
fn session_drain_development_includes_write_capability() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-dev-test".to_string(), SessionDrain::Development, 0);

        // Development should have write capabilities
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Development should have WorkspaceWriteTracked"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::WorkspaceWriteEphemeral),
            "Development should have WorkspaceWriteEphemeral"
        );
        assert!(
            session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Development should have ProcessExecBounded"
        );

        // Development should still have read capabilities
        assert!(
            session.capabilities.contains(Capability::WorkspaceRead),
            "Development should have WorkspaceRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitStatusRead),
            "Development should have GitStatusRead"
        );
    });
}

/// Test that Commit drain has git write capability.
#[test]
fn session_drain_commit_has_git_write_capability() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-commit-test".to_string(), SessionDrain::Commit, 0);

        // Commit should have git write
        assert!(
            session.capabilities.contains(Capability::GitWrite),
            "Commit should have GitWrite"
        );
        assert!(
            session.capabilities.contains(Capability::GitStatusRead),
            "Commit should have GitStatusRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitDiffRead),
            "Commit should have GitDiffRead"
        );

        // Commit should NOT have workspace write
        assert!(
            !session
                .capabilities
                .contains(Capability::WorkspaceWriteTracked),
            "Commit should NOT have WorkspaceWriteTracked"
        );
    });
}

/// Test that Planning and Review drains carry the NoEdit policy flag.
#[test]
fn noedit_policy_flag_present_for_readonly_drains() {
    with_default_timeout(|| {
        let planning_session =
            AgentSession::for_drain("run-planning".to_string(), SessionDrain::Planning, 0);
        assert!(
            planning_session.policy_flags.contains(PolicyFlag::NoEdit),
            "Planning should have NoEdit flag"
        );

        let review_session =
            AgentSession::for_drain("run-review".to_string(), SessionDrain::Review, 0);
        assert!(
            review_session.policy_flags.contains(PolicyFlag::NoEdit),
            "Review should have NoEdit flag"
        );

        let analysis_session =
            AgentSession::for_drain("run-analysis".to_string(), SessionDrain::Analysis, 0);
        assert!(
            analysis_session.policy_flags.contains(PolicyFlag::NoEdit),
            "Analysis should have NoEdit flag"
        );

        // Development should NOT have NoEdit
        let dev_session =
            AgentSession::for_drain("run-dev".to_string(), SessionDrain::Development, 0);
        assert!(
            !dev_session.policy_flags.contains(PolicyFlag::NoEdit),
            "Development should NOT have NoEdit flag"
        );
    });
}

/// Test that SessionHandshake reflects all fields from the session.
#[test]
fn session_handshake_reflects_session_fields() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain(
            "run-handshake-test".to_string(),
            SessionDrain::Development,
            5,
        );
        let handshake = SessionHandshake::from_session(&session);

        assert_eq!(
            handshake.session_id.as_str(),
            session.session_id.as_str(),
            "Handshake session_id should match"
        );
        assert_eq!(
            handshake.drain, session.drain,
            "Handshake drain should match"
        );
        assert_eq!(
            handshake.protocol_version, session.protocol_version,
            "Handshake protocol_version should match"
        );
        assert_eq!(
            handshake.capabilities.to_vec(),
            session.capabilities.to_vec(),
            "Handshake capabilities should match"
        );
        assert_eq!(
            handshake.policy_flags.to_vec(),
            session.policy_flags.to_vec(),
            "Handshake policy_flags should match"
        );
        assert_eq!(
            handshake.issued_at,
            session
                .created_at
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0),
            "Handshake issued_at should be Unix timestamp from session created_at"
        );
    });
}

/// Test that AuditTrail records session capabilities with Approved outcomes.
#[test]
fn audit_trail_records_session_capabilities() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-audit-test".to_string(), SessionDrain::Development, 0);

        // Build audit trail with Approved records for each capability
        let timestamp = 1700000000u64;
        let audit_records: Vec<_> = session
            .capabilities
            .iter()
            .map(|cap| {
                AuditRecord::new(
                    session.session_id.clone(),
                    timestamp,
                    cap,
                    PolicyOutcome::Approved,
                    format!(
                        "Capability {} issued via session handshake",
                        cap.identifier()
                    ),
                )
            })
            .collect();
        let audit_trail = AuditTrail::from_records(audit_records);

        // Verify all capabilities are recorded
        assert_eq!(
            audit_trail.len(),
            session.capabilities.iter().count(),
            "Audit trail should have one record per capability"
        );

        // Verify all records are Approved
        for record in audit_trail.records() {
            assert!(
                matches!(record.outcome, PolicyOutcome::Approved),
                "All records should have Approved outcome"
            );
        }

        // Verify audit trail is not empty
        assert!(!audit_trail.is_empty(), "Audit trail should not be empty");
    });
}

/// Test that AuditRecord stores denial reason correctly.
#[test]
fn audit_record_denied_for_capability_not_in_set() {
    with_default_timeout(|| {
        let session_id = AgentSessionId::new("run-deny-test", &SessionDrain::Planning, 0);
        let timestamp = 1700000000u64;

        let denied_record = AuditRecord::new(
            session_id.clone(),
            timestamp,
            Capability::WorkspaceWriteTracked,
            PolicyOutcome::Denied {
                reason: "Capability not granted for this drain".to_string(),
            },
            "WorkspaceWriteTracked denied for read-only session".to_string(),
        );

        match denied_record.outcome {
            PolicyOutcome::Denied { ref reason } => {
                assert!(
                    reason.contains("not granted"),
                    "Denial reason should explain why capability was denied"
                );
            }
            other => panic!("Expected PolicyOutcome::Denied, got {:?}", other),
        }

        assert_eq!(
            denied_record.session_id.as_str(),
            "run-deny-test-planning-0",
            "Session ID should be preserved in denied record"
        );
        assert_eq!(
            denied_record.capability,
            Capability::WorkspaceWriteTracked,
            "Capability should be preserved in denied record"
        );
    });
}

/// Test that different session counters produce different session IDs.
#[test]
fn session_counter_increments_session_id() {
    with_default_timeout(|| {
        let session1 =
            AgentSession::for_drain("run-same".to_string(), SessionDrain::Development, 0);
        let session2 =
            AgentSession::for_drain("run-same".to_string(), SessionDrain::Development, 1);
        let session3 =
            AgentSession::for_drain("run-same".to_string(), SessionDrain::Development, 2);

        // Same run_id and drain, different counters should produce different IDs
        assert_ne!(
            session1.session_id.as_str(),
            session2.session_id.as_str(),
            "Different counters should produce different session IDs"
        );
        assert_ne!(
            session2.session_id.as_str(),
            session3.session_id.as_str(),
            "Different counters should produce different session IDs"
        );

        // Same parameters should produce same ID
        let session1_copy =
            AgentSession::for_drain("run-same".to_string(), SessionDrain::Development, 0);
        assert_eq!(
            session1.session_id.as_str(),
            session1_copy.session_id.as_str(),
            "Same parameters should produce same session ID"
        );
    });
}

/// Test that check_capability returns Approved for granted capabilities.
#[test]
fn check_capability_returns_approved_for_granted_capabilities() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-check".to_string(), SessionDrain::Development, 0);

        // Development has WorkspaceWriteTracked - should be Approved
        assert_eq!(
            session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Approved,
            "WorkspaceWriteTracked should be Approved for Development drain"
        );

        // Development has ProcessExecBounded - should be Approved
        assert_eq!(
            session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Approved,
            "ProcessExecBounded should be Approved for Development drain"
        );

        // Development has GitWrite (NO) - should be Denied
        let outcome = session.check_capability(Capability::GitWrite);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "GitWrite should be Denied for Development drain"
        );
    });
}

/// Test that check_capability returns Denied for non-granted capabilities.
#[test]
fn check_capability_returns_denied_for_missing_capabilities() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-check-deny".to_string(), SessionDrain::Planning, 0);

        // Planning does NOT have WorkspaceWriteTracked - should be Denied
        let outcome = session.check_capability(Capability::WorkspaceWriteTracked);
        match outcome {
            PolicyOutcome::Denied { ref reason } => {
                assert!(
                    reason.contains("workspace.write_tracked"),
                    "Denial reason should mention the capability: {}",
                    reason
                );
                assert!(
                    reason.contains("planning"),
                    "Denial reason should mention the drain: {}",
                    reason
                );
            }
            other => panic!(
                "Expected PolicyOutcome::Denied for WorkspaceWriteTracked on Planning, got {:?}",
                other
            ),
        }

        // Planning does NOT have ProcessExecBounded - should be Denied
        let outcome = session.check_capability(Capability::ProcessExecBounded);
        assert!(
            matches!(outcome, PolicyOutcome::Denied { .. }),
            "ProcessExecBounded should be Denied for Planning drain"
        );
    });
}

/// Test check_capability for all drain types.
#[test]
fn check_capability_for_all_drain_types() {
    with_default_timeout(|| {
        // Development: has write and exec capabilities
        let dev_session =
            AgentSession::for_drain("run-dev".to_string(), SessionDrain::Development, 0);
        assert_eq!(
            dev_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Approved,
            "Development should approve WorkspaceWriteTracked"
        );
        assert_eq!(
            dev_session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Approved,
            "Development should approve ProcessExecBounded"
        );

        // Planning: read-only, no write/exec
        let planning_session =
            AgentSession::for_drain("run-planning".to_string(), SessionDrain::Planning, 0);
        assert!(matches!(
            planning_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Denied { .. }
        ));
        assert!(matches!(
            planning_session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Denied { .. }
        ));

        // Commit: has git write
        let commit_session =
            AgentSession::for_drain("run-commit".to_string(), SessionDrain::Commit, 0);
        assert_eq!(
            commit_session.check_capability(Capability::GitWrite),
            PolicyOutcome::Approved,
            "Commit should approve GitWrite"
        );
        assert!(matches!(
            commit_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Denied { .. }
        ));

        // Fix: has write and exec (similar to Development)
        let fix_session = AgentSession::for_drain("run-fix".to_string(), SessionDrain::Fix, 0);
        assert_eq!(
            fix_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Approved,
            "Fix should approve WorkspaceWriteTracked"
        );
        assert_eq!(
            fix_session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Approved,
            "Fix should approve ProcessExecBounded"
        );

        // Review: read-only
        let review_session =
            AgentSession::for_drain("run-review".to_string(), SessionDrain::Review, 0);
        assert!(matches!(
            review_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Denied { .. }
        ));
        assert!(matches!(
            review_session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Denied { .. }
        ));
    });
}

/// Test accessor methods: capabilities(), policy_flags(), drain().
#[test]
fn session_accessor_methods() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("run-accessors".to_string(), SessionDrain::Development, 0);

        // Test capabilities() accessor
        let caps = session.capabilities();
        assert!(
            caps.contains(Capability::WorkspaceWriteTracked),
            "capabilities() should return reference with WorkspaceWriteTracked"
        );

        // Test policy_flags() accessor
        let flags = session.policy_flags();
        assert!(
            flags.contains(PolicyFlag::AllowShell),
            "policy_flags() should return reference with AllowShell"
        );

        // Test drain() accessor
        assert_eq!(
            session.drain(),
            SessionDrain::Development,
            "drain() should return Development"
        );
    });
}

/// Test PolicyOutcome variants serialize and deserialize correctly.
#[test]
fn policy_outcome_serde_roundtrip() {
    with_default_timeout(|| {
        // Test Approved roundtrip
        let approved = PolicyOutcome::Approved;
        let serialized = serde_json::to_string(&approved).unwrap();
        let deserialized: PolicyOutcome = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized, approved);

        // Test Denied roundtrip
        let denied = PolicyOutcome::Denied {
            reason: "Test denial reason".to_string(),
        };
        let serialized = serde_json::to_string(&denied).unwrap();
        let deserialized: PolicyOutcome = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized, denied);

        // Test ApprovedWithRestriction roundtrip
        let restricted = PolicyOutcome::ApprovedWithRestriction {
            restriction: "Limited to specific files".to_string(),
        };
        let serialized = serde_json::to_string(&restricted).unwrap();
        let deserialized: PolicyOutcome = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized, restricted);
    });
}

/// Test AuditRecord and AuditTrail serde roundtrip.
#[test]
fn audit_record_and_trail_serde_roundtrip() {
    with_default_timeout(|| {
        let session_id = AgentSessionId::new("run-serde", &SessionDrain::Planning, 0);
        let timestamp = 1700000000u64;

        let record = AuditRecord::new(
            session_id.clone(),
            timestamp,
            Capability::WorkspaceRead,
            PolicyOutcome::Approved,
            "Test description".to_string(),
        );

        // Roundtrip AuditRecord
        let serialized = serde_json::to_string(&record).unwrap();
        let deserialized: AuditRecord = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized.session_id.as_str(), record.session_id.as_str());
        assert_eq!(deserialized.timestamp, record.timestamp);
        assert_eq!(deserialized.capability, record.capability);
        assert_eq!(deserialized.description, record.description);

        // Roundtrip AuditTrail
        let trail = AuditTrail::from_records(vec![record]);
        let serialized = serde_json::to_string(&trail).unwrap();
        let deserialized: AuditTrail = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized.len(), trail.len());
        assert_eq!(
            deserialized.records()[0].session_id.as_str(),
            trail.records()[0].session_id.as_str()
        );
    });
}
