/// The level of truncation applied to a diff for review.
///
/// This enum tracks how much a diff has been abbreviated and determines
/// what instructions should be given to the reviewer agent.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DiffTruncationLevel {
    /// No truncation - full diff is included
    #[default]
    Full,
    /// Diff was semantically truncated - high-priority files shown, instruction to explore
    Abbreviated,
    /// Only file paths listed - instruction to explore each file's diff
    FileList,
    /// File list was abbreviated - instruction to explore and discover files
    FileListAbbreviated,
}

/// The result of diff truncation for review purposes.
///
/// Contains both the potentially-truncated content and metadata about
/// what truncation was applied, along with version context information.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiffReviewContent {
    /// The content to include in the review prompt
    pub content: String,
    /// The level of truncation applied
    pub truncation_level: DiffTruncationLevel,
    /// Total number of files in the full diff (for context in messages)
    pub total_file_count: usize,
    /// Number of files shown in the abbreviated content (if applicable)
    pub shown_file_count: Option<usize>,
    /// The OID (commit SHA) that this diff is compared against (baseline)
    pub baseline_oid: Option<String>,
    /// Short form (first 8 chars) of the baseline OID for display
    pub baseline_short: Option<String>,
    /// Description of what the baseline represents (e.g., "`review_baseline`", "`start_commit`")
    pub baseline_description: String,
}

impl DiffReviewContent {
    /// Generate a human-readable header describing the diff's version context.
    ///
    /// This header is meant to be included at the beginning of the diff content
    /// to provide clarity about what state of the code the diff represents.
    ///
    /// # Returns
    ///
    /// A formatted string like:
    /// ```text
    /// Diff Context: Compared against review_baseline abc12345
    /// Current state: Working directory (includes unstaged changes)
    /// ```
    ///
    /// If no baseline information is available, returns a generic message.
    #[must_use]
    pub fn format_context_header(&self) -> String {
        let baseline_line = self
            .baseline_short
            .as_ref()
            .map(|short| {
                format!(
                    "Diff Context: Compared against {} {}",
                    self.baseline_description, short
                )
            })
            .unwrap_or_else(|| "Diff Context: Version information not available".to_string());

        let truncation_line = match self.truncation_level {
            DiffTruncationLevel::Full => None,
            DiffTruncationLevel::Abbreviated => Some(format!(
                "Note: Diff abbreviated - {}/{} files shown",
                self.shown_file_count.unwrap_or(0),
                self.total_file_count
            )),
            DiffTruncationLevel::FileList => Some(format!(
                "Note: Only file list shown - {} files changed",
                self.total_file_count
            )),
            DiffTruncationLevel::FileListAbbreviated => Some(format!(
                "Note: File list abbreviated - {}/{} files shown",
                self.shown_file_count.unwrap_or(0),
                self.total_file_count
            )),
        };

        let lines: Vec<String> = [Some(baseline_line), truncation_line]
            .into_iter()
            .flatten()
            .collect();

        if lines.is_empty() {
            String::new()
        } else {
            format!("{}\n", lines.join("\n"))
        }
    }
}
