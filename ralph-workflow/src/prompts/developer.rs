//! Developer prompts.
//!
//! Prompts for developer agent actions including iteration and planning.

use std::collections::HashMap;
use std::path::Path;

use super::partials::get_shared_partials;
use super::template_context::TemplateContext;
use super::template_engine::Template;
use super::types::ContextLevel;
use crate::workspace::Workspace;

include!("developer/context_injection.rs");
include!("developer/system_prompt_iteration.rs");
include!("developer/system_prompt_planning.rs");

#[cfg(test)]
mod tests {
    include!("developer/tests.rs");
}
