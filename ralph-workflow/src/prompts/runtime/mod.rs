//! Runtime boundary module for prompts.
//!
//! This module contains imperative code (template parsing, rendering) that cannot
//! be easily converted to functional style. It satisfies the dylint boundary-module
//! check.

pub mod parser;
pub mod renderer;
pub mod resume_note;
pub mod template_engine;

use std::fmt;

/// Template for rendering prompts with variable substitution.
pub struct Template {
    pub content: String,
}

impl Template {
    #[must_use]
    pub fn new(content: &str) -> Self {
        Self {
            content: content.to_string(),
        }
    }
}

impl fmt::Debug for Template {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Template")
            .field(
                "content",
                &self.content.chars().take(50).collect::<String>(),
            )
            .finish()
    }
}
