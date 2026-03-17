//! Functional and other language review guidelines
//!
//! Contains guidelines for Elixir, Scala, and Swift projects.

use super::base::ReviewGuidelines;

/// Add Elixir-specific guidelines to the review
pub fn add_elixir_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use pattern matching effectively".to_string(),
        "Follow pipe operator conventions".to_string(),
        "Use dialyzer for type checking".to_string(),
    ]);

    guidelines.performance_checks.extend([
        "Use streams for large data processing".to_string(),
        "Consider GenServer state design".to_string(),
    ]);

    guidelines.idioms.extend([
        "Let it crash - use supervisors".to_string(),
        "Use with for happy path chaining".to_string(),
    ]);

    guidelines
}

/// Add Scala-specific guidelines to the review
pub fn add_scala_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use immutable collections".to_string(),
        "Prefer Option over null".to_string(),
        "Use pattern matching".to_string(),
        "Follow functional programming principles".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid mutable state".to_string(),
        "Don't use return statements".to_string(),
        "Avoid throwing exceptions".to_string(),
    ]);

    guidelines
}

/// Add Swift-specific guidelines to the review
pub fn add_swift_guidelines(mut guidelines: ReviewGuidelines) -> ReviewGuidelines {
    guidelines.quality_checks.extend([
        "Use optionals correctly".to_string(),
        "Follow Swift API design guidelines".to_string(),
        "Use value types where appropriate".to_string(),
        "Leverage Swift's type inference".to_string(),
    ]);

    guidelines.security_checks.extend([
        "Use Keychain for sensitive data".to_string(),
        "Validate URL schemes".to_string(),
    ]);

    guidelines.anti_patterns.extend([
        "Avoid force unwrapping (!)".to_string(),
        "Don't use implicitly unwrapped optionals unnecessarily".to_string(),
    ]);

    guidelines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_elixir_guidelines() {
        let guidelines = add_elixir_guidelines(ReviewGuidelines::default());

        // Should have Elixir-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("pattern matching") || c.contains("pipe")));
        assert!(guidelines
            .idioms
            .iter()
            .any(|c| c.contains("crash") || c.contains("supervisor")));
    }

    #[test]
    fn test_scala_guidelines() {
        let guidelines = add_scala_guidelines(ReviewGuidelines::default());

        // Should have Scala-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("immutable") || c.contains("Option")));
        assert!(guidelines
            .anti_patterns
            .iter()
            .any(|c| c.contains("mutable")));
    }

    #[test]
    fn test_swift_guidelines() {
        let guidelines = add_swift_guidelines(ReviewGuidelines::default());

        // Should have Swift-specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("optional")));
        assert!(guidelines
            .anti_patterns
            .iter()
            .any(|c| c.contains("force unwrapping") || c.contains('!')));
    }
}
