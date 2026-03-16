/// Prompt the user to confirm overwriting an existing PROMPT.md.
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
    if !std::io::stdin().is_terminal() {
        return None;
    }

    if std::io::stdout().is_terminal() {
        return Some(PromptOutputTarget::Stdout);
    }
    if std::io::stderr().is_terminal() {
        return Some(PromptOutputTarget::Stderr);
    }

    None
}

fn with_prompt_writer<T>(
    target: PromptOutputTarget,
    f: impl FnOnce(&mut dyn std::io::Write) -> anyhow::Result<T>,
) -> anyhow::Result<T> {
    use std::io;

    match target {
        PromptOutputTarget::Stdout => {
            let mut out = io::stdout().lock();
            f(&mut out)
        }
        PromptOutputTarget::Stderr => {
            let mut err = io::stderr().lock();
            f(&mut err)
        }
    }
}

pub fn prompt_overwrite_confirmation(prompt_path: &Path, colors: Colors) -> anyhow::Result<bool> {
    use std::io;

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

    let mut input = String::new();
    match io::stdin().read_line(&mut input) {
        Ok(0) | Err(_) => return Ok(false),
        Ok(_) => {}
    }

    let response = input.trim().to_lowercase();
    Ok(response == "y" || response == "yes")
}

/// Prompt the user to select a template interactively.
///
/// Returns `Some(template_name)` if the user selected a template,
/// or `None` if the user declined or entered invalid input.
pub fn prompt_for_template(colors: Colors) -> Option<String> {
    use std::io;

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

    let mut input = String::new();
    match io::stdin().read_line(&mut input) {
        Ok(0) | Err(_) => return None,
        Ok(_) => {}
    }

    let response = input.trim().to_lowercase();
    if response == "n" || response == "no" || response == "skip" {
        return None;
    }

    // Show available templates
    let templates: Vec<(&str, &str)> = list_templates();
    if with_prompt_writer(target, |w| {
        let _ = writeln!(w);
        let _ = writeln!(w, "Available Work Guides:");

        for (i, (name, description)) in templates.iter().enumerate() {
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
                let _ = writeln!(w); // Group templates in sets of 5 for readability
            }
        }

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

    let mut template_input = String::new();
    match io::stdin().read_line(&mut template_input) {
        Ok(0) | Err(_) => return None,
        Ok(_) => {}
    }

    let template_name = template_input.trim();
    if template_name.is_empty() {
        // Default to 'quick' template
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
