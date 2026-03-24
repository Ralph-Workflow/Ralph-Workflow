//! Java and Kotlin review guidelines
//!
//! Contains guidelines for Java and Kotlin projects including Spring framework.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Java-specific guidelines to the review
pub fn add_java_guidelines(guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    let base = ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Follow Java naming conventions".to_string(),
                "Use Optional instead of null returns".to_string(),
                "Prefer composition over inheritance".to_string(),
                "Use try-with-resources for AutoCloseable".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Use PreparedStatement for SQL queries".to_string(),
                "Validate deserialized objects".to_string(),
                "Check for path traversal in file operations".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid catching Exception or Throwable".to_string(),
                "Don't use raw types with generics".to_string(),
                "Avoid public fields".to_string(),
            ])
            .collect(),
        ..guidelines
    };

    if stack.frameworks.contains(&"Spring".to_string()) {
        add_spring_guidelines(base)
    } else {
        base
    }
}

pub(crate) fn add_kotlin_guidelines(
    guidelines: ReviewGuidelines,
    stack: &ProjectStack,
) -> ReviewGuidelines {
    let base = ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use null safety features".to_string(),
                "Prefer data classes for DTOs".to_string(),
                "Use extension functions appropriately".to_string(),
                "Leverage scope functions (let, run, apply)".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid !! operator without validation".to_string(),
                "Don't use lateinit for nullable fields".to_string(),
            ])
            .collect(),
        ..guidelines
    };

    if stack.frameworks.contains(&"Spring".to_string()) {
        add_spring_guidelines(base)
    } else {
        base
    }
}

fn add_spring_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use constructor injection".to_string(),
                "Follow Spring Boot conventions".to_string(),
                "Use proper transaction management".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Configure Spring Security properly".to_string(),
                "Use @Valid for input validation".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_java_guidelines() {
        let stack = ProjectStack {
            primary_language: "Java".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: true,
            test_framework: Some("JUnit".to_string()),
            package_manager: Some("Maven".to_string()),
        };

        let guidelines = add_java_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Java-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Optional")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("PreparedStatement")));
        assert!(guidelines
            .anti_patterns
            .iter()
            .any(|c| c.contains("Exception") || c.contains("Throwable")));
    }

    #[test]
    fn test_java_spring_guidelines() {
        let stack = ProjectStack {
            primary_language: "Java".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Spring".to_string()],
            has_tests: true,
            test_framework: Some("JUnit".to_string()),
            package_manager: Some("Maven".to_string()),
        };

        let guidelines = add_java_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Spring-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("constructor injection")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("Spring Security") || c.contains("@Valid")));
    }

    #[test]
    fn test_kotlin_guidelines() {
        let stack = ProjectStack {
            primary_language: "Kotlin".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Gradle".to_string()),
        };

        let guidelines = add_kotlin_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Kotlin-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("null safety") || c.contains("data class")));
        assert!(guidelines.anti_patterns.iter().any(|c| c.contains("!!")));
    }

    #[test]
    fn test_kotlin_spring_guidelines() {
        let stack = ProjectStack {
            primary_language: "Kotlin".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Spring".to_string()],
            has_tests: true,
            test_framework: Some("JUnit".to_string()),
            package_manager: Some("Gradle".to_string()),
        };

        let guidelines = add_kotlin_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Spring-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("constructor injection")));
    }
}
