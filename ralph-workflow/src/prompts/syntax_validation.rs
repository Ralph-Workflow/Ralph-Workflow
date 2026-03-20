//! Template syntax validation.
//!
//! This module re-exports imperative validation code from the io boundary module.

pub fn validate_syntax(content: &str) -> Vec<super::template_types::ValidationError> {
    crate::prompts::io::validate_syntax(content)
}
