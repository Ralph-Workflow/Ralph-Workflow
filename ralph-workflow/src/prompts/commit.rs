//! Commit and fix prompts.
//!
//! Prompts for commit message generation and fix actions.

use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_engine::Template;
use crate::prompts::{RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource};
use crate::workspace::Workspace;
use std::collections::HashMap;

const COMMIT_MESSAGE_XSD_SCHEMA: &str =
    include_str!("../files/llm_output_extraction/commit_message.xsd");

use crate::files::result_extraction::extract_file_paths_from_issues;

include!("commit/fix_prompts.rs");
include!("commit/commit_message_generate.rs");
include!("commit/commit_xsd_retry.rs");

#[cfg(test)]
mod tests;
