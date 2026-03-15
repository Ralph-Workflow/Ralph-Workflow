//! JavaScript and TypeScript review guidelines
//!
//! Contains guidelines for JavaScript/TypeScript projects including React, Vue, Angular,
//! Node.js backends (Express, Fastify, `NestJS`), and SSR frameworks (Next.js, Nuxt).

use super::base::ReviewGuidelines;
use crate::language_detector::ProjectStack;

/// Add JavaScript-specific guidelines to the review
pub fn add_javascript_guidelines(guidelines: &mut ReviewGuidelines, stack: &ProjectStack) {
    guidelines.quality_checks.extend([
        "Use const/let, never var".to_string(),
        "Handle Promise rejections".to_string(),
        "Use async/await over raw Promises".to_string(),
        "Avoid deeply nested callbacks".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Sanitize user input before DOM insertion".to_string(),
        "Use Content Security Policy headers".to_string(),
        "Validate data from external APIs".to_string(),
        "Check for prototype pollution vulnerabilities".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Debounce/throttle frequent event handlers".to_string(),
        "Use appropriate data structures".to_string(),
        "Minimize DOM manipulation".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid == for comparisons (use ===)".to_string(),
        "Don't mutate function arguments".to_string(),
        "Avoid synchronous I/O in Node.js".to_string(),
    ]);

    // Add frontend guidelines if using React or Vue
    if stack.frameworks.iter().any(|f| f == "React" || f == "Vue") {
        add_frontend_guidelines(guidelines);
    }

    // Add framework-specific guidelines
    add_framework_guidelines(guidelines, stack);
}

/// Add TypeScript-specific guidelines to the review
pub fn add_typescript_guidelines(guidelines: &mut ReviewGuidelines, stack: &ProjectStack) {
    // First add all JavaScript guidelines
    add_javascript_guidelines(guidelines, stack);

    // Then add TypeScript-specific guidelines
    guidelines.quality_checks.extend([
        "Use strict TypeScript mode".to_string(),
        "Prefer interfaces over type aliases for objects".to_string(),
        "Use explicit return types for public functions".to_string(),
        "Avoid 'any' type; use 'unknown' if needed".to_string(),
    ]);

    guidelines.idioms.extend([
        "Use union types for discriminated unions".to_string(),
        "Leverage type inference where clear".to_string(),
        "Use generics appropriately".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Don't use 'as' casts to bypass type checking".to_string(),
        "Avoid non-null assertions (!) without justification".to_string(),
    ]);
}

/// Add frontend-specific guidelines (React/Vue)
fn add_frontend_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Components are properly modularized".to_string(),
        "State management is predictable".to_string(),
        "Accessibility (a11y) is considered".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Avoid unnecessary re-renders".to_string(),
        "Use lazy loading for large components".to_string(),
        "Optimize bundle size".to_string(),
    ]);
}

/// Add framework-specific guidelines based on detected frameworks
fn add_framework_guidelines(guidelines: &mut ReviewGuidelines, stack: &ProjectStack) {
    for framework in &stack.frameworks {
        match framework.as_str() {
            "React" => add_react_guidelines(guidelines),
            "Vue" => add_vue_guidelines(guidelines),
            "Angular" => add_angular_guidelines(guidelines),
            "Express" | "Fastify" | "NestJS" => add_node_backend_guidelines(guidelines),
            "Next.js" | "Nuxt" => add_ssr_framework_guidelines(guidelines),
            _ => {}
        }
    }
}

/// Add React-specific guidelines
fn add_react_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Use hooks correctly (rules of hooks)".to_string(),
        "Properly manage component lifecycle".to_string(),
        "Use React.memo for expensive renders".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid prop drilling (use context or state management)".to_string(),
        "Don't mutate state directly".to_string(),
        "Avoid inline functions in render".to_string(),
    ]);
}

/// Add Vue-specific guidelines
fn add_vue_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Use Composition API for complex logic".to_string(),
        "Follow Vue style guide".to_string(),
        "Use computed properties appropriately".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid watchers when computed works".to_string(),
        "Don't directly mutate props".to_string(),
    ]);
}

