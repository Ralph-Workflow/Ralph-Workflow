//! Core types for review guidelines
//!
//! Contains the fundamental structures used across all language-specific guideline modules.

/// Severity level for code review checks
///
/// Used to prioritize review feedback and help developers focus on
/// the most important issues first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum CheckSeverity {
    /// Must fix before merge - security vulnerabilities, data loss, crashes
    Critical,
    /// Should fix before merge - bugs, significant functional issues
    High,
    /// Should address - code quality, maintainability concerns
    Medium,
    /// Nice to have - minor improvements, style suggestions
    Low,
    /// Informational - observations, suggestions for future consideration
    Info,
}

impl std::fmt::Display for CheckSeverity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Critical => write!(f, "CRITICAL"),
            Self::High => write!(f, "HIGH"),
            Self::Medium => write!(f, "MEDIUM"),
            Self::Low => write!(f, "LOW"),
            Self::Info => write!(f, "INFO"),
        }
    }
}

/// A review check with associated severity
#[derive(Debug, Clone)]
pub struct SeverityCheck {
    /// The check description
    pub(crate) check: String,
    /// Severity level for this check
    pub(crate) severity: CheckSeverity,
}

impl SeverityCheck {
    pub(crate) fn new(check: impl Into<String>, severity: CheckSeverity) -> Self {
        Self {
            check: check.into(),
            severity,
        }
    }

    pub(crate) fn critical(check: impl Into<String>) -> Self {
        Self::new(check, CheckSeverity::Critical)
    }

    pub(crate) fn high(check: impl Into<String>) -> Self {
        Self::new(check, CheckSeverity::High)
    }

    pub(crate) fn medium(check: impl Into<String>) -> Self {
        Self::new(check, CheckSeverity::Medium)
    }

    pub(crate) fn low(check: impl Into<String>) -> Self {
        Self::new(check, CheckSeverity::Low)
    }

    pub(crate) fn info(check: impl Into<String>) -> Self {
        Self::new(check, CheckSeverity::Info)
    }
}

/// Review guidelines for a specific technology stack.
///
/// Each field is a category of checks/expectations used to generate review guidance.
#[derive(Debug, Clone)]
pub struct ReviewGuidelines {
    pub(crate) quality_checks: Vec<String>,
    pub(crate) security_checks: Vec<String>,
    pub(crate) performance_checks: Vec<String>,
    pub(crate) testing_checks: Vec<String>,
    pub(crate) documentation_checks: Vec<String>,
    pub(crate) idioms: Vec<String>,
    pub(crate) anti_patterns: Vec<String>,
    pub(crate) concurrency_checks: Vec<String>,
    pub(crate) resource_checks: Vec<String>,
    pub(crate) observability_checks: Vec<String>,
    pub(crate) secrets_checks: Vec<String>,
    pub(crate) api_design_checks: Vec<String>,
}

impl Default for ReviewGuidelines {
    fn default() -> Self {
        Self {
            quality_checks: vec![
                "Code follows consistent style and formatting".to_string(),
                "Functions have single responsibility".to_string(),
                "Error handling is comprehensive".to_string(),
                "No dead code or unused imports".to_string(),
            ],
            security_checks: vec![
                "No hardcoded secrets or credentials".to_string(),
                "Input validation on external data".to_string(),
                "Proper authentication/authorization checks".to_string(),
            ],
            performance_checks: vec![
                "No obvious performance bottlenecks".to_string(),
                "Efficient data structures used".to_string(),
            ],
            testing_checks: vec![
                "Tests cover main functionality".to_string(),
                "Edge cases are tested".to_string(),
            ],
            documentation_checks: vec![
                "Public APIs are documented".to_string(),
                "Complex logic has explanatory comments".to_string(),
            ],
            idioms: vec!["Code follows language conventions".to_string()],
            anti_patterns: vec!["Avoid code duplication".to_string()],
            concurrency_checks: vec![
                "Shared mutable state is properly synchronized".to_string(),
                "No potential deadlocks (lock ordering)".to_string(),
            ],
            resource_checks: vec![
                "Resources are properly closed/released".to_string(),
                "No resource leaks in error paths".to_string(),
            ],
            observability_checks: vec![
                "Errors are logged with context".to_string(),
                "Critical operations have appropriate logging".to_string(),
            ],
            secrets_checks: vec![
                "Secrets loaded from environment/config, not hardcoded".to_string(),
                "Sensitive data not logged or exposed in errors".to_string(),
            ],
            api_design_checks: vec![
                "API follows consistent naming conventions".to_string(),
                "Breaking changes are clearly documented".to_string(),
            ],
        }
    }
}

