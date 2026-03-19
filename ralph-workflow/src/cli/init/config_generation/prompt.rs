//! PROMPT.md creation and smart init orchestration.
//!
//! Handles creating PROMPT.md from Work Guide templates and smart `--init` logic
//! that infers user intent based on existing files.

use crate::config::ConfigEnvironment;
use crate::logger::Colors;
use crate::templates::{get_template, list_templates};
use std::io::Write;
use std::path::Path;

use super::global::handle_init_global_with;

// Import helpers from parent module
use super::super::{
    can_prompt_user, find_similar_templates, print_common_work_guides, prompt_for_template,
    prompt_overwrite_confirmation,
};

/// Create a minimal default PROMPT.md content.
fn create_minimal_prompt_md() -> String {
    "# Task Description

Describe what you want the AI agents to implement.

## Example

\"Fix the typo in the README file\"

## Context

Provide any relevant context about the task:
- What problem are you trying to solve?
- What are the acceptance criteria?
- Are there any specific requirements or constraints?

## Notes

- This is a minimal PROMPT.md created by `ralph --init`
- You can edit this file directly or use `ralph --init <work-guide>` to start from a Work Guide
- Run `ralph --list-work-guides` to see all available Work Guides
"
    .to_string()
}

/// Create PROMPT.md from a template at the specified path.
pub fn create_prompt_from_template<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    // Validate the template exists first, before any file operations
    let Some(template) = get_template(template_name) else {
        let _ = writeln!(
            std::io::stdout(),
            "{}Unknown Work Guide: '{}'{}",
            colors.red(),
            template_name,
            colors.reset()
        );
        // Show similar templates
        let similar = find_similar_templates(template_name);
        if !similar.is_empty() {
            let _ = writeln!(
                std::io::stdout(),
                "{}Did you mean:?{}",
                colors.yellow(),
                colors.reset()
            );
            similar.into_iter().for_each(|(name, score)| {
                let _ = writeln!(std::io::stdout(), "  - {} ({}% match)", name, score);
            });
        }
        return Ok(false);
    };

    // Check if file already exists
    if env.file_exists(prompt_path) && !force {
        let response =
            prompt_overwrite_confirmation(prompt_path.to_string_lossy().as_ref(), colors)?;
        if !response {
            let _ = writeln!(
                std::io::stdout(),
                "{}Aborted.{}",
                colors.yellow(),
                colors.reset()
            );
            return Ok(false);
        }
    }

    // Write the template content to the file
    env.write_file(prompt_path, template.content().as_bytes())?;
    let _ = writeln!(
        std::io::stdout(),
        "{}Created PROMPT.md from '{}' work guide{}",
        colors.green(),
        template.name(),
        colors.reset()
    );

    Ok(true)
}

/// Infer the best init action based on existing files in the project.
///
/// Returns the appropriate init action based on what files exist.
pub fn infer_init_action<R: ConfigEnvironment>(
    config_path: &Path,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> InitInferredAction {
    let has_config = env.file_exists(config_path);
    let has_prompt = env.file_exists(prompt_path);

    match (has_config, has_prompt) {
        (true, true) => InitInferredAction::BothExist,
        (true, false) => InitInferredAction::ConfigOnly,
        (false, true) => InitInferredAction::PromptOnly,
        (false, false) => InitInferredAction::NeitherExists,
    }
}

/// Action inferred from existing files.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitInferredAction {
    /// Both config and PROMPT.md exist
    BothExists,
    /// Only config exists
    ConfigOnly,
    /// Only PROMPT.md exists
    PromptOnly,
    /// Neither exists
    NeitherExists,
}

