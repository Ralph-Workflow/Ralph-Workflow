// State enums and basic types.
//
// Contains ArtifactType, PromptMode, DevelopmentStatus, FixStatus, RebaseState, CommitState.

/// Artifact type being processed by the current phase.
///
/// Used to track which XML artifact type is currently being processed,
/// enabling role-specific error messages.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ArtifactType {
    /// Plan XML from planning phase.
    Plan,
    /// `DevelopmentResult` XML from development phase.
    DevelopmentResult,
    /// Issues XML from review phase.
    Issues,
    /// `FixResult` XML from fix phase.
    FixResult,
    /// `CommitMessage` XML from commit message generation.
    CommitMessage,
}

/// Prompt rendering mode chosen by the reducer.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PromptMode {
    /// Standard prompt rendering.
    Normal,
    /// Continuation prompt rendering for partial/failed outputs.
    Continuation,
    /// Same-agent retry prompt rendering for transient invocation failures.
    ///
    /// Used for timeouts and internal/unknown errors where we want to retry the
    /// same agent first with additional guidance (reduce scope, chunk work, etc.).
    SameAgentRetry,
}

/// Reason a same-agent retry is pending.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SameAgentRetryReason {
    /// The agent invocation timed out with no meaningful output.
    ///
    /// This is the legacy timeout behavior - treated like other same-agent retries.
    Timeout,
    /// The agent invocation timed out with meaningful partial output.
    ///
    /// Context should be preserved for the retry. If the agent supports session IDs,
    /// the session is reused. If not, the prior context is extracted from the logfile
    /// and written to a temp file for the retry prompt.
    TimeoutWithContext,
    /// The agent invocation failed with an internal/unknown error.
    InternalError,
    /// The agent invocation failed with a non-auth, non-rate-limit, non-timeout error.
    ///
    /// This is a catch-all category used to ensure immediate agent fallback only happens
    /// for rate limit (429) and authentication failures.
    Other,
}

impl std::fmt::Display for ArtifactType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Plan => write!(f, "plan"),
            Self::DevelopmentResult => write!(f, "development_result"),
            Self::Issues => write!(f, "issues"),
            Self::FixResult => write!(f, "fix_result"),
            Self::CommitMessage => write!(f, "commit_message"),
        }
    }
}

/// Development status from agent output.
///
/// These values map to the `<ralph-status>` element in `development_result.xml`.
/// Used to track whether work is complete or needs continuation.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DevelopmentStatus {
    /// Work completed successfully - no continuation needed.
    Completed,
    /// Work partially done - needs continuation.
    Partial,
    /// Work failed - needs continuation with different approach.
    Failed,
}

// Re-export typed analysis decision enums from policy crate.
// These replace the old 5-variant AnalysisDecision with separate enums for
// development analysis (NeedsMoreWork, CycleComplete) and review analysis
// (NeedsMoreFix, CycleComplete).
pub use ralph_workflow_policy::{DevelopmentAnalysisDecision, ReviewAnalysisDecision};

/// Fix status from agent output.
///
/// These values map to the `<ralph-status>` element in `fix_result.xml`.
/// Used to track whether fix work is complete or needs continuation.
///
/// # Continuation Semantics
///
/// - `AllIssuesAddressed`: Complete, no continuation needed
/// - `NoIssuesFound`: Complete, no continuation needed
/// - `IssuesRemain`: Work incomplete, needs continuation
/// - `Failed`: Fix attempt failed, needs continuation with different approach
#[derive(Copy, Clone, Debug, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum FixStatus {
    /// All issues have been addressed - no continuation needed.
    #[default]
    AllIssuesAddressed,
    /// Issues remain - needs continuation.
    IssuesRemain,
    /// No issues were found - nothing to fix.
    NoIssuesFound,
    /// Fix attempt failed - needs continuation with different approach.
    ///
    /// This status indicates the fix attempt produced valid XML output but
    /// the agent could not fix the issues (e.g., blocked by external factors,
    /// needs different strategy). Triggers continuation like `IssuesRemain`.
    Failed,
}

impl std::fmt::Display for DevelopmentStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Completed => write!(f, "completed"),
            Self::Partial => write!(f, "partial"),
            Self::Failed => write!(f, "failed"),
        }
    }
}

impl std::fmt::Display for FixStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::AllIssuesAddressed => write!(f, "all_issues_addressed"),
            Self::IssuesRemain => write!(f, "issues_remain"),
            Self::NoIssuesFound => write!(f, "no_issues_found"),
            Self::Failed => write!(f, "failed"),
        }
    }
}

