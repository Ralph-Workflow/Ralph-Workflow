//! Interactive template selection module.
//!
//! Provides functionality for prompting users to select a PROMPT.md template
//! when one doesn't exist and interactive mode is enabled.
//!
//! # Architecture Note
//!
//! This module operates at the CLI layer (pre-pipeline) and uses boundary modules
//! for file and terminal operations. This is acceptable per the effect-system architecture.

use std::io::Write;
use std::path::Path;

use crate::cli::handlers::boundary::{io as fs_io, terminal as term};
use crate::logger::Colors;
use crate::templates::{get_template, list_templates};

/// Result of interactive template selection.
///
/// * `Some(template_name)` - User selected a template
/// * `None` - User declined or input was not a terminal
pub type TemplateSelectionResult = Option<String>;

/// Prompt the user to select a template when PROMPT.md is missing.
///
/// This function:
/// 1. Displays a message that PROMPT.md is missing
/// 2. Asks if the user wants to create one from a template
/// 3. If yes, displays available templates
/// 4. Prompts for template selection (with default to feature-spec)
/// 5. Returns the selected template name or None if declined
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
///
/// # Returns
///
/// * `Some(template_name)` - User selected a template
/// * `None` - User declined, input was not a terminal, or input errored/ended
#[must_use]
pub fn prompt_template_selection(colors: Colors) -> TemplateSelectionResult {
    if !term::is_terminal() {
        return None;
    }

    let stdout = term::stdout();
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "{}PROMPT.md not found.{}",
        colors.yellow(),
        colors.reset()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "PROMPT.md contains your task specification for the AI agents."
    );
    let _ = write!(
        stdout,
        "Would you like to create one from a template? [Y/n]: "
    );
    if term::flush_stdout().is_err() {
        return None;
    }

    let input = term::read_line();
    let response = input.unwrap_or_default().trim().to_lowercase();

    if response == "n" || response == "no" || response == "skip" {
        return None;
    }

    let _ = writeln!(stdout);
    let _ = writeln!(stdout, "Available templates:");

    let templates = list_templates();

    templates.iter().for_each(|(name, description)| {
        let _ = writeln!(
            stdout,
            "  {}{}{}  {}{}{}",
            colors.cyan(),
            name,
            colors.reset(),
            colors.dim(),
            description,
            colors.reset()
        );
    });
    let _ = writeln!(stdout);

    let _ = write!(
        stdout,
        "Select template {}[default: feature-spec]{}: ",
        colors.dim(),
        colors.reset()
    );
    if term::flush_stdout().is_err() {
        return None;
    }

    let template_input = term::read_line();
    let binding = template_input.unwrap_or_default();
    let template_name = binding.trim();

    let selected = if template_name.is_empty() {
        "feature-spec"
    } else {
        template_name
    };

    if get_template(selected).is_none() {
        let _ = writeln!(
            stdout,
            "{}Unknown template: '{}'. Using feature-spec as default.{}",
            colors.yellow(),
            selected,
            colors.reset()
        );
        return Some("feature-spec".to_string());
    }

    Some(selected.to_string())
}

/// Create PROMPT.md from the selected template.
///
/// # Arguments
///
/// * `template_name` - The name of the template to use
/// * `colors` - Terminal color configuration for output
///
/// # Returns
///
/// * `Ok(())` - File created successfully
/// * `Err(e)` - Failed to create file
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn create_prompt_from_template(template_name: &str, colors: Colors) -> anyhow::Result<()> {
    let prompt_path = Path::new("PROMPT.md");

    if fs_io::exists(prompt_path) {
        let stdout = term::stdout();
        let _ = writeln!(
            stdout,
            "{}PROMPT.md already exists. Skipping creation.{}",
            colors.yellow(),
            colors.reset()
        );
        return Ok(());
    }

    let Some(template) = get_template(template_name) else {
        return Err(anyhow::anyhow!("Template '{template_name}' not found"));
    };

    let content = template.content();
    fs_io::write(prompt_path, content)?;

    let stdout = term::stdout();
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "{}Created PROMPT.md from template: {}{}{}",
        colors.green(),
        colors.bold(),
        template_name,
        colors.reset()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(
        stdout,
        "Template: {}{}{}  {}",
        colors.cyan(),
        template.name(),
        colors.reset(),
        template.description()
    );
    let _ = writeln!(stdout);
    let _ = writeln!(stdout, "Next steps:");
    let _ = writeln!(stdout, " 1. Edit PROMPT.md with your task details");
    let _ = writeln!(stdout, " 2. Run ralph again with your commit message");

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_template_by_name() {
        assert!(get_template("feature-spec").is_some());
        assert!(get_template("bug-fix").is_some());
        assert!(get_template("refactor").is_some());
        assert!(get_template("test").is_some());
        assert!(get_template("docs").is_some());
        assert!(get_template("quick").is_some());
        assert!(get_template("nonexistent").is_none());
    }

    #[test]
    fn test_template_has_required_content() {
        for (name, _) in list_templates() {
            if let Some(template) = get_template(name) {
                let content = template.content();
                assert!(
                    content.contains("## Goal"),
                    "Template {name} missing Goal section"
                );
                assert!(
                    content.contains("Acceptance") || content.contains("## Acceptance Checks"),
                    "Template {name} missing Acceptance section"
                );
            }
        }
    }
}
