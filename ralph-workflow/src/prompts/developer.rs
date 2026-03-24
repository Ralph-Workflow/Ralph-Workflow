//! Developer prompts.
//!
//! Prompts for developer agent actions including iteration and planning.

use std::collections::HashMap;
use std::path::Path;

use super::partials::get_shared_partials;
use super::template_context::TemplateContext;
use super::template_engine::Template;
use super::template_variables::capability_template_variables;
use super::types::ContextLevel;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::workspace::Workspace;

include!("developer/context_injection.rs");
include!("developer/system_prompt_iteration.rs");
include!("developer/system_prompt_planning.rs");

#[cfg(test)]
mod io_tests {
    include!("developer/io_tests.rs");
}
