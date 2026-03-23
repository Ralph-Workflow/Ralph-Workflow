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
        AgentSession, AgentSessionId, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet,
        SessionDrain, PROTOCOL_VERSION_V1,
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
}
