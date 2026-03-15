//! Tests for review guidelines generation.
//!
//! Validates that language and framework-specific guidelines are correctly
//! generated for various project stacks (Rust, Python/Django, TypeScript/React).

use super::*;
use crate::language_detector::ProjectStack;

#[test]
fn default_guidelines_have_core_sections() {
    let guidelines = ReviewGuidelines::default();
    assert!(!guidelines.quality_checks.is_empty());
    assert!(!guidelines.security_checks.is_empty());
    assert!(!guidelines.anti_patterns.is_empty());
}

#[test]
fn rust_guidelines_include_rust_specific_checks() {
    let stack = ProjectStack {
        primary_language: "Rust".to_string(),
        frameworks: vec!["Actix".to_string()],
        has_tests: true,
        test_framework: Some("cargo test".to_string()),
        package_manager: Some("Cargo".to_string()),
        ..Default::default()
    };

    let guidelines = ReviewGuidelines::for_stack(&stack);
    assert!(guidelines
        .quality_checks
        .iter()
        .any(|c| c.contains("unwrap")));
    assert!(guidelines
        .security_checks
        .iter()
        .any(|c| c.contains("unsafe")));
}

#[test]
fn python_django_guidelines_include_framework_checks() {
    let stack = ProjectStack {
        primary_language: "Python".to_string(),
        frameworks: vec!["Django".to_string()],
        has_tests: true,
        test_framework: Some("pytest".to_string()),
        package_manager: Some("pip".to_string()),
        ..Default::default()
    };

    let guidelines = ReviewGuidelines::for_stack(&stack);
    assert!(guidelines.quality_checks.iter().any(|c| c.contains("PEP")));
    assert!(guidelines
        .security_checks
        .iter()
        .any(|c| c.contains("CSRF")));
}

#[test]
fn typescript_react_guidelines_include_ts_and_react_checks() {
    let stack = ProjectStack {
        primary_language: "TypeScript".to_string(),
        secondary_languages: vec!["JavaScript".to_string()],
        frameworks: vec!["React".to_string(), "Next.js".to_string()],
        has_tests: true,
        test_framework: Some("Jest".to_string()),
        package_manager: Some("Bun".to_string()),
    };

    let guidelines = ReviewGuidelines::for_stack(&stack);
    assert!(guidelines.quality_checks.iter().any(|c| c.contains("any")));
    assert!(guidelines
        .quality_checks
        .iter()
        .any(|c| c.contains("hooks")));
}

#[test]
fn unknown_language_uses_defaults() {
    let stack = ProjectStack {
        primary_language: "Brainfuck".to_string(),
        ..Default::default()
    };

    let guidelines = ReviewGuidelines::for_stack(&stack);
    assert!(!guidelines.quality_checks.is_empty());
    assert!(!guidelines.security_checks.is_empty());
}

#[test]
fn format_for_prompt_contains_headers_and_is_reasonable_size() {
    let stack = ProjectStack {
        primary_language: "Rust".to_string(),
        ..Default::default()
    };
    let guidelines = ReviewGuidelines::for_stack(&stack);
    let formatted = guidelines.format_for_prompt();

    assert!(formatted.contains("CODE QUALITY"));
    assert!(formatted.contains("SECURITY"));
    assert!(formatted.len() > 100);
    assert!(formatted.len() < 5000);
}

#[test]
fn get_all_checks_has_severity_coverage() {
    let stack = ProjectStack {
        primary_language: "Python".to_string(),
        frameworks: vec!["Django".to_string()],
        has_tests: true,
        test_framework: Some("pytest".to_string()),
        package_manager: Some("pip".to_string()),
        ..Default::default()
    };

    let guidelines = ReviewGuidelines::for_stack(&stack);
    let all = guidelines.get_all_checks();

    assert!(all.iter().any(|c| c.severity == CheckSeverity::Critical));
    assert!(all.iter().any(|c| c.severity == CheckSeverity::High));
    assert!(all.iter().any(|c| c.severity == CheckSeverity::Medium));
    assert!(all.iter().any(|c| c.severity == CheckSeverity::Low));
}
