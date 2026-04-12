//! Integration tests for session handshake recording.
//!
//! These tests verify that when an agent is invoked, the session handshake
//! (capabilities, policy flags, drain type) is recorded in the audit trail
//! as the first record.

use ralph_workflow::agents::session::{AgentSession, AuditTrail, Capability, SessionDrain};

use crate::test_timeout::with_default_timeout;

/// Verify that a Planning session has the expected read-only capabilities.
#[test]
fn planning_session_has_readonly_capabilities() {
    with_default_timeout(|| {
        let session =
            AgentSession::for_drain("test-planning".to_string(), SessionDrain::Planning, 0);

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

/// Verify that a Development session has write capabilities.
#[test]
fn development_session_has_write_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-dev".to_string(), SessionDrain::Development, 0);

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
                .contains(Capability::ProcessExecBounded),
            "Development should have ProcessExecBounded"
        );
        // Note: Development does NOT have GitWrite - that's Commit's domain
        assert!(
            !session.capabilities.contains(Capability::GitWrite),
            "Development should NOT have GitWrite"
        );
    });
}

/// Verify that a Commit session has git write but not process exec.
#[test]
fn commit_session_has_git_write_capability() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-commit".to_string(), SessionDrain::Commit, 0);

        // Commit should have git write
        assert!(
            session.capabilities.contains(Capability::GitWrite),
            "Commit should have GitWrite"
        );

        // Commit should NOT have process exec (no development agent)
        assert!(
            !session
                .capabilities
                .contains(Capability::ProcessExecBounded),
            "Commit should NOT have ProcessExecBounded"
        );
    });
}

/// Verify that a Review session has read-only git capabilities.
#[test]
fn review_session_has_readonly_git_capabilities() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-review".to_string(), SessionDrain::Review, 0);

        // Review should have git read
        assert!(
            session.capabilities.contains(Capability::GitStatusRead),
            "Review should have GitStatusRead"
        );
        assert!(
            session.capabilities.contains(Capability::GitDiffRead),
            "Review should have GitDiffRead"
        );

        // Review should NOT have git write
        assert!(
            !session.capabilities.contains(Capability::GitWrite),
            "Review should NOT have GitWrite"
        );
    });
}

/// Verify that AuditTrail can be constructed with a handshake record.
#[test]
fn audit_trail_accepts_handshake_record() {
    with_default_timeout(|| {
        let session = AgentSession::for_drain("test-audit".to_string(), SessionDrain::Planning, 0);
        let timestamp = 1700000000u64;

        // Create a handshake-like record
        let handshake_record = ralph_workflow::agents::session::AuditRecord::new(
            session.session_id.clone(),
            timestamp,
            Capability::EnvRead,
            ralph_workflow::agents::session::PolicyOutcome::Approved,
            format!(
                "Session handshake: drain={}, protocol={}, capabilities=[{}], policy_flags=[]",
                session.drain.as_str(),
                session.protocol_version,
                session
                    .capabilities
                    .iter()
                    .map(|c| c.identifier())
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        );

        let trail = AuditTrail::from_records(vec![handshake_record]);

        assert_eq!(trail.len(), 1, "Audit trail should have one record");
        assert!(!trail.is_empty(), "Audit trail should not be empty");
    });
}