impl ReviewGuidelines {
    /// Get all checks with their severity classifications
    ///
    /// Returns a comprehensive list of all applicable checks organized by category
    /// with severity levels. This is useful for generating detailed review reports.
    pub(crate) fn get_all_checks(&self) -> Vec<SeverityCheck> {
        self.security_checks
            .iter()
            .chain(self.secrets_checks.iter())
            .map(SeverityCheck::critical)
            .chain(
                self.concurrency_checks
                    .iter()
                    .chain(self.resource_checks.iter())
                    .map(SeverityCheck::high),
            )
            .chain(
                self.quality_checks
                    .iter()
                    .chain(self.anti_patterns.iter())
                    .chain(self.performance_checks.iter())
                    .chain(self.testing_checks.iter())
                    .chain(self.api_design_checks.iter())
                    .map(SeverityCheck::medium),
            )
            .chain(
                self.observability_checks
                    .iter()
                    .chain(self.documentation_checks.iter())
                    .map(SeverityCheck::low),
            )
            .chain(self.idioms.iter().map(SeverityCheck::info))
            .collect()
    }

    /// Get a brief summary for display
    pub(crate) fn summary(&self) -> String {
        format!(
            "{} quality checks, {} security checks, {} anti-patterns",
            self.quality_checks.len(),
            self.security_checks.len(),
            self.anti_patterns.len()
        )
    }

    /// Get a comprehensive count of all checks
    pub(crate) const fn total_checks(&self) -> usize {
        self.quality_checks.len()
            + self.security_checks.len()
            + self.performance_checks.len()
            + self.testing_checks.len()
            + self.documentation_checks.len()
            + self.idioms.len()
            + self.anti_patterns.len()
            + self.concurrency_checks.len()
            + self.resource_checks.len()
            + self.observability_checks.len()
            + self.secrets_checks.len()
            + self.api_design_checks.len()
    }
}

/// Test-only methods for `ReviewGuidelines`.
/// These are used by tests to format guidelines into prompts.
#[cfg(test)]
impl ReviewGuidelines {
    /// Format a section of guidelines with a title and item limit.
    fn format_section(items: &[String], title: &str, limit: usize) -> Option<String> {
        if items.is_empty() {
            return None;
        }
        let mut lines: Vec<String> = items
            .iter()
            .take(limit)
            .map(|s| format!("  - {s}"))
            .collect();
        if items.len() > limit {
            lines.push(format!("  - ... (+{} more)", items.len() - limit));
        }
        Some(format!("{}:\n{}", title, lines.join("\n")))
    }

    /// Format guidelines as a prompt section
    pub(crate) fn format_for_prompt(&self) -> String {
        let sections: Vec<String> = [
            Self::format_section(&self.quality_checks, "CODE QUALITY", 10),
            Self::format_section(&self.security_checks, "SECURITY", 10),
            Self::format_section(&self.performance_checks, "PERFORMANCE", 8),
            Self::format_section(&self.anti_patterns, "AVOID", 8),
        ]
        .into_iter()
        .flatten()
        .collect();

        sections.join("\n\n")
    }

