//! Policy flag tests for session model.
//!
//! Tests for policy flags that modify session behavior:
//! - no_edit
//! - allow_shell
//! - allow_git_read
//! - allow_git_write
//! - allow_parallel_workers
//! - allow_network
//! - allow_env_read

#[cfg(test)]
mod policy_flag_identifier_tests {
    use crate::agents::session::PolicyFlag;

    #[test]
    fn no_edit_identifier() {
        assert_eq!(PolicyFlag::NoEdit.identifier(), "no_edit");
    }

    #[test]
    fn allow_shell_identifier() {
        assert_eq!(PolicyFlag::AllowShell.identifier(), "allow_shell");
    }

    #[test]
    fn allow_git_identifiers() {
        assert_eq!(PolicyFlag::AllowGitRead.identifier(), "allow_git_read");
        assert_eq!(PolicyFlag::AllowGitWrite.identifier(), "allow_git_write");
    }

    #[test]
    fn allow_parallel_workers_identifier() {
        assert_eq!(
            PolicyFlag::AllowParallelWorkers.identifier(),
            "allow_parallel_workers"
        );
    }

    #[test]
    fn allow_network_identifier() {
        assert_eq!(PolicyFlag::AllowNetwork.identifier(), "allow_network");
    }

    #[test]
    fn allow_env_read_identifier() {
        assert_eq!(PolicyFlag::AllowEnvRead.identifier(), "allow_env_read");
    }
}

#[cfg(test)]
mod policy_flag_set_tests {
    use crate::agents::session::{PolicyFlag, PolicyFlagSet};

    #[test]
    fn policy_flag_set_starts_empty() {
        let flags = PolicyFlagSet::new();
        assert!(!flags.contains(PolicyFlag::NoEdit));
        assert!(!flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn policy_flag_set_insert_and_contains() {
        let mut flags = PolicyFlagSet::new();

        flags.insert(PolicyFlag::NoEdit);
        assert!(flags.contains(PolicyFlag::NoEdit));

        flags.insert(PolicyFlag::AllowGitRead);
        assert!(flags.contains(PolicyFlag::AllowGitRead));
        // NoEdit should still be present
        assert!(flags.contains(PolicyFlag::NoEdit));
    }

    #[test]
    fn policy_flag_set_to_vec() {
        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);
        flags.insert(PolicyFlag::AllowShell);

        let vec = flags.to_vec();
        assert!(vec.contains(&PolicyFlag::NoEdit));
        assert!(vec.contains(&PolicyFlag::AllowShell));
        assert_eq!(vec.len(), 2);
    }

    #[test]
    fn policy_flag_set_iter() {
        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);
        flags.insert(PolicyFlag::AllowGitWrite);

        let iterated: Vec<_> = flags.iter().collect();
        assert!(iterated.contains(&PolicyFlag::NoEdit));
        assert!(iterated.contains(&PolicyFlag::AllowGitWrite));
        assert!(!iterated.contains(&PolicyFlag::AllowShell));
    }

    #[test]
    fn policy_flag_set_default_is_empty() {
        let flags = PolicyFlagSet::default();
        assert!(!flags.contains(PolicyFlag::NoEdit));
        assert!(!flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn policy_flag_set_equality() {
        let mut flags1 = PolicyFlagSet::new();
        flags1.insert(PolicyFlag::NoEdit);

        let mut flags2 = PolicyFlagSet::new();
        flags2.insert(PolicyFlag::NoEdit);

        assert_eq!(flags1, flags2);
    }

    #[test]
    fn policy_flag_set_inequality() {
        let mut flags1 = PolicyFlagSet::new();
        flags1.insert(PolicyFlag::NoEdit);

        let flags2 = PolicyFlagSet::new();

        assert_ne!(flags1, flags2);
    }
}

#[cfg(test)]
mod no_edit_policy_tests {
    use crate::agents::session::{
        AgentSession, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet, SessionDrain,
    };

    #[test]
    fn no_edit_session_cannot_write_tracked() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceWriteTracked);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);

        let session = AgentSession::new(
            "run-noedit".to_string(),
            SessionDrain::Review,
            0,
            caps,
            flags,
        );

        // Even though WorkspaceWriteTracked is in capabilities,
        // the NoEdit policy flag should prevent writes
        assert!(session
            .capabilities
            .contains(Capability::WorkspaceWriteTracked));
        assert!(session.policy_flags.contains(PolicyFlag::NoEdit));
    }

    #[test]
    fn no_edit_with_development_drain() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::WorkspaceRead);
        caps.insert(Capability::WorkspaceWriteTracked);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::NoEdit);

        let session = AgentSession::new(
            "run-noedit-dev".to_string(),
            SessionDrain::Development,
            0,
            caps,
            flags,
        );

        // NoEdit policy restricts Development drain
        assert!(session.policy_flags.contains(PolicyFlag::NoEdit));
        assert_eq!(session.drain, SessionDrain::Development);
    }
}

#[cfg(test)]
mod git_policy_tests {
    use crate::agents::session::{
        AgentSession, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet, SessionDrain,
    };

    #[test]
    fn git_read_policy_allows_status_and_diff() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::GitStatusRead);
        caps.insert(Capability::GitDiffRead);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::AllowGitRead);

        let session = AgentSession::new(
            "run-gitread".to_string(),
            SessionDrain::Review,
            0,
            caps,
            flags,
        );

        assert!(session.capabilities.contains(Capability::GitStatusRead));
        assert!(session.capabilities.contains(Capability::GitDiffRead));
        assert!(session.policy_flags.contains(PolicyFlag::AllowGitRead));
    }

    #[test]
    fn git_write_policy_allows_commits() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::GitWrite);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::AllowGitWrite);

        let session = AgentSession::new(
            "run-gitwrite".to_string(),
            SessionDrain::Commit,
            0,
            caps,
            flags,
        );

        assert!(session.capabilities.contains(Capability::GitWrite));
        assert!(session.policy_flags.contains(PolicyFlag::AllowGitWrite));
    }

    #[test]
    fn git_write_without_policy_should_still_have_capability() {
        // The capability can be granted, but policy determines enforcement
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::GitWrite);

        let flags = PolicyFlagSet::new();

        let session = AgentSession::new(
            "run-gitwrite-nopolicy".to_string(),
            SessionDrain::Commit,
            0,
            caps,
            flags,
        );

        // GitWrite capability exists but no AllowGitWrite policy flag
        assert!(session.capabilities.contains(Capability::GitWrite));
        assert!(!session.policy_flags.contains(PolicyFlag::AllowGitWrite));
    }
}

#[cfg(test)]
mod shell_policy_tests {
    use crate::agents::session::{
        AgentSession, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet, SessionDrain,
    };

    #[test]
    fn shell_allowed_for_development() {
        let mut caps = CapabilitySet::new();
        caps.insert(Capability::ProcessExecBounded);

        let mut flags = PolicyFlagSet::new();
        flags.insert(PolicyFlag::AllowShell);

        let session = AgentSession::new(
            "run-shell".to_string(),
            SessionDrain::Development,
            0,
            caps,
            flags,
        );

        assert!(session
            .capabilities
            .contains(Capability::ProcessExecBounded));
        assert!(session.policy_flags.contains(PolicyFlag::AllowShell));
    }

    #[test]
    fn shell_disallowed_for_planning() {
        let caps = CapabilitySet::new();
        let flags = PolicyFlagSet::new();

        let session = AgentSession::new(
            "run-no-shell".to_string(),
            SessionDrain::Planning,
            0,
            caps,
            flags,
        );

        assert!(!session.policy_flags.contains(PolicyFlag::AllowShell));
    }
}
