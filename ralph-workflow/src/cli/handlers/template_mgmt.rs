//! Template management CLI handler.
//!
//! Provides commands for:
//! - Initializing user templates directory
//! - Listing all templates with metadata
//! - Showing template content and variables
//! - Validating templates for syntax errors
//! - Extracting variables from templates
//! - Rendering templates for testing

use std::collections::BTreeMap;

use itertools::Itertools;

use crate::cli::args::TemplateCommands;
use crate::logger::Colors;
use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_catalog;
use crate::prompts::template_registry::TemplateRegistry;
use crate::prompts::{
    extract_metadata, extract_partials, extract_variables, validate_template, Template,
};

trait StdIoWriteCompat {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()>;
}

impl<T: std::io::Write> StdIoWriteCompat for T {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()> {
        std::io::Write::write_fmt(self, args)
    }
}

/// Get all available templates as a map of name -> (content, description).
fn get_all_templates() -> BTreeMap<String, (String, String)> {
    template_catalog::get_templates_map()
}

include!("template_mgmt/validate.rs");
include!("template_mgmt/list.rs");
include!("template_mgmt/show.rs");
include!("template_mgmt/variables.rs");
include!("template_mgmt/render.rs");
include!("template_mgmt/formatting.rs");
include!("template_mgmt/init.rs");
include!("template_mgmt/dispatch.rs");

#[cfg(test)]
include!("template_mgmt/tests.rs");
