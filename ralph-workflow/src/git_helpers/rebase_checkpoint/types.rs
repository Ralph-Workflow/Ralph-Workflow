// Checkpoint type definitions for rebase operations.
//
// This file contains RebasePhase enum and RebaseCheckpoint struct
// with their implementations.

/// Phase of a rebase operation.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RebasePhase {
    /// Rebase has not started.
    NotStarted,
    /// Pre-rebase validation in progress.
    PreRebaseCheck,
    /// Rebase operation is in progress.
    RebaseInProgress,
    /// Conflicts detected, awaiting resolution.
    ConflictDetected,
    /// Conflict resolution in progress.
    ConflictResolutionInProgress,
    /// Completing rebase after conflict resolution.
    CompletingRebase,
    /// Rebase completed successfully.
    RebaseComplete,
    /// Rebase was aborted.
    RebaseAborted,
}

impl RebasePhase {
    /// Get the maximum number of recovery attempts allowed for this phase.
    ///
    /// Different phases have different recovery limits:
    /// - `ConflictResolutionInProgress`: Higher limit (5) - conflicts may need multiple AI attempts
    /// - `ConflictDetected`: Medium limit (3) - waiting for AI to process
    /// - `RebaseInProgress`: Lower limit (2) - transient Git issues
    /// - `CompletingRebase`: Lower limit (2) - final stages should be quick
    /// - `PreRebaseCheck`: Low limit (1) - validation should pass immediately
    /// - Other phases: Default limit (3)
    ///
    /// # Returns
    ///
    /// The maximum number of recovery attempts for this phase.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn max_recovery_attempts(&self) -> u32 {
        match self {
            Self::ConflictResolutionInProgress => 5,
            Self::RebaseInProgress | Self::CompletingRebase => 2,
            Self::PreRebaseCheck => 1,
            Self::ConflictDetected
            | Self::NotStarted
            | Self::RebaseComplete
            | Self::RebaseAborted => 3,
        }
    }
}

/// Checkpoint data for rebase operations.
///
/// This structure contains all the information needed to resume
/// a rebase operation after an interruption.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RebaseCheckpoint {
    /// Current phase of the rebase.
    pub phase: RebasePhase,
    /// Upstream branch being rebased onto.
    pub upstream_branch: String,
    /// Files with conflicts.
    pub conflicted_files: Vec<String>,
    /// Files that have been resolved.
    pub resolved_files: Vec<String>,
    /// Number of errors encountered.
    pub error_count: u32,
    /// Last error message.
    pub last_error: Option<String>,
    /// Timestamp of checkpoint.
    pub timestamp: String,
    /// Number of errors encountered in the current phase.
    #[serde(default)]
    pub phase_error_count: u32,
}

impl Default for RebaseCheckpoint {
    fn default() -> Self {
        Self {
            phase: RebasePhase::NotStarted,
            upstream_branch: String::new(),
            conflicted_files: Vec::new(),
            resolved_files: Vec::new(),
            error_count: 0,
            last_error: None,
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase_error_count: 0,
        }
    }
}

impl RebaseCheckpoint {
    /// Create a new rebase checkpoint.
    #[must_use]
    pub fn new(upstream_branch: String) -> Self {
        Self {
            phase: RebasePhase::NotStarted,
            upstream_branch,
            conflicted_files: Vec::new(),
            resolved_files: Vec::new(),
            error_count: 0,
            last_error: None,
            timestamp: chrono::Utc::now().to_rfc3339(),
            phase_error_count: 0,
        }
    }

    /// Set the phase of the rebase.
    ///
    /// Resets the phase error count when transitioning to a new phase.
    #[must_use]
    pub fn with_phase(mut self, phase: RebasePhase) -> Self {
        // Reset phase error count when transitioning to a new phase
        if self.phase != phase {
            self.phase_error_count = 0;
        }
        self.phase = phase;
        self.timestamp = chrono::Utc::now().to_rfc3339();
        self
    }

    /// Add a conflicted file.
    #[must_use]
    pub fn with_conflicted_file(mut self, file: String) -> Self {
        if !self.conflicted_files.contains(&file) {
            self.conflicted_files.push(file);
        }
        self
    }

    /// Add a resolved file.
    #[must_use]
    pub fn with_resolved_file(mut self, file: String) -> Self {
        if !self.resolved_files.contains(&file) {
            self.resolved_files.push(file);
        }
        self
    }

    /// Add an error.
    ///
    /// Increments both the global error count and the phase-specific error count.
    #[must_use]
    pub fn with_error(mut self, error: String) -> Self {
        self.error_count = self.error_count.saturating_add(1);
        self.phase_error_count = self.phase_error_count.saturating_add(1);
        self.last_error = Some(error);
        self.timestamp = chrono::Utc::now().to_rfc3339();
        self
    }

    /// Check if all conflicts are resolved.
    #[must_use]
    pub fn all_conflicts_resolved(&self) -> bool {
        self.conflicted_files
            .iter()
            .all(|f| self.resolved_files.contains(f))
    }

    /// Get the number of unresolved conflicts.
    #[must_use]
    pub fn unresolved_conflict_count(&self) -> usize {
        self.conflicted_files
            .iter()
            .filter(|f| !self.resolved_files.contains(f))
            .count()
    }
}
