//! Review Metrics
//!
//! Core `ReviewMetrics` struct and parsing logic for extracting
//! issue counts and resolution rates from ISSUES.md.

use crate::workspace::Workspace;
use std::io;
use std::path::Path;

use super::severity::IssueSeverity;

/// Parse header-based issue format: `#### [ ] Critical: description`
///
/// Returns the text after the checkbox if it matches, or None if not a header issue format.
fn parse_header_issue_format(line: &str) -> Option<&str> {
    // Strip leading # characters
    let stripped = line.trim_start_matches('#');
    if stripped.len() == line.len() {
        // No # characters found, not a header
        return None;
    }

    let stripped = stripped.trim_start();

    // Check for checkbox format in header
    if let Some(rest) = stripped.strip_prefix("[ ]") {
        return Some(rest.trim_start());
    }
    if let Some(rest) = stripped
        .strip_prefix("[x]")
        .or_else(|| stripped.strip_prefix("[X]"))
    {
        return Some(rest.trim_start());
    }

    None
}

/// Review metrics collected from a pipeline run
#[derive(Debug, Clone, Default)]
pub struct ReviewMetrics {
    /// Total number of issues found
    pub(crate) total_issues: u32,
    /// Issues by severity
    pub(crate) critical_issues: u32,
    pub(crate) high_issues: u32,
    pub(crate) medium_issues: u32,
    pub(crate) low_issues: u32,
    /// Number of resolved issues
    pub(crate) resolved_issues: u32,
    /// Whether the issues file was found
    pub(crate) issues_file_found: bool,
    /// Whether no issues were found (explicit statement)
    pub(crate) no_issues_declared: bool,
}

impl ReviewMetrics {
    /// Create new empty metrics
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Parse metrics from ISSUES.md content
    pub(crate) fn from_issues_content(content: &str) -> Self {
        let mut metrics = Self::new();
        metrics.issues_file_found = true;

        // Check for explicit "no issues" declaration AFTER parsing all issue lines.
        // This ensures we don't set no_issues_declared=true when the review contains
        // both "No issues found" text AND actual issue checkboxes - the actual issues
        // take precedence over any textual declaration.
        //
        // We defer this check until after we've parsed all issue lines below.
        let content_lower = content.to_lowercase();

        // Parse issue lines
        // Format: - [ ] Critical: [file:line] Description
        // Format: - [x] High: [file:line] Description
        // Format: #### [ ] Critical: [file:line] Description (header-based format)
        for line in content.lines() {
            let trimmed = line.trim();

            // Skip empty lines
            if trimmed.is_empty() {
                continue;
            }

            // Try header-based format first (e.g., "#### [ ] Critical:")
            // Some agents output issues with markdown headers containing checkboxes
            if let Some(rest) = parse_header_issue_format(trimmed) {
                if let Some(severity) = IssueSeverity::from_str(rest) {
                    metrics.total_issues = metrics.total_issues.saturating_add(1);
                    match severity {
                        IssueSeverity::Critical => {
                            metrics.critical_issues = metrics.critical_issues.saturating_add(1)
                        }
                        IssueSeverity::High => {
                            metrics.high_issues = metrics.high_issues.saturating_add(1)
                        }
                        IssueSeverity::Medium => {
                            metrics.medium_issues = metrics.medium_issues.saturating_add(1)
                        }
                        IssueSeverity::Low => {
                            metrics.low_issues = metrics.low_issues.saturating_add(1)
                        }
                    }
                }
                continue;
            }

            // Skip headers that don't contain issue format
            if trimmed.starts_with('#') {
                continue;
            }

            // Check for checkbox format
            let (resolved, rest) = if let Some(rest) = trimmed
                .strip_prefix("- [x]")
                .or_else(|| trimmed.strip_prefix("- [X]"))
            {
                (true, rest)
            } else if let Some(rest) = trimmed.strip_prefix("- [ ]") {
                (false, rest)
            } else if let Some(rest) = trimmed.strip_prefix("-") {
                // Plain list item without checkbox
                (false, rest)
            } else {
                continue;
            };

            let rest = rest.trim();

            // Try to extract severity
            if let Some(severity) = IssueSeverity::from_str(rest) {
                // Update counts
                metrics.total_issues = metrics.total_issues.saturating_add(1);
                if resolved {
                    metrics.resolved_issues = metrics.resolved_issues.saturating_add(1);
                }
                match severity {
                    IssueSeverity::Critical => {
                        metrics.critical_issues = metrics.critical_issues.saturating_add(1)
                    }
                    IssueSeverity::High => {
                        metrics.high_issues = metrics.high_issues.saturating_add(1)
                    }
                    IssueSeverity::Medium => {
                        metrics.medium_issues = metrics.medium_issues.saturating_add(1)
                    }
                    IssueSeverity::Low => metrics.low_issues = metrics.low_issues.saturating_add(1),
                }
            }
        }

        // Check for explicit "no issues" declaration only if no actual issues were found.
        // This prevents false positives where a review contains both "No issues found" text
        // AND actual issue checkboxes - the actual issues take precedence.
        if metrics.total_issues == 0 {
            for line in content_lower.lines() {
                let trimmed = line.trim();
                // Only match explicit declarations, not text that happens to contain the phrase
                // A declaration is typically: "No issues found" or "No issues" at the start of a line
                // or as the entire line (possibly after list markers)
                let cleaned = trimmed
                    .trim_start_matches('-')
                    .trim_start_matches('*')
                    .trim();

                if cleaned == "no issues found"
                    || cleaned == "no issues found."
                    || cleaned == "no issues"
                    || cleaned == "no issues."
                    || cleaned == "all issues resolved"
                    || cleaned == "all issues resolved."
                    // Handle "all issues resolved. <additional text>" pattern
                    || cleaned.starts_with("all issues resolved.")
                    // Handle "no issues found" at start without severity markers
                    || cleaned.starts_with("no issues found")
                        && !cleaned.contains("critical")
                        && !cleaned.contains("high")
                        && !cleaned.contains("medium")
                        && !cleaned.contains("low")
                {
                    metrics.no_issues_declared = true;
                    break;
                }
            }
        } else {
            // If we found actual issues, ensure "no issues declared" is false
            // This handles cases where review text contains "No issues found" somewhere
            // but the review actually lists issues elsewhere.
            metrics.no_issues_declared = false;
        }

        metrics
    }

    /// Load metrics from the ISSUES.md file using workspace abstraction.
    ///
    /// This enables testing with `MemoryWorkspace` without real filesystem access.
    /// Used by the pipeline layer for post-flight validation checks.
    pub(crate) fn from_issues_file_with_workspace(workspace: &dyn Workspace) -> io::Result<Self> {
        let path = Path::new(".agent/ISSUES.md");
        if !workspace.exists(path) {
            return Ok(Self::new());
        }

        let content = workspace.read(path)?;
        Ok(Self::from_issues_content(&content))
    }
}
