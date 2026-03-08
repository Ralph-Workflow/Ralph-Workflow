//! Type definitions for parsed development result XML elements.

/// Parsed development result elements from valid XML.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DevelopmentResultElements {
    /// The development status (required)
    /// Valid values: completed, partial, failed
    pub status: String,
    /// Summary of what was done (required)
    pub summary: String,
    /// Optional list of files changed
    pub files_changed: Option<String>,
    /// Optional next steps
    pub next_steps: Option<String>,
}

impl DevelopmentResultElements {
    /// Returns true if the work is completed.
    #[must_use]
    pub fn is_completed(&self) -> bool {
        self.status == "completed"
    }

    /// Returns true if the work is partially done.
    #[must_use]
    pub fn is_partial(&self) -> bool {
        self.status == "partial"
    }

    /// Returns true if the work failed.
    #[must_use]
    pub fn is_failed(&self) -> bool {
        self.status == "failed"
    }
}
