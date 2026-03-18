//! Template engine runtime - rendering logic.
//!
//! This module contains imperative rendering code.

pub use crate::prompts::io::extract_metadata;
pub use crate::prompts::io::extract_partials;
pub use crate::prompts::io::extract_variables;
pub use crate::prompts::io::validate_syntax;

pub use crate::prompts::runtime::Template;
pub use crate::prompts::template_registry::TemplateError;
pub use crate::prompts::template_validator::{
    RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
};
