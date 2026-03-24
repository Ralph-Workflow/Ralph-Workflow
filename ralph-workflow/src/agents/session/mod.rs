//! Session model for MCP-style agent-orchestrator communication.
//!
//! This module defines the control plane types for RFC-009 Phase 1:
//! - Session identification and drain identity
//! - Capability vocabulary for typed tool access
//! - Policy flags for session-scoped permissions
//!
//! # Session Model
//!
//! Each agent invocation starts with a session handshake that declares:
//! - run ID
//! - drain identity (planning, development, analysis, review, fix, commit)
//! - protocol version
//! - allowed capability set for this session
//! - session policy flags such as `no_edit`, `allow_shell`, `allow_git_read`

use serde::{Deserialize, Serialize};
use std::fmt;
use std::time::SystemTime;

/// Protocol version for MCP-style communication (v1).
pub const PROTOCOL_VERSION_V1: &str = "ralph-mcp/1.0";

/// Unique identifier for an agent session.
///
/// Combines run ID, drain identity, and a counter to ensure uniqueness
/// across parallel workers and retries.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct AgentSessionId {
    id: String,
}

impl AgentSessionId {
    /// Create a new session ID from run ID, drain, and counter.
    pub fn new(run_id: &str, drain: &SessionDrain, counter: u32) -> Self {
        let id = format!("{}-{}-{}", run_id, drain, counter);
        Self { id }
    }

    /// Returns the string representation of the session ID.
    pub fn as_str(&self) -> &str {
        &self.id
    }
}

impl fmt::Display for AgentSessionId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.id)
    }
}

/// Drain identity for the session.
///
/// Represents the phase of the pipeline this session belongs to.
/// Each drain has different capability and policy implications.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SessionDrain {
    Planning,
    Development,
    Analysis,
    Review,
    Fix,
    Commit,
}

impl SessionDrain {
    /// Returns the string representation of the drain.
    pub fn as_str(&self) -> &'static str {
        match self {
            SessionDrain::Planning => "planning",
            SessionDrain::Development => "development",
            SessionDrain::Analysis => "analysis",
            SessionDrain::Review => "review",
            SessionDrain::Fix => "fix",
            SessionDrain::Commit => "commit",
        }
    }
}

impl fmt::Display for SessionDrain {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Individual capability that can be granted to a session.
///
/// These are the typed capabilities an agent can request.
/// V1 focuses on brokered read-only and bounded execution capabilities.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Capability {
    /// Read files and directories in the workspace.
    WorkspaceRead,
    /// Write ephemeral files that are not tracked by git.
    WorkspaceWriteEphemeral,
    /// Write to files tracked by git.
    WorkspaceWriteTracked,
    /// Execute bounded shell commands with timeout and policy filters.
    ProcessExecBounded,
    /// Submit structured artifacts (plan, issues, development result, etc.).
    ArtifactSubmit,
    /// Report progress and emit structured notes.
    RunReportProgress,
    /// Read git status, diff, log, and show.
    GitStatusRead,
    /// Read git diff (may include diff of uncommitted changes).
    GitDiffRead,
    /// Perform git operations that create or modify history (commit, merge, etc.).
    GitWrite,
    /// Read environment variables and system information.
    EnvRead,
}

impl Capability {
    /// Returns the string identifier for this capability.
    pub fn identifier(&self) -> &'static str {
        match self {
            Capability::WorkspaceRead => "workspace.read",
            Capability::WorkspaceWriteEphemeral => "workspace.write_ephemeral",
            Capability::WorkspaceWriteTracked => "workspace.write_tracked",
            Capability::ProcessExecBounded => "process.exec_bounded",
            Capability::ArtifactSubmit => "artifact.submit",
            Capability::RunReportProgress => "run.report_progress",
            Capability::GitStatusRead => "git.status_read",
            Capability::GitDiffRead => "git.diff_read",
            Capability::GitWrite => "git.write",
            Capability::EnvRead => "env.read",
        }
    }
}

/// Set of capabilities granted to a session.
///
/// Implements a bit-set style storage for efficient containment checks.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct CapabilitySet(u128);

impl CapabilitySet {
    /// Create an empty capability set.
    pub fn new() -> Self {
        Self(0)
    }

    /// Insert a capability into the set.
    pub fn insert(&mut self, cap: Capability) {
        let idx = Self::capability_index(cap);
        self.0 |= 1u128 << idx;
    }

    /// Check if a capability is present in the set.
    pub fn contains(&self, cap: Capability) -> bool {
        let idx = Self::capability_index(cap);
        (self.0 & (1u128 << idx)) != 0
    }

