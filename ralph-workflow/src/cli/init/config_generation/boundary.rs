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

fn print_aborted(colors: Colors) {
    let _ = writeln!(
        std::io::stdout(),
        "{}Aborted.{}",
        colors.yellow(),
        colors.reset()
    );
}

fn prompt_overwrite_if_exists<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    _env: &R,
) -> anyhow::Result<bool> {
    let response = prompt_overwrite_confirmation(prompt_path, colors)?;
    if !response {
        print_aborted(colors);
    }
    Ok(response)
}

fn check_overwrite_if_needed<R: ConfigEnvironment>(
    prompt_path: &Path,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    if env.file_exists(prompt_path) && !force {
        return prompt_overwrite_if_exists(prompt_path, colors, env);
    }
    Ok(true)
}

fn write_template_and_notify<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<()> {
    let template = get_template(template_name)
        .ok_or_else(|| anyhow::anyhow!("template not found: {}", template_name))?;
    env.write_file(prompt_path, template.content())?;
    let _ = writeln!(
        std::io::stdout(),
        "{}Created PROMPT.md from '{}' work guide{}",
        colors.green(),
        template.name(),
        colors.reset()
    );
    Ok(())
}

fn validate_or_report_template(template_name: &str, colors: Colors) -> bool {
    use crate::cli::diagnostics_domain::{validate_template_name, TemplateValidation};
    if matches!(
        validate_template_name(template_name),
        TemplateValidation::Unknown
    ) {
        print_unknown_template_error(template_name, colors);
        return false;
    }
    true
}

pub fn create_prompt_from_template<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    force: bool,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    if !validate_or_report_template(template_name, colors) {
        return Ok(true);
    }

    if !check_overwrite_if_needed(prompt_path, force, colors, env)? {
        return Ok(false);
    }

    write_template_and_notify(template_name, prompt_path, colors, env)?;
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

fn create_minimal_and_notify<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
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

fn maybe_prompt_for_template(can_prompt: bool, colors: Colors) -> Option<String> {
    if can_prompt {
        prompt_for_template(colors)
    } else {
        None
    }
}

fn dispatch_config_only_action<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    use crate::cli::diagnostics_domain::ConfigOnlyNextAction;
    let can_prompt = can_prompt_user();
    let template_name = maybe_prompt_for_template(can_prompt, colors);
    match crate::cli::diagnostics_domain::determine_config_only_next_action(
        can_prompt,
        template_name,
    ) {
        ConfigOnlyNextAction::CreateFromTemplate(name) => {
            create_prompt_from_template(&name, prompt_path, false, colors, env)
        }
        ConfigOnlyNextAction::CreateMinimal => create_minimal_and_notify(prompt_path, colors, env),
        ConfigOnlyNextAction::Skip => Ok(false),
    }
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
    dispatch_config_only_action(prompt_path, colors, env)
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

fn print_available_work_guides(colors: Colors) {
    let _ = writeln!(
        std::io::stdout(),
        "\n{}Available Work Guides:{}",
        colors.blue(),
        colors.reset()
    );
    let _ = list_templates();
    let _ = writeln!(std::io::stdout());
    print_common_work_guides(colors);
}

fn create_minimal_with_help<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
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

fn dispatch_neither_exists_action<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    use crate::cli::diagnostics_domain::NeitherExistsNextAction;
    let can_prompt = can_prompt_user();
    let template_name = maybe_prompt_for_template(can_prompt, colors);
    match crate::cli::diagnostics_domain::determine_neither_exists_next_action(
        can_prompt,
        template_name,
    ) {
        NeitherExistsNextAction::CreateFromTemplate(name) => {
            create_prompt_from_template(&name, prompt_path, false, colors, env)
        }
        NeitherExistsNextAction::CreateMinimal => {
            create_minimal_with_help(prompt_path, colors, env)
        }
        NeitherExistsNextAction::Skip => Ok(true),
    }
}

fn handle_neither_exists<R: ConfigEnvironment>(
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    handle_init_global_with(colors, env)?;
    print_available_work_guides(colors);
    dispatch_neither_exists_action(prompt_path, colors, env)
}

pub fn handle_init_template_arg_at_path_with_env<R: ConfigEnvironment>(
    template_name: &str,
    prompt_path: &Path,
    colors: Colors,
    env: &R,
) -> anyhow::Result<bool> {
    create_prompt_from_template(template_name, prompt_path, false, colors, env)
}
