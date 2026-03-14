//! Commit generation events.
//!
//! Events related to commit message generation, validation, and creation.

use serde::{Deserialize, Serialize};

/// Commit generation events.
///
/// Events related to commit message generation, validation, and creation.
/// Commit generation occurs after development iterations and review fixes.
///
/// # State Machine
///
/// ```text
/// NotStarted -> Generating -> Generated -> Committed
///                    |              |
///                    +--> (retry) --+
///                    |
///                    +--> Skipped
/// ```
///
/// # Emitted By
///
/// - Commit generation handlers in `handler/commit/`
/// - Commit message validation handlers
/// - Git commit handlers
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum CommitEvent {
    /// Commit message generation started.
    GenerationStarted,
    /// Commit diff computed for commit generation.
    ///
    /// Emitted after preparing the diff that will be committed. The reducer
    /// uses the `empty` flag to decide whether to skip commit creation.
    DiffPrepared {
        /// True when the diff is empty.
        empty: bool,
        /// Content identifier (sha256 hex) of the prepared diff content.
        ///
        /// This is used to guard against reusing stale materialized inputs when the
        /// diff content changes across checkpoints or retries.
        content_id_sha256: String,
    },
    /// Commit diff computation failed.
    DiffFailed {
        /// The error message for the diff failure.
        error: String,
    },
    /// Commit diff is no longer available and must be recomputed.
    ///
    /// This is used for recoverability when `.agent/tmp` artifacts are cleaned between
    /// checkpoints or when required diff files go missing during resume.
    DiffInvalidated {
        /// Reason for invalidation.
        reason: String,
    },
    /// Commit prompt prepared for a commit attempt.
    PromptPrepared {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit agent invoked for a commit attempt.
    AgentInvoked {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message XML extracted for a commit attempt.
    CommitXmlExtracted {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message XML missing for a commit attempt.
    CommitXmlMissing {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message XML validated successfully.
    CommitXmlValidated {
        /// The generated commit message.
        message: String,
        /// The attempt number.
        attempt: u32,
        /// Optional list of files to selectively commit.
        ///
        /// Empty means commit all changed files.
        /// Defaults to empty for backward compatibility with old checkpoints.
        #[serde(default)]
        files: Vec<String>,
        /// Files excluded from this commit with documented reasons.
        ///
        /// Audit/observability metadata only — does not affect commit execution.
        /// Defaults to empty for backward compatibility with old checkpoints.
        #[serde(default)]
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    },
    /// Commit message XML validation failed.
    CommitXmlValidationFailed {
        /// The reason for validation failure.
        reason: String,
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message XML archived.
    CommitXmlArchived {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message XML cleaned before invoking the commit agent.
    CommitXmlCleaned {
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message was generated.
    MessageGenerated {
        /// The generated commit message.
        message: String,
        /// The attempt number.
        attempt: u32,
    },
    /// Commit message validation failed.
    MessageValidationFailed {
        /// The reason for validation failure.
        reason: String,
        /// The attempt number that failed.
        attempt: u32,
    },
    /// Commit was created successfully.
    Created {
        /// The commit hash.
        hash: String,
        /// The commit message used.
        message: String,
    },

    // === Cloud mode git remote operations (emitted only when cloud mode is enabled) ===
    /// Git authentication configured successfully for remote operations.
    GitAuthConfigured,

    /// Push to remote completed successfully.
    PushCompleted {
        remote: String,
        branch: String,
        commit_sha: String,
    },

    /// Push to remote failed.
    PushFailed {
        remote: String,
        branch: String,
        error: String,
    },

    /// Pull request created successfully.
    PullRequestCreated { url: String, number: u32 },

    /// Pull request creation failed.
    PullRequestFailed { error: String },
    /// Commit generation failed completely.
    GenerationFailed {
        /// The reason for failure.
        reason: String,
    },
    /// Commit was skipped (e.g., no changes to commit).
    Skipped {
        /// The reason for skipping.
        reason: String,
    },

    /// Pre-termination commit safety check completed successfully.
    ///
    /// Emitted after `Effect::CheckUncommittedChangesBeforeTermination` when the
    /// working directory is clean, allowing termination to proceed.
    PreTerminationSafetyCheckPassed,

    /// Pre-termination commit safety check detected uncommitted changes.
    ///
    /// This is not a terminal error: the reducer must route back through the
    /// commit phase so the changes are committed (or explicitly skipped).
    PreTerminationUncommittedChangesDetected {
        /// Number of lines in `git status --porcelain` output.
        file_count: usize,
    },

    /// Residual uncommitted files detected after a selective commit pass.
    ///
    /// When `pass` is below the configured retry limit, triggers the next automatic
    /// commit retry pass. When `pass` reaches the final retry pass, the files are
    /// carried forward to the next cycle.
    ResidualFilesFound {
        /// Repo-relative paths of remaining dirty files.
        files: Vec<String>,
        /// Which pass just completed (1 = first pass, 2 = second pass).
        pass: u8,
    },

    /// No residual uncommitted files detected after a commit pass.
    ///
    /// Pipeline may proceed normally; working tree is clean.
    ResidualFilesNone,
}
