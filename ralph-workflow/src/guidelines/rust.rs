//! Rust-specific review guidelines
//!
//! Contains guidelines for Rust projects including web frameworks like Actix, Axum, and Rocket.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Rust-specific guidelines to the review
pub(crate) fn add_guidelines(
    guidelines: ReviewGuidelines,
    stack: &ProjectStack,
) -> ReviewGuidelines {
    let base = ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "No unwrap/expect in production paths; use Result + ?".to_string(),
                "Proper lifetime annotations where needed".to_string(),
                "Prefer borrowing over cloning".to_string(),
                "Use strong types and exhaustive matching".to_string(),
                "Keep public API minimal (pub(crate) by default)".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Minimize unsafe code blocks; justify each use".to_string(),
                "Check for integer overflow in arithmetic".to_string(),
                "Validate untrusted input before processing".to_string(),
            ])
            .collect(),
        performance_checks: guidelines
            .performance_checks
            .into_iter()
            .chain([
                "Avoid unnecessary allocations (String → &str, Vec → slice)".to_string(),
                "Use iterators instead of indexing loops".to_string(),
                "Consider async for I/O-bound operations".to_string(),
            ])
            .collect(),
        testing_checks: guidelines
            .testing_checks
            .into_iter()
            .chain([
                "Unit tests for core logic (#[cfg(test)])".to_string(),
                "Integration tests in tests/ directory".to_string(),
                "Consider property-based testing for invariants".to_string(),
            ])
            .collect(),
        idioms: guidelines
            .idioms
            .into_iter()
            .chain([
                "Follow Rust API Guidelines".to_string(),
                "Use derive macros appropriately".to_string(),
                "Implement standard traits (Debug, Clone, etc.)".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid .clone() to satisfy borrow checker without understanding".to_string(),
                "Don't use Rc<RefCell<T>> when ownership can be restructured".to_string(),
                "Avoid panic! in library code".to_string(),
            ])
            .collect(),
        ..guidelines
    };

    if stack
        .frameworks
        .iter()
        .any(|f| matches!(f.as_str(), "Actix" | "Axum" | "Rocket" | "Warp"))
    {
        add_rust_web_guidelines(base)
    } else {
        base
    }
}

fn add_rust_web_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use extractors for request data".to_string(),
                "Handle errors with proper status codes".to_string(),
                "Use async handlers appropriately".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Validate all user input".to_string(),
                "Use tower middleware for common concerns".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rust_guidelines() {
        let stack = ProjectStack {
            primary_language: "Rust".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Actix".to_string()],
            has_tests: true,
            test_framework: Some("cargo test".to_string()),
            package_manager: Some("Cargo".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Rust-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("unwrap")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("unsafe")));
        // Should have Actix-specific checks (web framework)
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("extractors")));
    }

    #[test]
    fn test_rust_without_web_framework() {
        let stack = ProjectStack {
            primary_language: "Rust".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Tokio".to_string()],
            has_tests: true,
            test_framework: Some("cargo test".to_string()),
            package_manager: Some("Cargo".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Rust-specific checks but not web framework checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("unwrap")));
        // Should not have web-specific extractors check
        let extractor_count = guidelines
            .quality_checks
            .iter()
            .filter(|c| c.contains("extractors"))
            .count();
        assert_eq!(extractor_count, 0);
    }

    #[test]
    fn test_rust_axum_guidelines() {
        let stack = ProjectStack {
            primary_language: "Rust".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Axum".to_string()],
            has_tests: true,
            test_framework: Some("cargo test".to_string()),
            package_manager: Some("Cargo".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Rust web framework checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("extractors") || c.contains("async")));
    }
}
