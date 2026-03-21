//! Types for XSD validation of commit message XML format.

use crate::reducer::state::pipeline::ExcludedFile;

/// Parsed commit message elements from valid XML.
///
/// This struct contains all the elements that were successfully
/// extracted and validated from the XML content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitMessageElements {
    /// The commit subject line (required)
    /// Format: type(scope): description
    pub subject: String,
    /// Optional simple body content (mutually exclusive with detailed elements)
    pub body: Option<String>,
    /// Optional body summary (for detailed format)
    pub body_summary: Option<String>,
    /// Optional body details (for detailed format)
    pub body_details: Option<String>,
    /// Optional body footer (for detailed format)
    pub body_footer: Option<String>,
    /// Optional skip reason (mutually exclusive with commit message)
    /// When present, indicates AI determined no commit is needed
    pub skip_reason: Option<String>,
    /// Optional list of files to selectively stage for this commit.
    ///
    /// When empty (the default), all changed files are committed.
    /// When non-empty, only the listed paths are staged.
    pub files: Vec<String>,
    /// Files excluded from this commit with documented reasons.
    ///
    /// Populated from `<ralph-excluded-files>` in the commit XML.
    /// Audit/observability only — does not affect commit execution.
    pub excluded_files: Vec<ExcludedFile>,
}

impl CommitMessageElements {
    /// Format all body elements into a single body string.
    ///
    /// Combines the simple body or detailed elements into a formatted
    /// commit message body string suitable for git commit.
    pub(crate) fn format_body(&self) -> String {
        // If simple body exists, use it directly
        if let Some(ref body) = self.body {
            return body.clone();
        }

        // Otherwise, combine detailed elements
        let parts: Vec<&str> = [
            self.body_summary.as_deref(),
            self.body_details.as_deref(),
            self.body_footer.as_deref(),
        ]
        .into_iter()
        .flatten()
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect();

        if parts.is_empty() {
            String::new()
        } else {
            parts.join("\n\n")
        }
    }
}
