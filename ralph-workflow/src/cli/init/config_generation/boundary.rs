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

fn print_unknown_template_error(template_name: &str, colors: Colors) {
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
        similar.into_iter().for_each(|(name, score)| {
            let _ = writeln!(std::io::stdout(), "  - {} ({}% match)", name, score);
        });
    }
}

pub fn create_prompt_from_template<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    use crate::cli::diagnostics_domain::validate_template_name;

    let validation = validate_template_name(template_name);
    if matches!(
        validation,
        crate::cli::diagnostics_domain::TemplateValidation::Unknown { .. }
    ) {
        print_unknown_template_error(template_name, colors);
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

pub fn handle_init_state_inference_with_env<R: ConfigEnvironment>(
    config_path: &Path,
    prompt_path: &Path,
    template_arg: Option<&str>,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    let has_config = env.file_exists(config_path);
    let has_prompt = env.file_exists(prompt_path);
    let action =
        crate::cli::diagnostics_domain::determine_init_action(has_config, has_prompt, template_arg);

    match action {
        crate::cli::diagnostics_domain::InitFileState::BothExist => {
            handle_both_exists(prompt_path, colors, env)
        }
        crate::cli::diagnostics_domain::InitFileState::ConfigOnly => {
            handle_config_only(prompt_path, colors, env)
        }
        crate::cli::diagnostics_domain::InitFileState::PromptOnly => {
            handle_prompt_only(colors, env)
        }
        crate::cli::diagnostics_domain::InitFileState::NeitherExists => {
            handle_neither_exists(prompt_path, colors, env)
        }
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

    let can_prompt = can_prompt_user();
    let template_name = if can_prompt {
        prompt_for_template(colors)
    } else {
        None
    };

    match crate::cli::diagnostics_domain::determine_config_only_next_action(
        can_prompt,
        template_name,
    ) {
        crate::cli::diagnostics_domain::ConfigOnlyNextAction::CreateFromTemplate(name) => {
            create_prompt_from_template(&name, prompt_path, false, colors, env)
        }
        crate::cli::diagnostics_domain::ConfigOnlyNextAction::CreateMinimal => {
            let content = create_minimal_prompt_md();
            env.write_file(prompt_path, &content)?;
            let _ = writeln!(
                std::io::stdout(),
                "{}Created minimal PROMPT.md (non-interactive mode){}",
                colors.green(),
                colors.reset()
            );
            Ok(false)
        }
        crate::cli::diagnostics_domain::ConfigOnlyNextAction::Skip => Ok(false),
    }
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

    let can_prompt = can_prompt_user();
    let template_name = if can_prompt {
        prompt_for_template(colors)
    } else {
        None
    };

    match crate::cli::diagnostics_domain::determine_neither_exists_next_action(
        can_prompt,
        template_name,
    ) {
        crate::cli::diagnostics_domain::NeitherExistsNextAction::CreateFromTemplate(name) => {
            create_prompt_from_template(&name, prompt_path, false, colors, env)
        }
        crate::cli::diagnostics_domain::NeitherExistsNextAction::CreateMinimal => {
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
        crate::cli::diagnostics_domain::NeitherExistsNextAction::Skip => Ok(true),
    }
}

pub fn handle_init_template_arg_at_path_with_env<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    create_prompt_from_template(template_name, prompt_path, false, colors, env)
}
