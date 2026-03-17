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
        // Parse all issue lines first using iterator pipeline
        let (issues, resolved_count) =
            content
                .lines()
                .fold((Vec::new(), 0u32), |(mut issues, mut resolved), line| {
                    let trimmed = line.trim();

                    // Skip empty lines
                    if trimmed.is_empty() {
                        return (issues, resolved);
                    }

                    // Try header-based format first (e.g., "#### [ ] Critical:")
                    if let Some(rest) = parse_header_issue_format(trimmed) {
                        if let Some(severity) = IssueSeverity::from_str(rest) {
                            issues.push(severity);
                        }
                        return (issues, resolved);
                    }

                    // Skip headers that don't contain issue format
                    if trimmed.starts_with('#') {
                        return (issues, resolved);
                    }

                    // Check for checkbox format
                    let (is_resolved, rest) = if let Some(rest) = trimmed
                        .strip_prefix("- [x]")
                        .or_else(|| trimmed.strip_prefix("- [X]"))
                    {
                        (true, rest)
                    } else if let Some(rest) = trimmed.strip_prefix("- [ ]") {
                        (false, rest)
                    } else if let Some(rest) = trimmed.strip_prefix("-") {
                        (false, rest)
                    } else {
                        return (issues, resolved);
                    };

                    let rest = rest.trim();

                    // Try to extract severity
                    if let Some(severity) = IssueSeverity::from_str(rest) {
                        issues.push(severity);
                        if is_resolved {
                            resolved = resolved.saturating_add(1);
                        }
                    }

                    (issues, resolved)
                });

        let content_lower = content.to_lowercase();

        // Count by severity
        let critical_issues = issues
            .iter()
            .filter(|&s| *s == IssueSeverity::Critical)
            .count() as u32;
        let high_issues = issues.iter().filter(|&s| *s == IssueSeverity::High).count() as u32;
        let medium_issues = issues
            .iter()
            .filter(|&s| *s == IssueSeverity::Medium)
            .count() as u32;
        let low_issues = issues.iter().filter(|&s| *s == IssueSeverity::Low).count() as u32;
        let total_issues = issues.len() as u32;

        // Check for explicit "no issues" declaration only if no actual issues were found.
        let no_issues_declared = if total_issues == 0 {
            content_lower.lines().any(|line| {
                let trimmed = line.trim();
                let cleaned = trimmed
                    .trim_start_matches('-')
                    .trim_start_matches('*')
                    .trim();

                cleaned == "no issues found"
                    || cleaned == "no issues found."
                    || cleaned == "no issues"
                    || cleaned == "no issues."
                    || cleaned == "all issues resolved"
                    || cleaned == "all issues resolved."
                    || cleaned.starts_with("all issues resolved.")
                    || (cleaned.starts_with("no issues found")
                        && !cleaned.contains("critical")
                        && !cleaned.contains("high")
                        && !cleaned.contains("medium")
                        && !cleaned.contains("low"))
            })
        } else {
            false
        };

        Self {
            total_issues,
            critical_issues,
            high_issues,
            medium_issues,
            low_issues,
            resolved_issues: resolved_count,
            issues_file_found: true,
            no_issues_declared,
        }
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
