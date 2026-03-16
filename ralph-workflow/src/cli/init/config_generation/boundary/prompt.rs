//! PROMPT.md creation and smart init orchestration.
//!
//! Handles creating PROMPT.md from Work Guide templates and smart `--init` logic
//! that infers user intent based on existing files.

use crate::config::ConfigEnvironment;
use crate::logger::Colors;
use crate::templates::{get_template, list_templates};
use std::path::Path;

use super::super::global::handle_init_global_with;

// Import helpers from grandparent module (cli::init)
use super::super::super::{
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
        println!(
            "{}Unknown Work Guide: '{}'{}",
            colors.red(),
            template_name,
            colors.reset()
        );
        // Show similar templates
        let similar = find_similar_templates(template_name);
        if !similar.is_empty() {
            println!("{}Did you mean:?{}", colors.yellow(), colors.reset());
            for (name, score) in similar {
                println!("  - {} ({}% match)", name, score);
            }
        }
        return Ok(true);
    };

    // Check if file already exists
    if env.file_exists(prompt_path) && !force {
        let response = prompt_overwrite_confirmation(prompt_path, colors)?;
        if !response {
            println!("{}Aborted.{}", colors.yellow(), colors.reset());
            return Ok(false);
        }
    }

    // Write the template content to the file
    env.write_file(prompt_path, template.content())?;
    println!(
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
    env: &R,
) -> InitInferredAction {
    let has_config = env.file_exists(config_path);
    let has_prompt = env.file_exists(prompt_path);

    match (has_config, has_prompt) {
        (true, true) => InitInferredAction::BothExists,
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
    let action = infer_init_action(config_path, prompt_path, env);

    // Check if user provided a non-empty template name
    let has_template = template_arg.map(|s| !s.is_empty()).unwrap_or(false);

    if has_template {
        // User specified a template - create PROMPT.md from template
        return create_prompt_from_template(template_arg.unwrap(), prompt_path, false, colors, env);
    }

    // No template provided - use smart inference based on current state
    match action {
        // Both exist - offer to reinitialize
        InitInferredAction::BothExists => {
            println!(
                "{}Found existing config and PROMPT.md{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation(Path::new("Reinitialize both?"), colors)?;
            if response {
                handle_init_global_with(colors, env)?;
                create_prompt_from_template("blank", prompt_path, true, colors, env)?;
                return Ok(true);
            }
            Ok(false)
        }

        // Only config exists - offer to create PROMPT.md
        InitInferredAction::ConfigOnly => {
            println!(
                "{}Found existing config but no PROMPT.md{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation(Path::new("Create PROMPT.md?"), colors)?;
            if response {
                // Show available templates
                println!(
                    "\n{}Available Work Guides:{}",
                    colors.blue(),
                    colors.reset()
                );
                let _ = list_templates();

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
                    env.write_file(prompt_path, &content)?;
                    println!(
                        "{}Created minimal PROMPT.md (non-interactive mode){}",
                        colors.green(),
                        colors.reset()
                    );
                }
            }
            Ok(false)
        }

        // Only PROMPT.md exists - offer to create config
        InitInferredAction::PromptOnly => {
            println!(
                "{}Found existing PROMPT.md but no config{}",
                colors.yellow(),
                colors.reset()
            );
            let response = prompt_overwrite_confirmation(Path::new("Create config?"), colors)?;
            if response {
                handle_init_global_with(colors, env)?;
                return Ok(true);
            }
            Ok(false)
        }

        // Neither exists - full init
        InitInferredAction::NeitherExists => {
            // Create the config first
            handle_init_global_with(colors, env)?;

            // Show available templates
            println!(
                "\n{}Available Work Guides:{}",
                colors.blue(),
                colors.reset()
            );
            let _ = list_templates();
            println!();

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
                            println!(
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
            env.write_file(prompt_path, &content)?;
            println!(
                "{}Created minimal PROMPT.md (non-interactive mode){}",
                colors.green(),
                colors.reset()
            );

            // Show extended help for first-time users
            super::super::super::handle_extended_help();

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