    /// Returns an iterator over all capabilities in this set.
    pub fn iter(&self) -> impl Iterator<Item = Capability> {
        let set = self.0;
        (0..10).filter_map(move |idx| {
            let cap = match idx {
                0 => Capability::WorkspaceRead,
                1 => Capability::WorkspaceWriteEphemeral,
                2 => Capability::WorkspaceWriteTracked,
                3 => Capability::ProcessExecBounded,
                4 => Capability::ArtifactSubmit,
                5 => Capability::RunReportProgress,
                6 => Capability::GitStatusRead,
                7 => Capability::GitDiffRead,
                8 => Capability::GitWrite,
                9 => Capability::EnvRead,
                _ => return None,
            };
            if (set & (1u128 << idx)) != 0 {
                Some(cap)
            } else {
                None
            }
        })
    }

    /// Returns all capabilities in this set as a vector.
    pub fn to_vec(&self) -> Vec<Capability> {
        self.iter().collect()
    }

    fn capability_index(cap: Capability) -> u32 {
        match cap {
            Capability::WorkspaceRead => 0,
            Capability::WorkspaceWriteEphemeral => 1,
            Capability::WorkspaceWriteTracked => 2,
            Capability::ProcessExecBounded => 3,
            Capability::ArtifactSubmit => 4,
            Capability::RunReportProgress => 5,
            Capability::GitStatusRead => 6,
            Capability::GitDiffRead => 7,
            Capability::GitWrite => 8,
            Capability::EnvRead => 9,
        }
    }

    /// Returns the default capability set for a given drain.
    ///
    /// RFC-009 V1: Planning/Analysis/Review = read-only, Development = write-capable,
    /// Fix = write-capable (less restricted), Commit = git-write.
    pub fn defaults_for_drain(drain: SessionDrain) -> Self {
        match drain {
            SessionDrain::Planning | SessionDrain::Analysis | SessionDrain::Review => vec![
                Capability::WorkspaceRead,
                Capability::GitStatusRead,
                Capability::GitDiffRead,
                Capability::ArtifactSubmit,
            ]
            .into(),
            SessionDrain::Development => vec![
                Capability::WorkspaceRead,
                Capability::WorkspaceWriteEphemeral,
                Capability::WorkspaceWriteTracked,
                Capability::GitStatusRead,
                Capability::GitDiffRead,
                Capability::ProcessExecBounded,
                Capability::ArtifactSubmit,
            ]
            .into(),
            SessionDrain::Fix => vec![
                Capability::WorkspaceRead,
                Capability::WorkspaceWriteTracked,
                Capability::GitStatusRead,
                Capability::GitDiffRead,
                Capability::ProcessExecBounded,
                Capability::ArtifactSubmit,
            ]
            .into(),
            SessionDrain::Commit => vec![
                Capability::GitStatusRead,
                Capability::GitDiffRead,
                Capability::GitWrite,
            ]
            .into(),
        }
    }
}

impl From<Vec<Capability>> for CapabilitySet {
    fn from(caps: Vec<Capability>) -> Self {
        let bits = caps.into_iter().fold(0u128, |acc, cap| {
            let idx = match cap {
                Capability::WorkspaceRead => 0,
                Capability::WorkspaceWriteEphemeral => 1,
                Capability::WorkspaceWriteTracked => 2,
                Capability::ProcessExecBounded => 3,
                Capability::ArtifactSubmit => 4,
                Capability::RunReportProgress => 5,
                Capability::GitStatusRead => 6,
                Capability::GitDiffRead => 7,
                Capability::GitWrite => 8,
                Capability::EnvRead => 9,
            };
            acc | (1u128 << idx)
        });
        Self(bits)
    }
}

/// Policy flags that modify session behavior.
///
/// These are restrictive flags that modify how capabilities operate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PolicyFlag {
    /// Session cannot write to tracked files.
    NoEdit,
    /// Session may execute shell commands.
    AllowShell,
    /// Session may read git status and diffs.
    AllowGitRead,
    /// Session may perform git write operations (commit, merge, etc.).
    AllowGitWrite,
    /// Session may spawn parallel workers.
    AllowParallelWorkers,
    /// Session may access network (for API calls, etc.).
    AllowNetwork,
    /// Session may read sensitive environment variables.
    AllowEnvRead,
}

