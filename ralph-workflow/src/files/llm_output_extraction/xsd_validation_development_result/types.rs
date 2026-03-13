//! Type definitions for parsed development result XML elements.

/// Parsed development result elements from valid XML.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DevelopmentResultElements {
    /// The development status (required).
    ///
    /// This field always contains a canonical, normalized status value. The validator
    /// applies tolerant parsing (see `xml_helpers::tolerant_parsing::normalize_enum_value`)
    /// before storing the status, so this field is guaranteed to be one of the canonical
    /// values: `"completed"`, `"partial"`, or `"failed"`.
    ///
    /// Downstream consumers can safely use exact string comparison (e.g., `== "completed"`)
    /// without needing to handle synonym values or case variations.
    pub status: String,
    /// Summary of what was done (required)
    pub summary: String,
    /// Optional list of files changed
    pub files_changed: Option<String>,
    /// Whether the files changed element was present, even if empty.
    pub files_changed_present: bool,
    /// Optional next steps
    pub next_steps: Option<String>,
    /// Whether the next steps element was present, even if empty.
    pub next_steps_present: bool,
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
