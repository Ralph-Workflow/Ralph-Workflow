//! Capability tests for session model.
//!
//! Tests for the capability vocabulary defined in RFC-009 Phase 1:
//! - workspace.read
//! - workspace.write_ephemeral
//! - workspace.write_tracked
//! - process.exec_bounded
//! - artifact.submit
//! - run.report_progress
//! - git.status_read
//! - git.diff_read
//! - git.write
//! - env.read

#[cfg(test)]
mod capability_identifier_tests {
    use crate::agents::session::Capability;

    #[test]
    fn workspace_capability_identifiers() {
        assert_eq!(Capability::WorkspaceRead.identifier(), "workspace.read");
        assert_eq!(
            Capability::WorkspaceWriteEphemeral.identifier(),
            "workspace.write_ephemeral"
        );
        assert_eq!(
            Capability::WorkspaceWriteTracked.identifier(),
            "workspace.write_tracked"
        );
    }

    #[test]
    fn process_capability_identifiers() {
        assert_eq!(
            Capability::ProcessExecBounded.identifier(),
            "process.exec_bounded"
        );
    }

    #[test]
    fn artifact_capability_identifiers() {
        assert_eq!(Capability::ArtifactSubmit.identifier(), "artifact.submit");
    }

    #[test]
    fn run_capability_identifiers() {
        assert_eq!(
            Capability::RunReportProgress.identifier(),
            "run.report_progress"
        );
    }

    #[test]
    fn git_capability_identifiers() {
        assert_eq!(Capability::GitStatusRead.identifier(), "git.status_read");
        assert_eq!(Capability::GitDiffRead.identifier(), "git.diff_read");
        assert_eq!(Capability::GitWrite.identifier(), "git.write");
    }

    #[test]
    fn env_capability_identifiers() {
        assert_eq!(Capability::EnvRead.identifier(), "env.read");
    }
}

#[cfg(test)]
mod capability_set_tests {
    use crate::agents::session::{Capability, CapabilitySet};

    #[test]
    fn capability_set_starts_empty() {
        let caps = CapabilitySet::new();
        assert!(!caps.contains(Capability::WorkspaceRead));
        assert!(!caps.contains(Capability::GitWrite));
        assert!(!caps.contains(Capability::ProcessExecBounded));
    }

    #[test]
    fn capability_set_insert_and_contains() {
        let mut caps = CapabilitySet::new();

        caps.insert(Capability::WorkspaceRead);
        assert!(caps.contains(Capability::WorkspaceRead));

        caps.insert(Capability::GitStatusRead);
        assert!(caps.contains(Capability::GitStatusRead));
        // WorkspaceRead should still be present
        assert!(caps.contains(Capability::WorkspaceRead));
    }

    #[test]
    fn capability_set_to_vec() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::ArtifactSubmit);

        let vec = caps.to_vec();
        assert!(vec.contains(&Capability::WorkspaceRead));
        assert!(vec.contains(&Capability::ArtifactSubmit));
        assert_eq!(vec.len(), 2);
    }

    #[test]
    fn capability_set_iter() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::ProcessExecBounded);

        let iterated: Vec<_> = caps.iter().collect();
        assert!(iterated.contains(&Capability::WorkspaceRead));
        assert!(iterated.contains(&Capability::ProcessExecBounded));
        assert!(!iterated.contains(&Capability::GitWrite));
    }

    #[test]
    fn capability_set_default_is_empty() {
        let caps = CapabilitySet::default();
        assert!(!caps.contains(Capability::WorkspaceRead));
        assert!(!caps.contains(Capability::ArtifactSubmit));
    }

    #[test]
    fn capability_set_equality() {
        let mut caps1 = CapabilitySet::new();
        caps1.insert(Capability::WorkspaceRead);

        let mut caps2 = CapabilitySet::new();
        caps2.insert(Capability::WorkspaceRead);

        assert_eq!(caps1, caps2);
    }

    #[test]
    fn capability_set_inequality() {
        let mut caps1 = CapabilitySet::new();
        caps1.insert(Capability::WorkspaceRead);

        let caps2 = CapabilitySet::new();

        assert_ne!(caps1, caps2);
    }
}

#[cfg(test)]
mod capability_defaults_by_drain_tests {
    use crate::agents::session::{Capability, CapabilitySet, SessionDrain};

    /// Helper to get default capabilities for a given drain.
    fn default_capabilities_for_drain(drain: SessionDrain) -> CapabilitySet {
        let mut caps = CapabilitySet::new();
        match drain {
            SessionDrain::Planning | SessionDrain::Analysis | SessionDrain::Review => {
                caps.insert(Capability::WorkspaceRead);
                caps.insert(Capability::GitStatusRead);
                caps.insert(Capability::GitDiffRead);
                caps.insert(Capability::ArtifactSubmit);
            }
            SessionDrain::Development => {
                caps.insert(Capability::WorkspaceRead);
                caps.insert(Capability::WorkspaceWriteEphemeral);
                caps.insert(Capability::WorkspaceWriteTracked);
                caps.insert(Capability::GitStatusRead);
                caps.insert(Capability::GitDiffRead);
                caps.insert(Capability::ProcessExecBounded);
                caps.insert(Capability::ArtifactSubmit);
            }
            SessionDrain::Fix => {
                caps.insert(Capability::WorkspaceRead);
                caps.insert(Capability::WorkspaceWriteTracked);
                caps.insert(Capability::GitStatusRead);
                caps.insert(Capability::GitDiffRead);
                caps.insert(Capability::ProcessExecBounded);
                caps.insert(Capability::ArtifactSubmit);
            }
            SessionDrain::Commit => {
                caps.insert(Capability::GitStatusRead);
                caps.insert(Capability::GitDiffRead);
                caps.insert(Capability::GitWrite);
            }
        }
        caps
    }

    #[test]
    fn planning_drain_default_capabilities() {
        let caps = default_capabilities_for_drain(SessionDrain::Planning);
        assert!(caps.contains(Capability::WorkspaceRead));
        assert!(caps.contains(Capability::GitStatusRead));
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }

    #[test]
    fn development_drain_default_capabilities() {
        let caps = default_capabilities_for_drain(SessionDrain::Development);
        assert!(caps.contains(Capability::WorkspaceRead));
        assert!(caps.contains(Capability::WorkspaceWriteTracked));
        assert!(caps.contains(Capability::ProcessExecBounded));
    }

    #[test]
    fn commit_drain_default_capabilities() {
        let caps = default_capabilities_for_drain(SessionDrain::Commit);
        assert!(caps.contains(Capability::GitWrite));
        assert!(caps.contains(Capability::GitStatusRead));
        assert!(!caps.contains(Capability::WorkspaceWriteTracked));
    }
}