impl PolicyFlag {
    /// Returns the string identifier for this policy flag.
    pub fn identifier(&self) -> &'static str {
        match self {
            PolicyFlag::NoEdit => "no_edit",
            PolicyFlag::AllowShell => "allow_shell",
            PolicyFlag::AllowGitRead => "allow_git_read",
            PolicyFlag::AllowGitWrite => "allow_git_write",
            PolicyFlag::AllowParallelWorkers => "allow_parallel_workers",
            PolicyFlag::AllowNetwork => "allow_network",
            PolicyFlag::AllowEnvRead => "allow_env_read",
        }
    }
}

/// Set of policy flags for a session.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct PolicyFlagSet(u128);

impl PolicyFlagSet {
    /// Create an empty policy flag set.
    pub fn new() -> Self {
        Self(0)
    }

    /// Insert a policy flag into the set.
    pub fn insert(&mut self, flag: PolicyFlag) {
        let idx = Self::flag_index(flag);
        self.0 |= 1u128 << idx;
    }

    /// Check if a policy flag is present in the set.
    pub fn contains(&self, flag: PolicyFlag) -> bool {
        let idx = Self::flag_index(flag);
        (self.0 & (1u128 << idx)) != 0
    }

    /// Returns an iterator over all policy flags in this set.
    pub fn iter(&self) -> impl Iterator<Item = PolicyFlag> {
        let set = self.0;
        (0..7).filter_map(move |idx| {
            let flag = match idx {
                0 => PolicyFlag::NoEdit,
                1 => PolicyFlag::AllowShell,
                2 => PolicyFlag::AllowGitRead,
                3 => PolicyFlag::AllowGitWrite,
                4 => PolicyFlag::AllowParallelWorkers,
                5 => PolicyFlag::AllowNetwork,
                6 => PolicyFlag::AllowEnvRead,
                _ => return None,
            };
            if (set & (1u128 << idx)) != 0 {
                Some(flag)
            } else {
                None
            }
        })
    }

    /// Returns all policy flags in this set as a vector.
    pub fn to_vec(&self) -> Vec<PolicyFlag> {
        self.iter().collect()
    }

    fn flag_index(flag: PolicyFlag) -> u32 {
        match flag {
            PolicyFlag::NoEdit => 0,
            PolicyFlag::AllowShell => 1,
            PolicyFlag::AllowGitRead => 2,
            PolicyFlag::AllowGitWrite => 3,
            PolicyFlag::AllowParallelWorkers => 4,
            PolicyFlag::AllowNetwork => 5,
            PolicyFlag::AllowEnvRead => 6,
        }
    }

    /// Returns the default policy flag set for a given drain.
    ///
    /// RFC-009 V1: Planning/Analysis/Review drains carry NoEdit flag.
    /// Development and Fix drains allow shell execution.
    /// Commit drain allows git write.
    pub fn defaults_for_drain(drain: SessionDrain) -> Self {
        match drain {
            SessionDrain::Planning | SessionDrain::Analysis | SessionDrain::Review => {
                vec![PolicyFlag::NoEdit].into()
            }
            SessionDrain::Development | SessionDrain::Fix => vec![PolicyFlag::AllowShell].into(),
            SessionDrain::Commit => vec![PolicyFlag::AllowGitWrite].into(),
        }
    }
}

impl From<Vec<PolicyFlag>> for PolicyFlagSet {
    fn from(flags: Vec<PolicyFlag>) -> Self {
        let bits = flags.into_iter().fold(0u128, |acc, flag| {
            let idx = match flag {
                PolicyFlag::NoEdit => 0,
                PolicyFlag::AllowShell => 1,
                PolicyFlag::AllowGitRead => 2,
                PolicyFlag::AllowGitWrite => 3,
                PolicyFlag::AllowParallelWorkers => 4,
                PolicyFlag::AllowNetwork => 5,
                PolicyFlag::AllowEnvRead => 6,
            };
            acc | (1u128 << idx)
        });
        Self(bits)
    }
}

// ============================================================================
// RFC-009 V1: Session Handshake, Audit Record, and Audit Trail Types
// ============================================================================

/// Outcome of a policy check for a requested capability or action.
///
/// V1 data model only — policy enforcement gates are future work (Phase 2).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum PolicyOutcome {
    /// The request was approved.
    Approved,
    /// The request was denied by policy.
    Denied { reason: String },
    /// Approved but with a restriction applied.
    ApprovedWithRestriction { restriction: String },
}

/// A single audit record capturing one agent interaction event.
///
/// V1 data model only — recording happens at the boundary handler during
/// agent invocation. The audit trail is logged, not stored in reducer state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditRecord {
    /// Session this record belongs to.
    pub session_id: AgentSessionId,
    /// UTC timestamp of the event (Unix seconds since epoch).
    pub timestamp: u64,
    /// The capability that was exercised or checked.
    pub capability: Capability,
    /// The policy outcome for this interaction.
    pub outcome: PolicyOutcome,
    /// Human-readable description of what was attempted.
    pub description: String,
}

