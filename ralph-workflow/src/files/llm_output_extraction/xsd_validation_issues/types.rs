//! Types for representing parsed issues XML.
//!
//! This module defines the `IssuesElements` type, which represents the parsed
//! result of validating issues XML content.

use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;

/// A single issue entry with optional skills-mcp recommendations.
///
/// Each issue in the `<ralph-issues>` XML can optionally contain a `<skills-mcp>`
/// child element with recommendations for the next agent fixing this issue.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssueEntry {
    /// The issue description text
    pub text: String,
    /// Optional skills and MCP recommendations for fixing this issue
    pub skills_mcp: Option<SkillsMcp>,
}

/// Parsed issues elements from valid XML.
///
/// This type represents the result of successfully validating issues XML content.
/// It contains either a list of issues or a "no issues found" message.
///
/// # XML Format
///
/// The XML can take two forms:
///
/// **With issues:**
/// ```xml
/// <ralph-issues>
///   <ralph-issue>First issue description</ralph-issue>
///   <ralph-issue>Second issue description</ralph-issue>
/// </ralph-issues>
/// ```
///
/// **Without issues:**
/// ```xml
/// <ralph-issues>
///   <ralph-no-issues-found>No issues were found during review</ralph-no-issues-found>
/// </ralph-issues>
/// ```
///
/// # Examples
///
/// ```
/// use ralph_workflow::files::llm_output_extraction::{IssuesElements, IssueEntry};
///
/// // Issues found - access via public fields
/// let issues = IssuesElements {
///     issues: vec![
///         IssueEntry { text: "First issue".to_string(), skills_mcp: None },
///         IssueEntry { text: "Second issue".to_string(), skills_mcp: None },
///     ],
///     no_issues_found: None,
/// };
/// assert_eq!(issues.issues.len(), 2);
/// assert_eq!(issues.no_issues_found, None);
///
/// // No issues found - access via public fields
/// let no_issues = IssuesElements {
///     issues: vec![],
///     no_issues_found: Some("All good".to_string()),
/// };
/// assert!(no_issues.issues.is_empty());
/// assert_eq!(no_issues.no_issues_found, Some("All good".to_string()));
/// ```
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuesElements {
    /// List of issues (if any)
    pub issues: Vec<IssueEntry>,
    /// No issues found message (if no issues)
    pub no_issues_found: Option<String>,
}

impl IssuesElements {
    /// Returns true if there are no issues.
    ///
    /// This is true when the issues list is empty and a "no issues found" message exists.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.issues.is_empty() && self.no_issues_found.is_some()
    }

    /// Returns the number of issues.
    ///
    /// This is the count of issues in the issues list (does not include "no issues found").
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn issue_count(&self) -> usize {
        self.issues.len()
    }

    /// Returns the issue texts as a Vec of Strings for backward-compatible consumers.
    ///
    /// This helper extracts just the text portion from each issue entry.
    #[must_use]
    pub fn issue_texts(&self) -> Vec<String> {
        self.issues.iter().map(|e| e.text.clone()).collect()
    }
}