    /// Format guidelines with severity priorities for the review prompt.
    ///
    /// This produces a more detailed prompt section that groups checks by priority,
    /// helping agents focus on the most critical issues first.
    pub(crate) fn format_for_prompt_with_priorities(&self) -> String {
        fn build_section(header: &str, checks: &[SeverityCheck], limit: usize) -> Option<String> {
            if checks.is_empty() {
                return None;
            }
            let items: Vec<String> = checks
                .iter()
                .take(limit)
                .map(|c| format!("  - {}", c.check))
                .collect();
            let mut items = items;
            if checks.len() > limit {
                items.push(format!("  - ... (+{} more)", checks.len() - limit));
            }
            Some(format!("{}\n{}", header, items.join("\n")))
        }

        let critical_checks: Vec<SeverityCheck> = self
            .security_checks
            .iter()
            .chain(self.secrets_checks.iter())
            .cloned()
            .map(SeverityCheck::critical)
            .collect();

        let high_checks: Vec<SeverityCheck> = self
            .concurrency_checks
            .iter()
            .chain(self.resource_checks.iter())
            .cloned()
            .map(SeverityCheck::high)
            .collect();

        let medium_checks: Vec<SeverityCheck> = self
            .quality_checks
            .iter()
            .chain(self.anti_patterns.iter())
            .chain(self.performance_checks.iter())
            .chain(self.testing_checks.iter())
            .chain(self.api_design_checks.iter())
            .cloned()
            .map(SeverityCheck::medium)
            .collect();

        let low_checks: Vec<SeverityCheck> = self
            .documentation_checks
            .iter()
            .chain(self.observability_checks.iter())
            .cloned()
            .map(SeverityCheck::low)
            .collect();

        let info_checks: Vec<SeverityCheck> = self
            .idioms
            .iter()
            .cloned()
            .map(SeverityCheck::info)
            .collect();

        let sections: Vec<String> = [
            build_section("CRITICAL (must fix before merge):", &critical_checks, 10),
            build_section("HIGH (should fix before merge):", &high_checks, 10),
            build_section("MEDIUM (should address):", &medium_checks, 12),
            build_section("LOW (nice to have):", &low_checks, 10),
            build_section("INFO (observations):", &info_checks, 10),
        ]
        .into_iter()
        .flatten()
        .collect();

        sections.join("\n\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_guidelines() {
        let guidelines = ReviewGuidelines::default();
        assert!(!guidelines.quality_checks.is_empty());
        assert!(!guidelines.security_checks.is_empty());
    }

    #[test]
    fn test_check_severity_ordering() {
        // Critical should be less than (higher priority) than High, etc.
        assert!(CheckSeverity::Critical < CheckSeverity::High);
        assert!(CheckSeverity::High < CheckSeverity::Medium);
        assert!(CheckSeverity::Medium < CheckSeverity::Low);
        assert!(CheckSeverity::Low < CheckSeverity::Info);
    }

    #[test]
    fn test_check_severity_display() {
        assert_eq!(format!("{}", CheckSeverity::Critical), "CRITICAL");
        assert_eq!(format!("{}", CheckSeverity::High), "HIGH");
        assert_eq!(format!("{}", CheckSeverity::Medium), "MEDIUM");
        assert_eq!(format!("{}", CheckSeverity::Low), "LOW");
        assert_eq!(format!("{}", CheckSeverity::Info), "INFO");
    }

    #[test]
    fn test_severity_check_constructors() {
        let critical = SeverityCheck::critical("test");
        assert_eq!(critical.severity, CheckSeverity::Critical);
        assert_eq!(critical.check, "test");

        let high = SeverityCheck::high("high test");
        assert_eq!(high.severity, CheckSeverity::High);

        let medium = SeverityCheck::medium("medium test");
        assert_eq!(medium.severity, CheckSeverity::Medium);

        let low = SeverityCheck::low("low test");
        assert_eq!(low.severity, CheckSeverity::Low);
    }

    #[test]
    fn test_get_all_checks() {
        let guidelines = ReviewGuidelines::default();
        let all_checks = guidelines.get_all_checks();

        // Should have checks from all categories
        assert!(!all_checks.is_empty());

        // Security checks should be critical
        let critical_count = all_checks
            .iter()
            .filter(|c| c.severity == CheckSeverity::Critical)
            .count();
        assert!(critical_count > 0);

        // Should have some medium severity checks
        let medium_count = all_checks
            .iter()
            .filter(|c| c.severity == CheckSeverity::Medium)
            .count();
        assert!(medium_count > 0);
    }

    #[test]
    fn test_format_for_prompt_with_priorities() {
        let guidelines = ReviewGuidelines::default();
        let formatted = guidelines.format_for_prompt_with_priorities();

        // Should contain priority indicators
        assert!(formatted.contains("CRITICAL"));
        assert!(formatted.contains("HIGH"));
        assert!(formatted.contains("MEDIUM"));
        assert!(formatted.contains("LOW"));

        // Should not omit new/extended categories
        assert!(formatted.contains("API follows consistent naming conventions"));
        assert!(formatted.contains("Code follows language conventions"));
    }

    #[test]
    fn test_summary() {
        let guidelines = ReviewGuidelines::default();
        let summary = guidelines.summary();

        assert!(summary.contains("quality checks"));
        assert!(summary.contains("security checks"));
        assert!(summary.contains("anti-patterns"));
    }

    #[test]
    fn test_total_checks() {
        let guidelines = ReviewGuidelines::default();
        let total = guidelines.total_checks();

        // Should be the sum of all check categories
        let expected = guidelines.quality_checks.len()
            + guidelines.security_checks.len()
            + guidelines.performance_checks.len()
            + guidelines.testing_checks.len()
            + guidelines.documentation_checks.len()
            + guidelines.idioms.len()
            + guidelines.anti_patterns.len()
            + guidelines.concurrency_checks.len()
            + guidelines.resource_checks.len()
            + guidelines.observability_checks.len()
            + guidelines.secrets_checks.len()
            + guidelines.api_design_checks.len();

        assert_eq!(total, expected);
        assert!(total > 10); // Should have a reasonable number of checks
    }

    #[test]
    fn test_default_has_new_check_categories() {
        let guidelines = ReviewGuidelines::default();

        // New categories should have defaults
        assert!(!guidelines.concurrency_checks.is_empty());
        assert!(!guidelines.resource_checks.is_empty());
        assert!(!guidelines.observability_checks.is_empty());
        assert!(!guidelines.secrets_checks.is_empty());
        assert!(!guidelines.api_design_checks.is_empty());
    }
}
