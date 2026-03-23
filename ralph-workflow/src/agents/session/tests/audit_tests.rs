//! Audit trail tests for session model.
//!
//! Tests for structured audit records that capture:
//! - capability set issued
//! - tool calls requested
//! - policy outcome
//! - execution result metadata
//! - artifact submission status
//! - denial reasons and retry patterns

#[cfg(test)]
mod session_audit_tests {
    use crate::agents::session::{
        AgentSession, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet, SessionDrain,
        PROTOCOL_VERSION_V1,
    };
    use std::time::SystemTime;

    #[test]
    fn session_records_capabilities_issued() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::GitStatusRead);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);

        let session = AgentSession::new(
            "run-789".to_string(),
            SessionDrain::Planning,
            0,
            caps,
            flags,
        );

        // Verify capabilities were recorded
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(!session.capabilities.contains(Capability::GitWrite));
    }

    #[test]
    fn session_records_policy_flags() {
        let caps = CapabilitySet::new();
        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);
        flags.insert(PolicyFlag::AllowGitRead);

        let session =
            AgentSession::new("run-abc".to_string(), SessionDrain::Review, 0, caps, flags);

        // Verify policy flags were recorded
        assert!(session.policy_flags.contains(PolicyFlag::NoEdit));
        assert!(session.policy_flags.contains(PolicyFlag::AllowGitRead));
        assert!(!session.policy_flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn session_records_drain_identity() {
        let session = AgentSession::new(
            "run-xyz".to_string(),
            SessionDrain::Development,
            1,
            CapabilitySet::new(),
            PolicyFlagSet::new(),
        );

        assert_eq!(session.drain, SessionDrain::Development);
        assert_eq!(session.drain.as_str(), "development");
    }

    #[test]
    fn session_records_run_id() {
        let session = AgentSession::new(
            "run-12345".to_string(),
            SessionDrain::Commit,
            0,
            CapabilitySet::new(),
            PolicyFlagSet::new(),
        );

        assert_eq!(session.run_id, "run-12345");
    }

    #[test]
    fn session_records_protocol_version() {
        let session = AgentSession::new(
            "run-version-test".to_string(),
            SessionDrain::Fix,
            0,
            CapabilitySet::new(),
            PolicyFlagSet::new(),
        );

        assert_eq!(session.protocol_version, PROTOCOL_VERSION_V1);
        assert_eq!(session.protocol_version, "ralph-mcp/1.0");
    }

    #[test]
    fn session_records_creation_time() {
        let before = SystemTime::now();
        let session = AgentSession::new(
            "run-timing".to_string(),
            SessionDrain::Analysis,
            0,
            CapabilitySet::new(),
            PolicyFlagSet::new(),
        );
        let after = SystemTime::now();

        assert!(session.created_at >= before);
        assert!(session.created_at <= after);
    }

    #[test]
    fn session_id_unique_per_drain_and_counter() {
        let caps = CapabilitySet::new();
        let flags = PolicyFlagSet::new();

        let session1 = AgentSession::new(
            "run-same".to_string(),
            SessionDrain::Planning,
            0,
            caps.clone(),
            flags.clone(),
        );
        let session2 = AgentSession::new(
            "run-same".to_string(),
            SessionDrain::Planning,
            1,
            caps.clone(),
            flags.clone(),
        );

        // Different counters produce different session IDs
        assert_ne!(session1.session_id.as_str(), session2.session_id.as_str());
    }

    #[test]
    fn session_id_unique_per_run_id() {
        let caps = CapabilitySet::new();
        let flags = PolicyFlagSet::new();

        let session1 = AgentSession::new(
            "run-001".to_string(),
            SessionDrain::Development,
            0,
            caps.clone(),
            flags.clone(),
        );
        let session2 = AgentSession::new(
            "run-002".to_string(),
            SessionDrain::Development,
            0,
            caps.clone(),
            flags.clone(),
        );

        // Different run IDs produce different session IDs
        assert_ne!(session1.session_id.as_str(), session2.session_id.as_str());
    }
}
