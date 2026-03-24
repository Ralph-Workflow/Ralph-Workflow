//! Session type construction tests
//!
//! These tests verify the core session data types are constructed correctly.

#[cfg(test)]
mod session_id_tests {
    use crate::agents::session::{AgentSessionId, SessionDrain};

    #[test]
    fn agent_session_id_unique_per_construction() {
        let id1 = AgentSessionId::new("run-123", &SessionDrain::Planning, 0);
        let id2 = AgentSessionId::new("run-123", &SessionDrain::Planning, 1);
        let id3 = AgentSessionId::new("run-124", &SessionDrain::Planning, 0);

        // Different counters produce different IDs
        assert_ne!(id1.as_str(), id2.as_str());
        // Different run IDs produce different IDs
        assert_ne!(id1.as_str(), id3.as_str());
    }

    #[test]
    fn agent_session_id_as_str() {
        let id = AgentSessionId::new("run-123", &SessionDrain::Development, 5);
        assert_eq!(id.as_str(), "run-123-development-5");
    }
}

#[cfg(test)]
mod session_drain_tests {
    use crate::agents::session::SessionDrain;

    #[test]
    fn session_drain_display() {
        assert_eq!(SessionDrain::Planning.to_string(), "planning");
        assert_eq!(SessionDrain::Development.to_string(), "development");
        assert_eq!(SessionDrain::Analysis.to_string(), "analysis");
        assert_eq!(SessionDrain::Review.to_string(), "review");
        assert_eq!(SessionDrain::Fix.to_string(), "fix");
        assert_eq!(SessionDrain::Commit.to_string(), "commit");
    }
}

#[cfg(test)]
mod capability_set_tests {
    use crate::agents::session::{Capability, CapabilitySet};

    #[test]
    fn capability_set_contains_present() {
        let mut caps = CapabilitySet::default();
        caps.insert(Capability::WorkspaceRead);
        assert!(caps.contains(Capability::WorkspaceRead));
    }

    #[test]
    fn capability_set_contains_absent() {
        let caps = CapabilitySet::default();
        assert!(!caps.contains(Capability::WorkspaceRead));
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn capability_set_default_empty() {
        let caps = CapabilitySet::default();
        assert!(!caps.contains(Capability::WorkspaceRead));
        assert!(!caps.contains(Capability::ProcessExecBounded));
    }
}

#[cfg(test)]
mod policy_flag_set_tests {
    use crate::agents::session::{PolicyFlag, PolicyFlagSet};

    #[test]
    fn policy_flag_set_contains_present() {
        let mut flags = PolicyFlagSet::default();
        flags.insert(PolicyFlag::NoEdit);
        assert!(flags.contains(PolicyFlag::NoEdit));
    }