impl FixStatus {
    /// Parse a fix status string from XML.
    ///
    /// This is intentionally not implementing `std::str::FromStr` because it returns
    /// `Option<Self>` for easier handling of unknown values without error types.
    #[must_use] 
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "all_issues_addressed" => Some(Self::AllIssuesAddressed),
            "issues_remain" => Some(Self::IssuesRemain),
            "no_issues_found" => Some(Self::NoIssuesFound),
            "failed" => Some(Self::Failed),
            _ => None,
        }
    }

    /// Returns true if the fix is complete (no more work needed).
    #[must_use] 
    pub const fn is_complete(&self) -> bool {
        matches!(self, Self::AllIssuesAddressed | Self::NoIssuesFound)
    }

    /// Returns true if continuation is needed (incomplete work or failure).
    ///
    /// Both `IssuesRemain` and `Failed` trigger continuation:
    /// - `IssuesRemain`: Some issues fixed, others remain
    /// - `Failed`: Fix attempt failed, needs different approach
    #[must_use] 
    pub const fn needs_continuation(&self) -> bool {
        matches!(self, Self::IssuesRemain | Self::Failed)
    }
}

/// Rebase operation state.
///
/// Tracks rebase progress through the state machine:
/// `NotStarted` → `InProgress` → Conflicted → Completed/Skipped
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum RebaseState {
    NotStarted,
    InProgress {
        original_head: String,
        target_branch: String,
    },
    Conflicted {
        original_head: String,
        target_branch: String,
        files: Vec<PathBuf>,
        resolution_attempts: u32,
    },
    Completed {
        new_head: String,
    },
    Skipped,
}

impl RebaseState {
    #[doc(hidden)]
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_terminal(&self) -> bool {
        matches!(self, Self::Completed { .. } | Self::Skipped)
    }

    #[must_use] 
    pub fn current_head(&self) -> Option<String> {
        match self {
            Self::InProgress { original_head, .. } => Some(original_head.clone()),
            Self::NotStarted | Self::Skipped | Self::Conflicted { .. } => None,
            Self::Completed { new_head } => Some(new_head.clone()),
        }
    }

    #[doc(hidden)]
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_in_progress(&self) -> bool {
        matches!(
            self,
            Self::InProgress { .. } | Self::Conflicted { .. }
        )
    }
}

/// Maximum number of retry attempts when XML/format validation fails.
///
/// This applies across the pipeline for:
/// - Commit message generation validation failures
/// - Plan generation validation failures
/// - Development output validation failures
/// - Review output validation failures
///
/// When an agent produces output that fails XML parsing or format validation,
/// we retry with corrective prompts up to this many times before giving up.
pub const MAX_VALIDATION_RETRY_ATTEMPTS: u32 = 100;

/// Commit generation state.
///
/// Tracks commit message generation progress through retries:
/// `NotStarted` → Generating → Generated → Committed/Skipped
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum CommitState {
    NotStarted,
    Generating { attempt: u32, max_attempts: u32 },
    Generated { message: String },
    Committed { hash: String },
    Skipped,
}

impl CommitState {
    #[doc(hidden)]
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_terminal(&self) -> bool {
        matches!(self, Self::Committed { .. } | Self::Skipped)
    }
}

/// Kind of prompt input that may require oversize handling.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PromptInputKind {
    Prompt,
    Plan,
    Diff,
    LastOutput,
}

/// How an input is represented to downstream prompt templates.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PromptInputRepresentation {
    /// Input is embedded inline in the prompt template.
    Inline,
    /// Input is referenced by a workspace-relative file path.
    ///
    /// Important: this path is serialized into checkpoints. Storing absolute paths
    /// would leak local filesystem layout and can break resuming a run from a
    /// different checkout location.
    FileReference {
        /// Workspace-relative path to the materialized artifact.
        path: PathBuf,
    },
}

/// Reason an input was materialized in a non-default way.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PromptMaterializationReason {
    /// Input was within all configured budgets (no oversize handling required).
    WithinBudgets,
    /// Input exceeded the inline-embedding budget and must be referenced by file.
    InlineBudgetExceeded,
    /// Input exceeded the model-context budget and was truncated before use.
    ModelBudgetExceeded,
    /// Input was referenced even though it was within budgets (explicit policy).
    PolicyForcedReference,
}

/// Canonical, reducer-visible record of prompt input materialization.
///
/// This records what the downstream prompt template will embed (inline vs file
/// reference), along with stable identifiers so the reducer can dedupe repeated
/// attempts in the event loop.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MaterializedPromptInput {
    pub kind: PromptInputKind,
    pub content_id_sha256: String,
    pub consumer_signature_sha256: String,
    pub original_bytes: u64,
    pub final_bytes: u64,
    #[serde(default)]
    pub model_budget_bytes: Option<u64>,
    #[serde(default)]
    pub inline_budget_bytes: Option<u64>,
    pub representation: PromptInputRepresentation,
    pub reason: PromptMaterializationReason,
}
