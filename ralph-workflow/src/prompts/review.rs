//! Review and fix result prompts with XML output format.
//!
//! Prompts for review and fix result generation using XML format with XSD validation.

use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_engine::Template;
use crate::prompts::template_variables::capability_template_variables;
use crate::prompts::{
    RenderedTemplate, SessionCapabilities, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
};
use crate::workspace::Workspace;
use std::collections::HashMap;

include!("review/review_prompts.rs");
include!("review/fix_prompts.rs");

#[cfg(test)]
mod io_tests;

#[cfg(test)]
mod tests;
