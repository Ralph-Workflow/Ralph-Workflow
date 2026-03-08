/// Rebase state tracking.
///
/// Tracks the state of rebase operations to enable
/// proper recovery from interruptions during rebase.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub enum RebaseState {
    /// Rebase not started yet
    #[default]
    NotStarted,
    /// Pre-development rebase in progress
    PreRebaseInProgress { upstream_branch: String },
    /// Pre-development rebase completed
    PreRebaseCompleted { commit_oid: String },
    /// Post-review rebase in progress
    PostRebaseInProgress { upstream_branch: String },
    /// Post-review rebase completed
    PostRebaseCompleted { commit_oid: String },
    /// Rebase has conflicts that need resolution
    HasConflicts { files: Vec<String> },
    /// Rebase failed
    Failed { error: String },
}

/// Pipeline phases for checkpoint tracking.
///
/// These phases represent the major stages of the Ralph pipeline.
/// Checkpoints are saved at phase boundaries to enable resume functionality.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum PipelinePhase {
    /// Rebase phase (synchronizing with upstream branch)
    Rebase,
    /// Planning phase (creating PLAN.md)
    Planning,
    /// Development/implementation phase
    Development,
    /// Review-fix cycles phase (N iterations of review + fix)
    Review,
    /// Commit message generation
    CommitMessage,
    /// Final validation phase
    FinalValidation,
    /// Pipeline complete
    Complete,
    /// Before initial rebase
    PreRebase,
    /// During pre-rebase conflict resolution
    PreRebaseConflict,
    /// Before post-review rebase
    PostRebase,
    /// During post-review conflict resolution
    PostRebaseConflict,
    /// Awaiting development agent to fix pipeline failure
    AwaitingDevFix,
    /// Pipeline was interrupted (e.g., by Ctrl+C)
    Interrupted,
}

impl<'de> Deserialize<'de> for PipelinePhase {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct PhaseVisitor;

        impl Visitor<'_> for PhaseVisitor {
            type Value = PipelinePhase;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a valid pipeline phase")
            }

            fn visit_str<E>(self, value: &str) -> Result<PipelinePhase, E>
            where
                E: de::Error,
            {
                match value {
                    "Rebase" => Ok(PipelinePhase::Rebase),
                    "Planning" => Ok(PipelinePhase::Planning),
                    "Development" => Ok(PipelinePhase::Development),
                    "Review" => Ok(PipelinePhase::Review),
                    "CommitMessage" => Ok(PipelinePhase::CommitMessage),
                    "FinalValidation" => Ok(PipelinePhase::FinalValidation),
                    "Complete" => Ok(PipelinePhase::Complete),
                    "PreRebase" => Ok(PipelinePhase::PreRebase),
                    "PreRebaseConflict" => Ok(PipelinePhase::PreRebaseConflict),
                    "PostRebase" => Ok(PipelinePhase::PostRebase),
                    "PostRebaseConflict" => Ok(PipelinePhase::PostRebaseConflict),
                    "AwaitingDevFix" => Ok(PipelinePhase::AwaitingDevFix),
                    "Interrupted" => Ok(PipelinePhase::Interrupted),
                    // Legacy phases are no longer supported - reject with clear error
                    "Fix" | "ReviewAgain" => Err(E::custom(format!(
                        "Legacy phase '{value}' is no longer supported. \
                         Delete .agent/checkpoint.json and start a fresh pipeline run."
                    ))),
                    _ => Err(E::unknown_variant(
                        value,
                        &[
                            "Rebase",
                            "Planning",
                            "Development",
                            "Review",
                            "CommitMessage",
                            "FinalValidation",
                            "Complete",
                            "PreRebase",
                            "PreRebaseConflict",
                            "PostRebase",
                            "PostRebaseConflict",
                            "AwaitingDevFix",
                            "Interrupted",
                        ],
                    )),
                }
            }
        }

        deserializer.deserialize_str(PhaseVisitor)
    }
}

impl std::fmt::Display for PipelinePhase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Rebase => write!(f, "Rebase"),
            Self::Planning => write!(f, "Planning"),
            Self::Development => write!(f, "Development"),
            Self::Review => write!(f, "Review"),
            Self::CommitMessage => write!(f, "Commit Message Generation"),
            Self::FinalValidation => write!(f, "Final Validation"),
            Self::Complete => write!(f, "Complete"),
            Self::PreRebase => write!(f, "Pre-Rebase"),
            Self::PreRebaseConflict => write!(f, "Pre-Rebase Conflict"),
            Self::PostRebase => write!(f, "Post-Rebase"),
            Self::PostRebaseConflict => write!(f, "Post-Rebase Conflict"),
            Self::AwaitingDevFix => write!(f, "Awaiting Dev Fix"),
            Self::Interrupted => write!(f, "Interrupted"),
        }
    }
}