    #[test]
    fn policy_flag_set_contains_absent() {
        let flags = PolicyFlagSet::default();
        assert!(!flags.contains(PolicyFlag::NoEdit));
        assert!(!flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn policy_flag_set_default_empty() {
        let flags = PolicyFlagSet::default();
        assert!(!flags.contains(PolicyFlag::NoEdit));
    }
}

#[cfg(test)]
mod agent_session_tests {
    use crate::agents::session::{
        AgentSession, AgentSessionId, AuditTrail, Capability, CapabilitySet, PolicyFlag,
        PolicyFlagSet, PolicyOutcome, SessionDrain, PROTOCOL_VERSION_V1,
    };
    use std::time::SystemTime;

    #[test]
    fn agent_session_contains_correct_fields() {
        let mut caps = CapabilitySet::default();
        caps.insert(Capability::WorkspaceRead);

        let mut flags = PolicyFlagSet::default();
        flags.insert(PolicyFlag::NoEdit);

        let session = AgentSession {
            session_id: AgentSessionId::new("run-123", &SessionDrain::Review, 0),
            run_id: "run-123".to_string(),
            drain: SessionDrain::Review,
            protocol_version: PROTOCOL_VERSION_V1.to_string(),
            capabilities: caps,
            policy_flags: flags,
            created_at: SystemTime::now(),
        };

        assert_eq!(session.run_id, "run-123");
        assert_eq!(session.drain, SessionDrain::Review);
        assert_eq!(session.protocol_version, "ralph-mcp/1.0");
        assert!(session.capabilities.contains(Capability::WorkspaceRead));
        assert!(session.policy_flags.contains(PolicyFlag::NoEdit));
    }

    #[test]
    fn protocol_version_v1() {
        assert_eq!(PROTOCOL_VERSION_V1, "ralph-mcp/1.0");
    }

    #[test]
    fn check_capability_returns_approved_when_present() {
        let mut caps = CapabilitySet::default();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::GitWrite);

        let session = AgentSession::new(
            "run-123".to_string(),
            SessionDrain::Development,
            0,
            caps,
            PolicyFlagSet::default(),
        );

        assert_eq!(
            session.check_capability(Capability::WorkspaceRead),
            PolicyOutcome::Approved
        );
        assert_eq!(
            session.check_capability(Capability::GitWrite),
            PolicyOutcome::Approved
        );
    }

    #[test]
    fn check_capability_returns_denied_when_absent() {
        let session = AgentSession::for_drain("run-123".to_string(), SessionDrain::Planning, 0);

        let outcome = session.check_capability(Capability::GitWrite);
        assert!(matches!(outcome, PolicyOutcome::Denied { .. }));
        if let PolicyOutcome::Denied { reason } = outcome {
            assert!(reason.contains("git.write"));
            assert!(reason.contains("planning"));
        }
    }

    #[test]
    fn check_capability_for_all_drain_capabilities() {
        // Development drain should approve WorkspaceWriteTracked and ProcessExecBounded
        let dev_session =
            AgentSession::for_drain("run-1".to_string(), SessionDrain::Development, 0);
        assert_eq!(
            dev_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Approved
        );
        assert_eq!(
            dev_session.check_capability(Capability::ProcessExecBounded),
            PolicyOutcome::Approved
        );

        // Planning drain should NOT approve WorkspaceWriteTracked
        let planning_session =
            AgentSession::for_drain("run-2".to_string(), SessionDrain::Planning, 0);
        assert!(matches!(
            planning_session.check_capability(Capability::WorkspaceWriteTracked),
            PolicyOutcome::Denied { .. }
        ));

        // Commit drain should approve GitWrite
        let commit_session = AgentSession::for_drain("run-3".to_string(), SessionDrain::Commit, 0);
        assert_eq!(
            commit_session.check_capability(Capability::GitWrite),
            PolicyOutcome::Approved
        );
    }

    #[test]
    fn capabilities_accessors_returns_reference_to_capabilities() {
        let session = AgentSession::for_drain("run".to_string(), SessionDrain::Development, 0);

        let retrieved_caps = session.capabilities();
        assert!(retrieved_caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn policy_flags_accessors_returns_reference_to_policy_flags() {
        let session = AgentSession::for_drain("run".to_string(), SessionDrain::Planning, 0);

        let retrieved_flags = session.policy_flags();
        assert!(retrieved_flags.contains(PolicyFlag::NoEdit));
    }

    #[test]
    fn drain_accessor_returns_drain() {
        let session = AgentSession::for_drain("run".to_string(), SessionDrain::Review, 0);
        assert_eq!(session.drain(), SessionDrain::Review);
    }

    #[test]
    fn record_capability_injection_creates_approved_records() {
        let session =
            AgentSession::for_drain("run-inject".to_string(), SessionDrain::Development, 0);
        let timestamp = 1700000000u64;

        let trail = AuditTrail::new();
        let trail = trail.record_capability_injection(
            &session.session_id,
            timestamp,
            session.capabilities(),
        );

        // Should have one record per capability
        assert_eq!(
            trail.len(),
            session.capabilities().iter().count(),
            "Audit trail should have one record per capability"
        );

        // All records should be Approved
        for record in trail.records() {
            assert!(
                matches!(record.outcome, PolicyOutcome::Approved),
                "All injection records should have Approved outcome"
            );
            assert!(
                record
                    .description
                    .contains("injected into prompt template variables"),
                "Description should mention prompt template injection"
            );
        }
    }

    #[test]
    fn record_capability_injection_empty_for_no_capabilities() {
        let session_id = AgentSessionId::new("run-empty", &SessionDrain::Planning, 0);
        let caps = CapabilitySet::new();
        let timestamp = 1700000000u64;

        let trail = AuditTrail::new();
        let trail = trail.record_capability_injection(&session_id, timestamp, &caps);

        assert!(
            trail.is_empty(),
            "Audit trail should be empty when no capabilities"
        );
    }
}
