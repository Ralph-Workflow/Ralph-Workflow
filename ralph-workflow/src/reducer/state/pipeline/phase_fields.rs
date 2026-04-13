// Phase-specific validated outcome types.
//
// These structures capture the validated results from each pipeline phase
// after XML parsing and schema validation. They represent the contract
// between agent output and reducer state.

/// Reason why a file was excluded from a selective commit.
///
/// Used in `<ralph-excluded-files>` XML elements to record why the commit
/// agent chose to omit a file. This metadata is audit/observability only
/// and does not change commit execution semantics.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ExcludedFileReason {
    /// File is a Ralph-internal artifact (logs, tmp outputs).
    ///
    /// This is audit/observability metadata only; commit execution ignores it.
    /// A separate, deterministic artifact-ignore mechanism may use this reason
    /// to manage a Ralph-local ignore layer (e.g., `.git/info/exclude`).
    InternalIgnore,
    /// File is unrelated to the current task and is intentionally deferred.
    NotTaskRelated,
    /// File contains sensitive content that must not be committed.
    Sensitive,
    /// File could not be committed in this pass; carried forward to the next cycle.
    Deferred,
}

/// A single file that was excluded from a selective commit, with a reason.
///
/// Populated from `<ralph-excluded-file reason="...">path</ralph-excluded-file>`
/// elements inside `<ralph-excluded-files>` in the commit XML output.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExcludedFile {
    /// Repo-relative path of the excluded file.
    pub path: String,
    /// Why this file was excluded from the commit.
    pub reason: ExcludedFileReason,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReviewValidatedOutcome {
    pub pass: u32,
    pub issues_found: bool,
    pub clean_no_issues: bool,
    /// Issues found during review. `Box<[String]>` saves 8 bytes per instance
    /// vs `Vec<String>` (no separate capacity field) since this collection
    /// never grows after construction.
    #[serde(default)]
    pub issues: Box<[String]>,
    #[serde(default)]
    pub no_issues_found: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PlanningValidatedOutcome {
    pub iteration: u32,
    pub valid: bool,
    #[serde(default)]
    pub markdown: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct DevelopmentValidatedOutcome {
    pub iteration: u32,
    pub status: DevelopmentStatus,
    /// Explicit analysis decision from the artifact's `decision` field.
    ///
    /// When `Some`, this overrides the status-derived routing in the reducer.
    /// When `None` (absent from the artifact or pre-Phase-2 artifact), the
    /// reducer falls back to deriving the decision from `status`.
    ///
    /// Default is `None` for backwards compatibility with checkpoints that
    /// predate the `decision` field.
    #[serde(default)]
    pub analysis_decision: Option<crate::reducer::state::DevelopmentAnalysisDecision>,
    pub summary: String,
    /// Files changed during development. `Option<Box<[String]>>` saves 8 bytes
    /// per instance vs `Option<Vec<String>>` when Some, and is None when empty
    /// to avoid allocation entirely.
    pub files_changed: Option<Box<[String]>>,
    pub next_steps: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct FixValidatedOutcome {
    pub pass: u32,
    pub status: FixStatus,
    pub summary: Option<String>,
    /// Phase 2: typed review-cycle analysis decision from the fix analysis agent.
    ///
    /// Uses `ReviewAnalysisDecision` which routes within the review cycle:
    /// - `NeedsMoreFix` → return to fix agent
    /// - `CycleComplete` → proceed to review_commit
    ///
    /// `None` means no explicit decision — use status-based continuation logic.
    #[serde(default)]
    pub analysis_decision: Option<crate::reducer::state::ReviewAnalysisDecision>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CommitValidatedOutcome {
    pub attempt: u32,
    pub message: Option<String>,
    pub reason: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
pub struct PromptInputsState {
    #[serde(default)]
    pub planning: Option<MaterializedPlanningInputs>,
    #[serde(default)]
    pub development: Option<MaterializedDevelopmentInputs>,
    #[serde(default)]
    pub review: Option<MaterializedReviewInputs>,
    #[serde(default)]
    pub commit: Option<MaterializedCommitInputs>,
}

impl PromptInputsState {
    /// Clear commit inputs without cloning other fields.
    /// Uses consuming builder pattern for zero-cost state updates.
    #[must_use]
    pub fn with_commit_cleared(self) -> Self {
        Self {
            commit: None,
            ..self
        }
    }

    /// Clear planning inputs without cloning other fields.
    #[must_use]
    pub fn with_planning_cleared(self) -> Self {
        Self {
            planning: None,
            ..self
        }
    }

    /// Clear development inputs without cloning other fields.
    #[must_use]
    pub fn with_development_cleared(self) -> Self {
        Self {
            development: None,
            ..self
        }
    }

    /// Clear review inputs without cloning other fields.
    #[must_use]
    pub fn with_review_cleared(self) -> Self {
        Self {
            review: None,
            ..self
        }
    }

}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct MaterializedPlanningInputs {
    pub iteration: u32,
    pub prompt: MaterializedPromptInput,
}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct MaterializedDevelopmentInputs {
    pub iteration: u32,
    pub prompt: MaterializedPromptInput,
    pub plan: MaterializedPromptInput,
}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct MaterializedReviewInputs {
    pub pass: u32,
    pub plan: MaterializedPromptInput,
    pub diff: MaterializedPromptInput,
}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct MaterializedCommitInputs {
    pub attempt: u32,
    pub diff: MaterializedPromptInput,
}

