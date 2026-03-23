//! Go-specific review guidelines
//!
//! Contains guidelines for Go projects including web frameworks like Gin, Chi, Fiber, and Echo.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Go-specific guidelines to the review
pub fn add_guidelines(guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    let base = ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Run go fmt and golint".to_string(),
                "Check all error returns".to_string(),
                "Use defer for cleanup".to_string(),
                "Keep functions short and focused".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Validate input bounds before slice operations".to_string(),
                "Use crypto/rand for security-sensitive random numbers".to_string(),
                "Check for SQL injection in database queries".to_string(),
            ])
            .collect(),
        performance_checks: guidelines
            .performance_checks
            .into_iter()
            .chain([
                "Pre-allocate slices when size is known".to_string(),
                "Use sync.Pool for frequently allocated objects".to_string(),
                "Consider goroutine leaks".to_string(),
            ])
            .collect(),
        testing_checks: guidelines
            .testing_checks
            .into_iter()
            .chain([
                "Use table-driven tests".to_string(),
                "Test error paths explicitly".to_string(),
                "Use testify or similar for assertions".to_string(),
            ])
            .collect(),
        idioms: guidelines
            .idioms
            .into_iter()
            .chain([
                "Accept interfaces, return structs".to_string(),
                "Make the zero value useful".to_string(),
                "Don't communicate by sharing memory".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Don't ignore returned errors".to_string(),
                "Avoid init() when possible".to_string(),
                "Don't use panic for normal error handling".to_string(),
            ])
            .collect(),
        ..guidelines
    };

    if stack
        .frameworks
        .iter()
        .any(|f| matches!(f.as_str(), "Gin" | "Chi" | "Fiber" | "Echo"))
    {
        add_go_web_guidelines(base)
    } else {
        base
    }
}

fn add_go_web_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use proper error handling in handlers".to_string(),
                "Use context for cancellation".to_string(),
                "Structure handlers and middleware properly".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Set proper CORS headers".to_string(),
                "Validate input in handlers".to_string(),
            ])
            .collect(),
        ..guidelines
    }
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