impl AuditRecord {
    /// Create a new audit record.
    pub fn new(
        session_id: AgentSessionId,
        timestamp: u64,
        capability: Capability,
        outcome: PolicyOutcome,
        description: String,
    ) -> Self {
        Self {
            session_id,
            timestamp,
            capability,
            outcome,
            description,
        }
    }
}

/// Ordered audit trail for a session.
///
/// Collects all AuditRecords produced during an agent session.
/// V1 data model: the trail is built during effect handling and logged
/// via the boundary handler's ctx.workspace logging. Not stored in reducer state.
///
/// Construction is immutable — use [`AuditTrail::from_records`] or collect into it.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AuditTrail {
    records: Vec<AuditRecord>,
}

impl AuditTrail {
    /// Create an empty audit trail.
    pub fn new() -> Self {
        Self::default()
    }

    /// Build an audit trail from an iterator of records.
    pub fn from_records(records: impl IntoIterator<Item = AuditRecord>) -> Self {
        Self {
            records: records.into_iter().collect(),
        }
    }

    /// View all records.
    pub fn records(&self) -> &[AuditRecord] {
        &self.records
    }

    /// Number of records.
    pub fn len(&self) -> usize {
        self.records.len()
    }

    /// True when no records have been recorded.
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    /// Record that capabilities were injected into prompt template variables.
    ///
    /// This creates an audit record for each granted capability, documenting
    /// that the session's capabilities were used to generate template variables.
    /// Each record is marked as `PolicyOutcome::Approved` since these are
    /// the capabilities granted to the session.
    ///
    /// # Arguments
    ///
    /// * `session_id` - The session these capabilities belong to
    /// * `timestamp` - Unix timestamp when the injection occurred
    /// * `capabilities` - The granted capabilities to record
    ///
    /// # Example
    ///
    /// ```ignore
    /// let trail = AuditTrail::new();
    /// let updated_trail = trail.record_capability_injection(
    ///     &session.session_id,
    ///     timestamp,
    ///     session.capabilities(),
    /// );
    /// ```
    pub fn record_capability_injection(
        &self,
        session_id: &AgentSessionId,
        timestamp: u64,
        capabilities: &CapabilitySet,
    ) -> AuditTrail {
        let new_records = capabilities.iter().map(|cap| {
            AuditRecord::new(
                session_id.clone(),
                timestamp,
                cap,
                PolicyOutcome::Approved,
                format!(
                    "Capability {} injected into prompt template variables for session {}",
                    cap.identifier(),
                    session_id.as_str()
                ),
            )
        });

        AuditTrail::from_records(self.records.iter().cloned().chain(new_records))
    }
}

/// Session handshake envelope produced before each agent invocation.
///
/// V1 data model: the handshake declares the session's identity, protocol version,
/// capability set, and policy flags. It is the RFC-009 session declaration contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionHandshake {
    /// The session this handshake belongs to.
    pub session_id: AgentSessionId,
    /// The drain (pipeline phase) identity for this session.
    pub drain: SessionDrain,
    /// Protocol version string (e.g. "ralph-mcp/1.0").
    pub protocol_version: String,
    /// Capabilities granted for this session.
    pub capabilities: CapabilitySet,
    /// Policy flags governing this session's behavior.
    pub policy_flags: PolicyFlagSet,
    /// UTC timestamp when the handshake was issued (Unix seconds since epoch).
    pub issued_at: u64,
}

impl SessionHandshake {
    /// Create a handshake from an existing AgentSession.
    pub fn from_session(session: &AgentSession) -> Self {
        let issued_at = session
            .created_at
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        Self {
            session_id: session.session_id.clone(),
            drain: session.drain,
            protocol_version: session.protocol_version.clone(),
            capabilities: session.capabilities.clone(),
            policy_flags: session.policy_flags.clone(),
            issued_at,
        }
    }
}

/// Agent session representing a single agent invocation with its capabilities and policy.
///
/// This is the core session type that encapsulates:
/// - Session identification (run ID, drain, counter)
/// - Protocol version
/// - Capability set granted to this session
/// - Policy flags restricting session behavior
/// - Session creation timestamp
#[derive(Debug, Clone)]
pub struct AgentSession {
    /// Unique session identifier.
    pub session_id: AgentSessionId,
    /// Run identifier for this pipeline execution.
    pub run_id: String,
    /// Drain identity for this session.
    pub drain: SessionDrain,
    /// Protocol version in use.
    pub protocol_version: String,
    /// Capabilities granted to this session.
    pub capabilities: CapabilitySet,
    /// Policy flags restricting this session.
    pub policy_flags: PolicyFlagSet,
    /// When this session was created.
    pub created_at: SystemTime,
}