/// Handle --init with state inference (smart init).
pub fn handle_init_state_inference_with_env<R: ConfigEnvironment>(
    config_path: &Path,
    prompt_path: &Path,
    template_arg: Option<&str>,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let action = infer_init_action(config_path, prompt_path, colors, env);

    match (action, template_arg) {
        // User specified a template - create PROMPT.md from template
        (_, Some(template_name)) => {
            create_prompt_from_template(template_name, prompt_path, false, colors, env)
        }

        // Both exist - offer to reinitialize
        (InitInferredAction::BothExists, None) => {
            let _ = writeln!(
                std::io::stdout(),
                "{}Found existing config and PROMPT.md{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation("Reinitialize both?", colors)?;
            if response {
                handle_init_global_with(config_path, true, colors, env)?;
                create_prompt_from_template("blank", prompt_path, true, colors, env)?;
                return Ok(true);
            }
            Ok(false)
        }

        // Only config exists - offer to create PROMPT.md
        (InitInferredAction::ConfigOnly, None) => {
            let _ = writeln!(
                std::io::stdout(),
                "{}Found existing config but no PROMPT.md{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation("Create PROMPT.md?", colors)?;
            if response {
                // Show available templates
                let _ = writeln!(
                    std::io::stdout(),
                    "\n{}Available Work Guides:{}",
                    colors.blue(),
                    colors.reset()
                );
                list_templates(colors);

                if can_prompt_user() {
                    if let Some(template_name) = prompt_for_template(colors) {
                        return create_prompt_from_template(
                            &template_name,
                            prompt_path,
                            false,
                            colors,
                            env,
                        );
                    }
                } else {
                    // Non-interactive: create minimal PROMPT.md
                    let content = create_minimal_prompt_md();
                    env.write_file(prompt_path, content.as_bytes())?;
                    let _ = writeln!(
                        std::io::stdout(),
                        "{}Created minimal PROMPT.md (non-interactive mode){}",
                        colors.green(),
                        colors.reset()
                    );
                }
            }
            Ok(false)
        }

        // Only PROMPT.md exists - offer to create config
        (InitInferredAction::PromptOnly, None) => {
            let _ = writeln!(
                std::io::stdout(),
                "{}Found existing PROMPT.md but no config{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation("Create config?", colors)?;
            if response {
                handle_init_global_with(config_path, false, colors, env)?;
                return Ok(true);
            }
            Ok(false)
        }

        // Neither exists - full init
        (InitInferredAction::NeitherExists, None) => {
            // Show available templates
            let _ = writeln!(
                std::io::stdout(),
                "\n{}Available Work Guides:{}",
                colors.blue(),
                colors.reset()
            );
            list_templates(colors);
            let _ = writeln!(std::io::stdout());

            // Show common Work Guides inline
            print_common_work_guides(colors);

            // Check if we're in a TTY for interactive prompting
            if can_prompt_user() {
                // Interactive mode: prompt for template selection
                if let Some(template_name) = prompt_for_template(colors) {
                    match create_prompt_from_template(
                        &template_name,
                        prompt_path,
                        false,
                        colors,
                        env,
                    ) {
                        Ok(true) => return Ok(true),
                        Ok(false) => {
                            // User declined or invalid template, fall through to show usage
                        }
                        Err(e) => {
                            let _ = writeln!(
                                std::io::stdout(),
                                "{}Failed to create PROMPT.md: {}{}",
                                colors.red(),
                                e,
                                colors.reset()
                            );
                            return Ok(false);
                        }
                    }
                }
                // User declined or entered invalid input, fall through to show usage
            }
            // Non-interactive or user declined: create minimal PROMPT.md and show help
            let content = create_minimal_prompt_md();
            env.write_file(prompt_path, content.as_bytes())?;
            let _ = writeln!(
                std::io::stdout(),
                "{}Created minimal PROMPT.md (non-interactive mode){}",
                colors.green(),
                colors.reset()
            );

            // Show extended help for first-time users
            super::super::handle_extended_help();

            Ok(true)
        }
    }
}

/// Handle --init with explicit template argument.
pub fn handle_init_template_arg_at_path_with_env<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    create_prompt_from_template(template_name, prompt_path, false, colors, env)
}
