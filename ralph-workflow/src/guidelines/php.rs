//! PHP-specific review guidelines
//!
//! Contains guidelines for PHP projects including Laravel and Symfony frameworks.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add PHP-specific guidelines to the review
pub fn add_guidelines(mut guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use PHP 8+ features where available".to_string(),
        "Follow PSR standards".to_string(),
        "Use type declarations".to_string(),
        "Use named arguments for clarity".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Use prepared statements for database queries".to_string(),
        "Escape output with htmlspecialchars()".to_string(),
        "Validate file uploads thoroughly".to_string(),
        "Use password_hash() for passwords".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid using extract() with user input".to_string(),
        "Don't suppress errors with @".to_string(),
        "Avoid register_globals behavior".to_string(),
    ]);

    if stack.frameworks.contains(&"Laravel".to_string()) {
        guidelines = add_laravel_guidelines(guidelines);
    }
    if stack.frameworks.contains(&"Symfony".to_string()) {
        guidelines = add_symfony_guidelines(guidelines);
    }

    guidelines
}

/// Add Laravel-specific guidelines
fn add_laravel_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines
        .quality_checks
        .push("Use Eloquent relationships properly".to_string());

    guidelines
        .security_checks
        .push("Use Laravel's CSRF protection".to_string());

    guidelines.quality_checks.extend([
        "Follow Laravel conventions".to_string(),
        "Use Laravel's validation system".to_string(),
        "Use middleware for cross-cutting concerns".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Use Laravel's authorization (Gates/Policies)".to_string(),
        "Sanitize input with request validation".to_string(),
    ]);

    guidelines
}

/// Add Symfony-specific guidelines
fn add_symfony_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Follow Symfony best practices".to_string(),
        "Use dependency injection properly".to_string(),
        "Use Symfony forms for validation".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Configure Symfony Security properly".to_string(),
        "Use voters for authorization".to_string(),
    ]);

    guidelines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_php_guidelines() {
        let stack = ProjectStack {
            primary_language: "PHP".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Composer".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have PHP-specific security checks
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("prepared statements") || c.contains("htmlspecialchars")));
        assert!(guidelines.quality_checks.iter().any(|c| c.contains("PSR")));
    }

    #[test]
    fn test_php_laravel_guidelines() {
        let stack = ProjectStack {
            primary_language: "PHP".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Laravel".to_string()],
            has_tests: true,
            test_framework: Some("PHPUnit".to_string()),
            package_manager: Some("Composer".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Laravel-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Eloquent") || c.contains("Laravel")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("CSRF")));
    }

    #[test]
    fn test_php_symfony_guidelines() {
        let stack = ProjectStack {
            primary_language: "PHP".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Symfony".to_string()],
            has_tests: true,
            test_framework: Some("PHPUnit".to_string()),
            package_manager: Some("Composer".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Symfony-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Symfony")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("voters") || c.contains("Security")));
    }
}
