//! Ruby-specific review guidelines
//!
//! Contains guidelines for Ruby projects including Rails and Sinatra frameworks.

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add Ruby-specific guidelines to the review
pub fn add_guidelines(guidelines: ReviewGuidelines, stack: &ProjectStack) -> ReviewGuidelines {
    let base = ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Follow Ruby style guide (rubocop)".to_string(),
                "Use meaningful variable names".to_string(),
                "Keep methods under 10 lines when possible".to_string(),
                "Use symbols instead of strings for keys".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Use parameterized queries (avoid string interpolation in SQL)".to_string(),
                "Escape output in views".to_string(),
                "Validate strong parameters".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid monkey patching core classes".to_string(),
                "Don't use eval with user input".to_string(),
                "Avoid deeply nested conditionals".to_string(),
            ])
            .collect(),
        ..guidelines
    };

    let with_rails = if stack.frameworks.contains(&"Rails".to_string()) {
        add_rails_guidelines(base)
    } else {
        base
    };

    if stack.frameworks.contains(&"Sinatra".to_string()) {
        add_sinatra_guidelines(with_rails)
    } else {
        with_rails
    }
}

fn add_rails_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Follow Rails conventions".to_string(),
                "Use Active Record validations".to_string(),
                "Keep controllers thin".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Use strong parameters".to_string(),
                "Protect against mass assignment".to_string(),
                "Use Rails' built-in CSRF protection".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

fn add_sinatra_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use modular Sinatra style for larger apps".to_string(),
                "Organize routes logically".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Enable rack protection".to_string(),
                "Set session secret securely".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ruby_guidelines() {
        let stack = ProjectStack {
            primary_language: "Ruby".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bundler".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Ruby-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("rubocop") || c.contains("Ruby")));
        assert!(guidelines
            .anti_patterns
            .iter()
            .any(|c| c.contains("monkey patching")));
    }

    #[test]
    fn test_rails_guidelines() {
        let stack = ProjectStack {
            primary_language: "Ruby".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Rails".to_string()],
            has_tests: true,
            test_framework: Some("RSpec".to_string()),
            package_manager: Some("Bundler".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Rails-specific security checks
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("strong parameters") || c.contains("CSRF")));
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Rails conventions")));
    }

    #[test]
    fn test_sinatra_guidelines() {
        let stack = ProjectStack {
            primary_language: "Ruby".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Sinatra".to_string()],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bundler".to_string()),
        };

        let guidelines = add_guidelines(ReviewGuidelines::default(), &stack);

        // Should have Sinatra-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("modular") || c.contains("routes")));
    }
}
