///
/// Returns `true` if the user confirms, `false` otherwise.
///
/// Requires stdin to be a terminal and at least one output stream (stdout/stderr)
/// to be a terminal so prompts are visible.
pub fn can_prompt_user() -> bool {
    prompt_output_target().is_some()
}

#[derive(Clone, Copy)]
enum PromptOutputTarget {
    Stdout,
    Stderr,
}

fn prompt_output_target() -> Option<PromptOutputTarget> {
    use crate::cli::handlers::boundary::terminal as term;

    if !term::is_terminal() {
        return None;
    }

    if term::stdout_is_terminal() {
        return Some(PromptOutputTarget::Stdout);
    }
    if term::stderr_is_terminal() {
        return Some(PromptOutputTarget::Stderr);
    }

    None
}

fn with_prompt_writer<T>(
    target: PromptOutputTarget,
    f: impl FnOnce(&mut dyn std::io::Write) -> anyhow::Result<T>,
) -> anyhow::Result<T> {
    use crate::cli::handlers::boundary::terminal as term;

    match target {
        PromptOutputTarget::Stdout => f(&mut term::stdout()),
        PromptOutputTarget::Stderr => f(&mut term::stderr()),
    }
}

pub fn prompt_overwrite_confirmation(prompt_path: &Path, colors: Colors) -> anyhow::Result<bool> {
    let Some(target) = prompt_output_target() else {
        return Ok(false);
    };

    with_prompt_writer(target, |w| {
        writeln!(
            w,
            "{}PROMPT.md already exists:{} {}",
            colors.yellow(),
            colors.reset(),
            prompt_path.display()
        )?;
        write!(w, "Do you want to overwrite it? [y/N]: ")?;
        w.flush()?;
        Ok(())
    })?;

    let input = crate::cli::handlers::boundary::terminal::read_line();
    let response = input.unwrap_or_default().trim().to_lowercase();

    Ok(response == "y" || response == "yes")
}

/// Prompt the user to select a template interactively.
///
/// Returns `Some(template_name)` if the user selected a template,
/// or `None` if the user declined or entered invalid input.
pub fn prompt_for_template(colors: Colors) -> Option<String> {
    let target = prompt_output_target()?;
    if with_prompt_writer(target, |w| {
        let _ = writeln!(
            w,
            "PROMPT.md contains your task specification for the AI agents."
        );
        let _ = write!(w, "Would you like to create one from a Work Guide? [Y/n]: ");
        w.flush()?;
        Ok(())
    })
    .is_err()
    {
        return None;
    }

    let input = crate::cli::handlers::boundary::terminal::read_line();
    let response = input.unwrap_or_default().trim().to_lowercase();

    if response == "n" || response == "no" || response == "skip" {
        return None;
    }

    // Show available templates
    let templates: Vec<(&str, &str)> = list_templates();
    if with_prompt_writer(target, |w| {
        let _ = writeln!(w);
        let _ = writeln!(w, "Available Work Guides:");

        templates
            .iter()
            .enumerate()
            .for_each(|(i, (name, description))| {
                let _ = writeln!(
                    w,
                    "  {}{}{}  {}{}{}",
                    colors.cyan(),
                    name,
                    colors.reset(),
                    colors.dim(),
                    description,
                    colors.reset()
                );
                if (i + 1) % 5 == 0 {
                    let _ = writeln!(w);
                }
            });

        let _ = writeln!(w);
        let _ = writeln!(w, "Common choices:");
        let _ = writeln!(
            w,
            "  {}quick{}           - Quick/small changes (typos, minor fixes)",
            colors.cyan(),
            colors.reset()
        );
        let _ = writeln!(
            w,
            "  {}bug-fix{}         - Bug fix with investigation guidance",
            colors.cyan(),
            colors.reset()
        );
        let _ = writeln!(
            w,
            "  {}feature-spec{}    - Product specification",
            colors.cyan(),
            colors.reset()
        );
        let _ = writeln!(w);
        let _ = write!(w, "Enter Work Guide name (or press Enter to use 'quick'): ");
        w.flush()?;
        Ok(())
    })
    .is_err()
    {
        return None;
    }

    let template_input = crate::cli::handlers::boundary::terminal::read_line();
    let template_name = template_input.unwrap_or_default().trim();

    // Empty input defaults to 'quick' template
    if template_name.is_empty() {
        return Some("quick".to_string());
    }

    // Validate the template exists
    if get_template(template_name).is_some() {
        Some(template_name.to_string())
    } else {
        let _ = with_prompt_writer(target, |w| {
            writeln!(
                w,
                "{}Unknown Work Guide: '{}'{}",
                colors.red(),
                template_name,
                colors.reset()
            )?;
            writeln!(
                w,
                "Run 'ralph --list-work-guides' to see all available Work Guides."
            )?;
            Ok(())
        });
        None
    }
}
