//! Template validation and inspection module.
//!
//! Provides functionality for validating template syntax, extracting variables,
//! and checking template integrity.
//!
//! This module is organized into sub-modules:
//! - `template_types`: Type definitions for validation results and errors
//! - `template_extraction`: Extraction of variables, partials, and metadata
//! - `syntax_validation`: Syntax checking for template structure

use std::collections::HashSet;

// Sub-modules
#[path = "syntax_validation.rs"]
mod syntax_validation;
#[path = "template_extraction.rs"]
mod template_extraction;
#[path = "template_types.rs"]
mod template_types;

// Re-export public types and functions that are currently used
// Note: TemplateMetadata and VariableInfo are defined in template_types.rs
// but not re-exported here because they're not currently used by any consumers.
// If needed in the future, they can be added to this re-export list.
pub use syntax_validation::validate_syntax;
pub use template_extraction::{extract_metadata, extract_partials, extract_variables};
pub use template_types::{
    RenderedPromptError, RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    TemplateVariablesInvalidError, ValidationError, ValidationResult, ValidationWarning,
};

/// Validate a complete template.
///
/// Performs comprehensive validation including syntax checking,
/// variable extraction, and partial reference validation.
#[must_use]
pub fn validate_template<S: std::hash::BuildHasher>(
    content: &str,
    available_partials: &HashSet<String, S>,
) -> ValidationResult {
    // Validate syntax
    let syntax_errors = validate_syntax(content);
    let has_syntax_errors = !syntax_errors.is_empty();

    // Extract variables and partials
    let variables = extract_variables(content);
    let partials = extract_partials(content);

    // Check for missing partials - functional style with partition
    let (missing_partials, valid_partials): (Vec<_>, Vec<_>) = partials
        .iter()
        .partition(|partial| !available_partials.contains(*partial));

    let partial_errors: Vec<ValidationError> = missing_partials
        .into_iter()
        .map(|partial| ValidationError::PartialNotFound {
            name: (*partial).clone(),
        })
        .collect();

    // Convert valid partials to owned strings
    let valid_partials: Vec<String> = valid_partials.into_iter().map(|s| (*s).clone()).collect();

    // Check for variables without defaults - functional style
    let warnings: Vec<ValidationWarning> = variables
        .iter()
        .filter(|var| !var.has_default)
        .map(|var| ValidationWarning::VariableMayError {
            name: var.name.clone(),
        })
        .collect();

    let is_valid = !has_syntax_errors && partial_errors.is_empty();

    ValidationResult {
        is_valid,
        variables,
        partials: valid_partials,
        errors: syntax_errors.into_iter().chain(partial_errors).collect(),
        warnings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_template_complete() {
        let content = "Hello {{NAME|default=\"Guest\"}}";
        let partials = HashSet::new();
        let result = validate_template(content, &partials);

        assert!(result.is_valid);
        assert_eq!(result.variables.len(), 1);
        assert!(result.errors.is_empty());
    }

    #[test]
    fn test_validate_template_with_missing_partial() {
        let content = "{{> missing_partial}}";
        let partials = HashSet::new();
        let result = validate_template(content, &partials);

        assert!(!result.is_valid);
        assert!(!result.errors.is_empty());
    }

    #[test]
    fn test_validate_template_ignores_partials_inside_comments() {
        let content = "{# {{> commented_partial}} #}\nHello {{NAME|default=\"Guest\"}}";
        let partials = HashSet::new();
        let result = validate_template(content, &partials);

        assert!(
            result.is_valid,
            "commented-out partials should not trigger validation errors"
        );
        assert!(
            result.errors.is_empty(),
            "no PartialNotFound errors should be emitted for comment content"
        );
        assert!(
            result.partials.is_empty(),
            "commented partials should not be extracted"
        );
    }
}
