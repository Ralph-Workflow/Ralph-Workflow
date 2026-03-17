/// Handle template show command.
pub fn handle_template_show(name: &str, colors: Colors) -> anyhow::Result<()> {
    let templates = get_all_templates();

    let (content, description) = templates
        .get(name)
        .ok_or_else(|| anyhow::anyhow!("Template '{name}' not found"))?;

    let _ = writeln!(std::io::stdout(), "{}Template: {}{}{}{}", colors.bold(), colors.cyan(), name, colors.reset(), colors.reset());
    let _ = writeln!(std::io::stdout(), "{}Description: {}{}{}", colors.dim(), description, colors.reset(), colors.reset());
    let _ = writeln!(std::io::stdout());

    // Show metadata
    let metadata = extract_metadata(content);
    if let Some(version) = metadata.version {
        let _ = writeln!(std::io::stdout(), "{}Version: {}{}{}", colors.dim(), version, colors.reset(), colors.reset());
    }
    if let Some(purpose) = metadata.purpose {
        let _ = writeln!(std::io::stdout(), "{}Purpose: {}{}{}", colors.dim(), purpose, colors.reset(), colors.reset());
    }

    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "{}Variables:{}", colors.bold(), colors.reset());

    let variables = extract_variables(content);
    if variables.is_empty() {
        let _ = writeln!(std::io::stdout(), "  (none)");
    } else {
        // Print variables using iterator for_each
        variables.iter().for_each(|var| {
            if var.has_default {
                let _ = writeln!(std::io::stdout(), "  {}{}{} = {}{}{}", colors.cyan(), var.name, colors.reset(), colors.green(), var.default_value.as_deref().unwrap_or(""), colors.reset());
            } else {
                let _ = writeln!(std::io::stdout(), "  {}{}{}", colors.cyan(), var.name, colors.reset());
            }
        });
    }

    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "{}Partials:{}", colors.bold(), colors.reset());

    let partials = extract_partials(content);
    if partials.is_empty() {
        let _ = writeln!(std::io::stdout(), "  (none)");
    } else {
        // Print partials using iterator for_each
        partials.iter().for_each(|partial| {
            let _ = writeln!(std::io::stdout(), "  {}{}{}", colors.cyan(), partial, colors.reset());
        });
    }

    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "{}Content:{}", colors.bold(), colors.reset());
    let _ = writeln!(std::io::stdout(), "{}", colors.dim());
    // Print content lines using iterator for_each
    content.lines().take(50).for_each(|line| {
        let _ = writeln!(std::io::stdout(), "{line}");
    });
    if content.lines().count() > 50 {
        let _ = writeln!(std::io::stdout(), "... ({} more lines)", content.lines().count() - 50);
    }
    let _ = writeln!(std::io::stdout(), "{}", colors.reset());

    Ok(())
}