impl AgentSession {
    /// Create a new agent session.
    pub fn new(
        run_id: String,
        drain: SessionDrain,
        counter: u32,
        capabilities: CapabilitySet,
        policy_flags: PolicyFlagSet,
    ) -> Self {
        Self::new_with_created_at(
            run_id,
            drain,
            counter,
            capabilities,
            policy_flags,
            SystemTime::UNIX_EPOCH,
        )
    }

    /// Create a new agent session with an explicit creation timestamp.
    pub fn new_with_created_at(
        run_id: String,
        drain: SessionDrain,
        counter: u32,
        capabilities: CapabilitySet,
        policy_flags: PolicyFlagSet,
        created_at: SystemTime,
    ) -> Self {
        Self {
            session_id: AgentSessionId::new(&run_id, &drain, counter),
            run_id,
            drain,
            protocol_version: PROTOCOL_VERSION_V1.to_string(),
            capabilities,
            policy_flags,
            created_at,
        }
    }

    /// Create a new session for the given drain with default capabilities.
    ///
    /// RFC-009 V1: Uses drain-specific capability defaults as defined
    /// in `CapabilitySet::defaults_for_drain` and `PolicyFlagSet::defaults_for_drain`.
    pub fn for_drain(run_id: String, drain: SessionDrain, counter: u32) -> Self {
        Self::for_drain_with_created_at(run_id, drain, counter, SystemTime::UNIX_EPOCH)
    }

    /// Create a new session for a drain with defaults and explicit creation timestamp.
    pub fn for_drain_with_created_at(
        run_id: String,
        drain: SessionDrain,
        counter: u32,
        created_at: SystemTime,
    ) -> Self {
        let session_id = AgentSessionId::new(&run_id, &drain, counter);
        let capabilities = CapabilitySet::defaults_for_drain(drain);
        let policy_flags = PolicyFlagSet::defaults_for_drain(drain);
        Self {
            session_id,
            run_id,
            drain,
            protocol_version: PROTOCOL_VERSION_V1.to_string(),
            capabilities,
            policy_flags,
            created_at,
        }
    }

    /// Check if a capability is granted in this session.
    ///
    /// Returns `PolicyOutcome::Approved` if the capability is present,
    /// or `PolicyOutcome::Denied` with a descriptive reason if not.
    ///
    /// This method is the primary interface for Phase 2/3 policy enforcement.
    /// In V1, policy enforcement is not yet active, but this method provides
    /// the infrastructure for future enforcement gates.
    #[must_use]
    pub fn check_capability(&self, requested: Capability) -> PolicyOutcome {
        if self.capabilities.contains(requested) {
            PolicyOutcome::Approved
        } else {
            PolicyOutcome::Denied {
                reason: format!(
                    "Capability {} not granted for {} drain",
                    requested.identifier(),
                    self.drain,
                ),
            }
        }
    }

    /// Get the capabilities for this session.
    ///
    /// Returns a reference to the granted capabilities.
    #[must_use]
    pub fn capabilities(&self) -> &CapabilitySet {
        &self.capabilities
    }

    /// Get the policy flags for this session.
    ///
    /// Returns a reference to the policy flags.
    #[must_use]
    pub fn policy_flags(&self) -> &PolicyFlagSet {
        &self.policy_flags
    }

    /// Get the drain for this session.
    ///
    /// Returns the session drain identity.
    #[must_use]
    pub fn drain(&self) -> SessionDrain {
        self.drain
    }
}

/// Conversion from AgentDrain to SessionDrain for RFC-009 session wiring.
///
/// Both enums have identical variants, so this is a straightforward mapping.
impl From<crate::agents::AgentDrain> for SessionDrain {
    fn from(drain: crate::agents::AgentDrain) -> Self {
        match drain {
            crate::agents::AgentDrain::Planning => SessionDrain::Planning,
            crate::agents::AgentDrain::Development => SessionDrain::Development,
            crate::agents::AgentDrain::Analysis => SessionDrain::Analysis,
            crate::agents::AgentDrain::Review => SessionDrain::Review,
            crate::agents::AgentDrain::Fix => SessionDrain::Fix,
            crate::agents::AgentDrain::Commit => SessionDrain::Commit,
        }
    }
}

#[cfg(test)]
mod tests;
