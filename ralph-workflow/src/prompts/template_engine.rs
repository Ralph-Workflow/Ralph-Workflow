//! Template engine for rendering prompt templates.
//!
//! This module provides a template variable replacement system for prompt templates
//! with support for variables, partials, comments, conditionals, loops, and defaults.
//!
//! ## Syntax
//!
//! - **Variables**: `{{VARIABLE}}` or `{{ VARIABLE }}` - replaced with values
//! - **Default values**: `{{VARIABLE|default="value"}}` - uses value if VARIABLE is missing
//! - **Conditionals**: `{% if VARIABLE %}...{% endif %}` - include content if VARIABLE is truthy
//! - **Negation**: `{% if !VARIABLE %}...{% endif %}` - include content if VARIABLE is falsy
//! - **Loops**: `{% for item in ITEMS %}...{% endfor %}` - iterate over comma-separated values
//! - **Partials**: `{{> partial_name}}` or `{{> partial/path}}` - includes another template
//! - **Comments**: `{# comment #}` - stripped from output, useful for documentation
//!
//! ## Partials System
//!
//! Partials allow sharing common template sections across multiple templates.
//! When a partial is referenced, it's looked up from the provided partials map
//! and recursively rendered with the same variables.
//!
//! Example partial include:
//! ```text
//! {{> shared/_critical_header}}
//! ```
//!
//! The partials system:
//! - Detects and prevents circular references
//! - Provides clear error messages for missing partials
//! - Supports hierarchical naming (dot notation or path-style)

use std::collections::HashMap;

/// Error type for template operations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TemplateError {
    /// Required variable not provided.
    MissingVariable(String),
    /// Referenced partial not found in partials map.
    PartialNotFound(String),
    /// Circular reference detected in partial includes.
    CircularReference(Vec<String>),
}

impl std::fmt::Display for TemplateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingVariable(name) => write!(f, "Missing required variable: {{{{ {name} }}}}"),
            Self::PartialNotFound(name) => {
                write!(f, "Partial not found: '{{> {name}}}'")
            }
            Self::CircularReference(chain) => {
                write!(f, "Circular reference detected in partials: ")?;
                let chain_str = chain
                    .iter()
                    .map(|partial| format!("{{{{> {partial}}}}}"))
                    .collect::<Vec<_>>()
                    .join(" -> ");
                write!(f, "{chain_str}")
            }
        }
    }
}

impl std::error::Error for TemplateError {}

/// A simple template engine for prompt templates.
///
/// Templates use `{{VARIABLE}}` syntax for placeholders and `{{> partial}}` for
/// including shared templates. Variables are replaced with the provided values.
/// Comments using `{# comment #}` syntax are stripped.
///
/// # Example
///
/// ```ignore
/// let partials = HashMap::from([("header", "Common Header\n")]);
/// let template = Template::new("{{> header}}\nReview this diff:\n{{DIFF}}");
/// let variables = HashMap::from([("DIFF", "+ new line")]);
/// let rendered = template.render_with_partials(&variables, &partials)?;
/// ```
#[derive(Debug, Clone)]
pub struct Template {
    /// The template content with comments and partials processed.
    content: String,
}

impl Template {
    /// Create a template from a string.
    ///
    /// Comments (`{# ... #}`) are stripped during creation.
    /// All features are enabled by default: variables, conditionals, loops, and defaults.
    #[must_use]
    pub fn new(content: &str) -> Self {
        // Strip comments first
        let content = Self::strip_comments(content);
        Self { content }
    }
}

include!("template_engine/parser.rs");
include!("template_engine/renderer.rs");
include!("template_engine/tests.rs");
