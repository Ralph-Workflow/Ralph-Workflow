//! Python-specific review guidelines
//!
//! Contains guidelines for Python projects including Django, `FastAPI`, and Flask frameworks.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Python-specific guidelines to the review
pub fn add_guidelines(mut guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Follow PEP 8 style guide".to_string(),
        "Use type hints for function signatures".to_string(),
        "Prefer f-strings over .format()".to_string(),
        "Use context managers for resources".to_string(),
    ]);

    guidelines.security_checks.extend([
        "No eval() or exec() with untrusted input".to_string(),
        "Use parameterized queries for database operations".to_string(),
        "Validate file paths to prevent path traversal".to_string(),
        "Check pickle/yaml.load usage for untrusted data".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Use generators for large data processing".to_string(),
        "Consider list comprehensions over loops".to_string(),
        "Profile before optimizing".to_string(),
    ]);

    guidelines.testing_checks.extend([
        "Use pytest fixtures for test setup".to_string(),
        "Mock external dependencies".to_string(),
        "Test exception handling".to_string(),
    ]);

    guidelines.idioms.extend([
        "Use Pythonic idioms (EAFP over LBYL)".to_string(),
        "Leverage standard library (itertools, collections)".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid mutable default arguments".to_string(),
        "Don't use bare except clauses".to_string(),
        "Avoid global state".to_string(),
    ]);

    if stack.frameworks.contains(&"Django".to_string()) {
        guidelines = add_django_guidelines(guidelines);
    }
    if stack.frameworks.contains(&"FastAPI".to_string()) {
        guidelines = add_fastapi_guidelines(guidelines);
    }
    if stack.frameworks.contains(&"Flask".to_string()) {
        guidelines = add_flask_guidelines(guidelines);
    }

    guidelines
}

/// Add Django-specific guidelines
fn add_django_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use Django ORM effectively".to_string(),
        "Follow Django coding style".to_string(),
        "Use class-based views appropriately".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Use Django's CSRF protection".to_string(),
        "Validate forms properly".to_string(),
        "Use Django's authentication system".to_string(),
    ]);

    guidelines
}

/// Add FastAPI-specific guidelines
fn add_fastapi_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use Pydantic models for validation".to_string(),
        "Define proper response models".to_string(),
        "Use dependency injection".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Implement proper OAuth2/JWT handling".to_string(),
        "Use HTTPSRedirectMiddleware".to_string(),
    ]);

    guidelines
}

/// Add Flask-specific guidelines
fn add_flask_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use Blueprints for organization".to_string(),
        "Use Flask-SQLAlchemy properly".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Set SECRET_KEY securely".to_string(),
        "Use flask-talisman for security headers".to_string(),
    ]);

    guidelines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_python_guidelines() {
        let stack = ProjectStack {
            primary_language: "Python".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: true,
            test_framework: Some("pytest".to_string()),
            package_manager: Some("pip".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Python-specific checks
        assert!(guidelines.quality_checks.iter().any(|c| c.contains("PEP")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("eval")));
    }

    #[test]
    fn test_python_django_guidelines() {
        let stack = ProjectStack {
            primary_language: "Python".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Django".to_string()],
            has_tests: true,
            test_framework: Some("pytest".to_string()),
            package_manager: Some("pip".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Django-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Django")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("CSRF")));
    }

    #[test]
    fn test_fastapi_guidelines() {
        let stack = ProjectStack {
            primary_language: "Python".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["FastAPI".to_string()],
            has_tests: true,
            test_framework: Some("pytest".to_string()),
            package_manager: Some("pip".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have FastAPI-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Pydantic")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("OAuth2") || c.contains("JWT")));
    }

    #[test]
    fn test_flask_guidelines() {
        let stack = ProjectStack {
            primary_language: "Python".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Flask".to_string()],
            has_tests: false,
            test_framework: None,
            package_manager: Some("pip".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Flask-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Blueprint")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("SECRET_KEY")));
    }
}
