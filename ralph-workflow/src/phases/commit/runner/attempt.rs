// Legacy phase-based code - deprecated in favor of reducer/handler architecture

/// Outcome of commit message generation.
///
/// This is intentionally an enum so callers must handle skip explicitly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitMessageOutcome {
    /// A normal commit message ready to be written to `commit-message.txt`.
    Message(String),
    /// The agent determined there are no changes to commit.
    Skipped { reason: String },
}

/// Result of commit message generation.
#[derive(Debug)]
pub struct CommitMessageResult {
    pub outcome: CommitMessageOutcome,
}
