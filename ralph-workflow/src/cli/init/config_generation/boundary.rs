//! I/O boundary for config generation.
//!
//! This module contains CLI handlers that perform console I/O.
//! According to the Boundary-First Architecture pattern, all I/O
//! operations (including console output) should live in boundary modules.
//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

use crate::config::ConfigEnvironment;
use crate::logger::Colors;
use crate::templates::{get_template, list_templates};
use std::io::Write;
use std::path::Path;

use super::global::handle_init_global_with;

use crate::cli::init::find_similar_templates;
use crate::cli::init::print_common_work_guides;
use crate::cli::init::{can_prompt_user, prompt_for_template, prompt_overwrite_confirmation};

fn create_minimal_prompt_md() -> String {
    "# Task Description\n\nDescribe what you want the AI agents to implement.\n\n## Example\n\n\"Fix the typo in the README file\"\n\n## Context\n\nProvide any relevant context about the task:\n- What problem are you trying to solve?\n- What are the acceptance criteria?\n- Are there any specific requirements or constraints?\n\n## Notes\n\n- This is a minimal PROMPT.md created by `ralph --init`\n- You can edit this file directly or use `ralph --init <work-guide>` to start from a Work Guide\n- Run `ralph --list-work-guides` to see all available Work Guides\n"
    .to_string()
}

pub fn create_prompt_from_template<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    if get_template(template_name).is_none() {
        let _ = writeln!(
            std::io::stdout(),
            "{}Unknown Work Guide: '{}'{}",
            colors.red(),
            template_name,
            colors.reset()
        );
        let similar = find_similar_templates(template_name);
        if !similar.is_empty() {
            let _ = writeln!(
                std::io::stdout(),
                "{}Did you mean:?{}",
                colors.yellow(),
                colors.reset()
            );
            for (name, score) in similar {
                let _ = writeln!(std::io::stdout(), "  - {} ({}% match)", name, score);
            }
        }
        return Ok(true);
    }

    if env.file_exists(prompt_path) && !force {
        let response = prompt_overwrite_confirmation(prompt_path, colors)?;
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

    let template = get_template(template_name).unwrap();
    env.write_file(prompt_path, template.content())?;
    let _ = writeln!(
        std::io::stdout(),
        "{}Created PROMPT.md from '{}' work guide{}",
        colors.green(),
        template.name(),
        colors.reset()
    );

    Ok(true)
}

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitInferredAction {
    BothExists,
    ConfigOnly,
    PromptOnly,
    NeitherExists,
}

pub fn handle_init_state_inference_with_env<R: ConfigEnvironment>(
    config_path: &Path,
    prompt_path: &Path,
    template_arg: Option<&str>,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let action = infer_init_action(config_path, prompt_path, env);
    let has_template = template_arg.map(|s| !s.is_empty()).unwrap_or(false);

    if has_template {
        return create_prompt_from_template(template_arg.unwrap(), prompt_path, false, colors, env);
    }

    match action {
        InitInferredAction::BothExists => handle_both_exists(prompt_path, colors, env),
        InitInferredAction::ConfigOnly => handle_config_only(prompt_path, colors, env),
        InitInferredAction::PromptOnly => handle_prompt_only(colors, env),
        InitInferredAction::NeitherExists => handle_neither_exists(prompt_path, colors, env),
    }
}

fn handle_both_exists<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let _ = writeln!(
        std::io::stdout(),
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

fn handle_config_only<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let _ = writeln!(
        std::io::stdout(),
        "{}Found existing config but no PROMPT.md{}",
        colors.yellow(),
        colors.reset()
    );
    let response = prompt_overwrite_confirmation(Path::new("Create PROMPT.md?"), colors)?;
    if !response {
        return Ok(false);
    }
    let _ = writeln!(
        std::io::stdout(),
        "\n{}Available Work Guides:{}",
        colors.blue(),
        colors.reset()
    );
    let _ = list_templates();

    if can_prompt_user() {
        if let Some(template_name) = prompt_for_template(colors) {
            return create_prompt_from_template(&template_name, prompt_path, false, colors, env);
        }
    } else {
        let content = create_minimal_prompt_md();
        env.write_file(prompt_path, &content)?;
        let _ = writeln!(
            std::io::stdout(),
            "{}Created minimal PROMPT.md (non-interactive mode){}",
            colors.green(),
            colors.reset()
        );
    }
    Ok(false)
}

fn handle_prompt_only<R: ConfigEnvironment>(colors: Colors, env: &R) -> anyhow::Result<bool> {
    let _ = writeln!(
        std::io::stdout(),
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

fn handle_neither_exists<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    handle_init_global_with(colors, env)?;

    let _ = writeln!(
        std::io::stdout(),
        "\n{}Available Work Guides:{}",
        colors.blue(),
        colors.reset()
    );
    let _ = list_templates();
    let _ = writeln!(std::io::stdout());
    print_common_work_guides(colors);

    if can_prompt_user() {
        if let Some(template_name) = prompt_for_template(colors) {
            match create_prompt_from_template(&template_name, prompt_path, false, colors, env) {
                Ok(true) => return Ok(true),
                Ok(false) => {}
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
    }

    let content = create_minimal_prompt_md();
    env.write_file(prompt_path, &content)?;
    let _ = writeln!(
        std::io::stdout(),
        "{}Created minimal PROMPT.md (non-interactive mode){}",
        colors.green(),
        colors.reset()
    );

    crate::cli::handle_extended_help();

    Ok(true)
}

pub fn handle_init_template_arg_at_path_with_env<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    create_prompt_from_template(template_name, prompt_path, false, colors, env)
}
