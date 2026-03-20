//! Template variable and partial extraction.
//!
//! This module re-exports imperative parsing code from the io boundary module.

pub fn extract_metadata(content: &str) -> super::template_types::TemplateMetadata {
    crate::prompts::io::extract_metadata(content)
}

pub fn extract_partials(content: &str) -> Vec<String> {
    crate::prompts::io::extract_partials(content)
}

pub fn extract_variables(content: &str) -> Vec<super::template_types::VariableInfo> {
    crate::prompts::io::extract_variables(content)
}
