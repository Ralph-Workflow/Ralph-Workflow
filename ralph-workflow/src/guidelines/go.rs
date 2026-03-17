//! Go-specific review guidelines
//!
//! Contains guidelines for Go projects including web frameworks like Gin, Chi, Fiber, and Echo.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Go-specific guidelines to the review
pub fn add_guidelines(mut guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Run go fmt and golint".to_string(),
        "Check all error returns".to_string(),
        "Use defer for cleanup".to_string(),
        "Keep functions short and focused".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Validate input bounds before slice operations".to_string(),
        "Use crypto/rand for security-sensitive random numbers".to_string(),
        "Check for SQL injection in database queries".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Pre-allocate slices when size is known".to_string(),
        "Use sync.Pool for frequently allocated objects".to_string(),
        "Consider goroutine leaks".to_string(),
    ]);

    guidelines.testing_checks.extend([
        "Use table-driven tests".to_string(),
        "Test error paths explicitly".to_string(),
        "Use testify or similar for assertions".to_string(),
    ]);

    guidelines.idioms.extend([
        "Accept interfaces, return structs".to_string(),
        "Make the zero value useful".to_string(),
        "Don't communicate by sharing memory".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Don't ignore returned errors".to_string(),
        "Avoid init() when possible".to_string(),
        "Don't use panic for normal error handling".to_string(),
    ]);

    if stack
        .frameworks
        .iter()
        .any(|f| matches!(f.as_str(), "Gin" | "Chi" | "Fiber" | "Echo"))
    {
        guidelines = add_go_web_guidelines(guidelines);
    }

    guidelines
}

/// Add Go web framework guidelines (Gin, Chi, Fiber, Echo)
fn add_go_web_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use proper error handling in handlers".to_string(),
        "Use context for cancellation".to_string(),
        "Structure handlers and middleware properly".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Set proper CORS headers".to_string(),
        "Validate input in handlers".to_string(),
    ]);

    guidelines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_go_guidelines() {
        let stack = ProjectStack {
            primary_language: "Go".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: true,
            test_framework: Some("go test".to_string()),
            package_manager: Some("Go modules".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Go-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("error") || c.contains("golint")));
        assert!(guidelines.anti_patterns.iter().any(|c| c.contains("panic")));
    }

    #[test]
    fn test_go_gin_guidelines() {
        let stack = ProjectStack {
            primary_language: "Go".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Gin".to_string()],
            has_tests: true,
            test_framework: Some("go test".to_string()),
            package_manager: Some("Go modules".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Go web framework checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("handlers") || c.contains("context")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("CORS") || c.contains("input")));
    }

    #[test]
    fn test_go_chi_guidelines() {
        let stack = ProjectStack {
            primary_language: "Go".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Chi".to_string()],
            has_tests: true,
            test_framework: Some("go test".to_string()),
            package_manager: Some("Go modules".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Go web framework checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("middleware")));
    }

    #[test]
    fn test_go_fiber_guidelines() {
        let stack = ProjectStack {
            primary_language: "Go".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Fiber".to_string()],
            has_tests: true,
            test_framework: Some("go test".to_string()),
            package_manager: Some("Go modules".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Go web framework checks
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("CORS")));
    }

    #[test]
    fn test_go_echo_guidelines() {
        let stack = ProjectStack {
            primary_language: "Go".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Echo".to_string()],
            has_tests: true,
            test_framework: Some("go test".to_string()),
            package_manager: Some("Go modules".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Go web framework checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("context")));
    }
}
