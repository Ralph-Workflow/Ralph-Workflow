//! Template engine for rendering prompt templates.
//!
//! This module provides a template variable replacement system for prompt templates
//! with support for variables, partials, comments, conditionals, loops, and defaults.
//!
//! The imperative parsing and rendering code lives in the runtime/ boundary module
//! to satisfy the functional programming lints.

pub use crate::prompts::runtime::parser::{extract_metadata, extract_partials, extract_variables};
pub use crate::prompts::runtime::Template;
pub use crate::prompts::template_registry::TemplateError;
pub use crate::prompts::template_validator::{
    RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
};