/// Add Angular-specific guidelines
fn add_angular_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Use OnPush change detection where possible".to_string(),
        "Follow Angular style guide".to_string(),
        "Use RxJS operators effectively".to_string(),
    ]);

    guidelines
        .security_checks
        .push("Use Angular's built-in sanitization".to_string());

    guidelines.anti_patterns.extend([
        "Avoid subscribing without unsubscribing".to_string(),
        "Don't use any type".to_string(),
    ]);
}

/// Add Node.js backend framework guidelines (Express, Fastify, `NestJS`)
fn add_node_backend_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Use middleware pattern effectively".to_string(),
        "Handle errors in middleware".to_string(),
        "Use environment variables for config".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Use helmet for security headers".to_string(),
        "Implement rate limiting".to_string(),
        "Validate request body schema".to_string(),
    ]);
}

/// Add SSR framework guidelines (Next.js, Nuxt)
fn add_ssr_framework_guidelines(guidelines: &mut ReviewGuidelines) {
    guidelines.quality_checks.extend([
        "Use appropriate rendering strategy (SSR/SSG/ISR)".to_string(),
        "Handle hydration correctly".to_string(),
        "Optimize for Core Web Vitals".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Minimize client-side JavaScript".to_string(),
        "Use image optimization".to_string(),
    ]);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_javascript_guidelines() {
        let stack = ProjectStack {
            primary_language: "JavaScript".to_string(),
            secondary_languages: vec![],
            frameworks: vec![],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_javascript_guidelines(&mut guidelines, &stack);

        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("const/let")));
        assert!(guidelines.anti_patterns.iter().any(|c| c.contains("===")));
    }

    #[test]
    fn test_typescript_react_guidelines() {
        let stack = ProjectStack {
            primary_language: "TypeScript".to_string(),
            secondary_languages: vec!["JavaScript".to_string()],
            frameworks: vec!["React".to_string(), "Next.js".to_string()],
            has_tests: true,
            test_framework: Some("Jest".to_string()),
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_typescript_guidelines(&mut guidelines, &stack);

        // Should have TypeScript checks
        assert!(guidelines.quality_checks.iter().any(|c| c.contains("any")));
        // Should have React checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("hooks")));
        // Should have Next.js checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("SSR") || c.contains("rendering")));
    }

    #[test]
    fn test_vue_guidelines() {
        let stack = ProjectStack {
            primary_language: "JavaScript".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Vue".to_string()],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_javascript_guidelines(&mut guidelines, &stack);

        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("Composition API")));
    }

    #[test]
    fn test_angular_guidelines() {
        let stack = ProjectStack {
            primary_language: "TypeScript".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Angular".to_string()],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_typescript_guidelines(&mut guidelines, &stack);

        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("OnPush") || c.contains("RxJS")));
    }

    #[test]
    fn test_express_guidelines() {
        let stack = ProjectStack {
            primary_language: "JavaScript".to_string(),
            secondary_languages: vec![],
            frameworks: vec!["Express".to_string()],
            has_tests: false,
            test_framework: None,
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_javascript_guidelines(&mut guidelines, &stack);

        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("middleware")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("helmet")));
    }

    #[test]
    fn test_nextjs_guidelines() {
        let stack = ProjectStack {
            primary_language: "TypeScript".to_string(),
            secondary_languages: vec!["JavaScript".to_string()],
            frameworks: vec!["Next.js".to_string()],
            has_tests: true,
            test_framework: Some("Jest".to_string()),
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_typescript_guidelines(&mut guidelines, &stack);

        // Should have SSR framework guidelines
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("SSR") || c.contains("rendering") || c.contains("hydration")));
    }

    #[test]
    fn test_multiple_frameworks_combines_guidelines() {
        let stack = ProjectStack {
            primary_language: "TypeScript".to_string(),
            secondary_languages: vec!["JavaScript".to_string()],
            frameworks: vec!["React".to_string(), "Express".to_string()],
            has_tests: true,
            test_framework: Some("Jest".to_string()),
            package_manager: Some("Bun".to_string()),
        };

        let mut guidelines = ReviewGuidelines::default();
        add_typescript_guidelines(&mut guidelines, &stack);

        // Should have React-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("hooks")));

        // Should have Express-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("middleware")));
    }
}
