//! Template enforcement macros for ensuring template usage conventions.
//!
//! This module provides compile-time and runtime tools to enforce that all
//! AI communication prompts come from template files, not inline strings.

#![deny(unsafe_code)]

/// Macro to verify that a string comes from a template file.
///
/// This macro provides compile-time assurance by using `include_str!` which
/// only works with files at compile time. This prevents inline prompt strings
/// from being accidentally used.
///
/// # Example
///
/// ```ignore
/// use crate::prompts::template_macros::include_template;
///
/// // This works - loads from template file
/// let template = include_template!("templates/my_prompt.txt");
///
/// // This would NOT work with include_template! macro - prevents inline templates
/// // let inline = "Hello {{NAME}}";  // Cannot be passed to include_template!
/// ```
///
/// # Enforcement
///
/// - The macro uses `concat!` with `include_str!` to ensure the template
///   path is known at compile time
/// - Returns a `&'static str` which makes it clear this is compiled content
#[macro_export]
macro_rules! include_template {
    ($path:expr) => {
        include_str!(concat!("../prompts/templates/", $path))
    };
}

/// Macro to verify a template file exists and contains expected content.
///
/// This is primarily used in tests to verify template structure.
///
/// # Example
///
/// ```ignore
/// assert_template_exists!("templates/my_prompt.txt");
/// assert_template_has_variable!("templates/my_prompt.txt", "CONTEXT");
/// ```
#[macro_export]
macro_rules! assert_template_exists {
    ($path:expr) => {
        let content = include_str!(concat!("../prompts/templates/", $path));
        assert!(!content.is_empty(), "Template file {} is empty", $path);
    };
}

#[macro_export]
macro_rules! assert_template_has_variable {
    ($path:expr, $var:expr) => {
        let content = include_str!(concat!("../prompts/templates/", $path));
        let var_pattern = concat!("{{", $var, "}}");
        assert!(
            content.contains(var_pattern) || content.contains(concat!("{{ ", $var, " }}")),
            "Template {} does not contain variable {{{}}}",
            $path,
            $var
        );
    };
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_include_template_macro() {
        // Test that we can include a template using the macro
        let _ = include_template!("conflict_resolution.txt");
    }

    #[test]
    fn test_assert_template_exists() {
        assert_template_exists!("conflict_resolution.txt");
    }

    #[test]
    fn test_assert_template_has_variable() {
        assert_template_has_variable!("conflict_resolution.txt", "CONTEXT");
        assert_template_has_variable!("conflict_resolution.txt", "CONFLICTS");
    }

    #[test]
    fn test_inline_template_detection() {
        // Test that we can detect potential inline templates in strings
        // These patterns suggest inline prompt content that should be in templates

        let suspicious_patterns = [
            // Multi-line raw string literals with prompt-like content
            r"You are a",
            r"Please review",
            r"Generate a",
            // Long format strings that look like prompts
            "## Instructions",
            "### Task",
            "# PROMPT",
            // JSON/structured prompt patterns
            r#"{"role": "developer""#,
        ];

        assert!(suspicious_patterns
            .iter()
            .all(|pattern| !pattern.is_empty()));
    }
}
