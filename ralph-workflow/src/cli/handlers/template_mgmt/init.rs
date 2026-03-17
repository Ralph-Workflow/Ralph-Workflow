/// Handle template initialization command.
///
/// Creates the user templates directory and copies all default templates.
fn handle_template_init(force: bool, colors: Colors) -> anyhow::Result<()> {
    let templates_dir = TemplateRegistry::default_user_templates_dir()
        .ok_or_else(|| anyhow::anyhow!("Cannot determine home directory for templates"))?;

    // Create a registry instance to validate the directory structure
    let registry = TemplateRegistry::new(Some(templates_dir.clone()));

    // Check if we're using user templates or embedded templates
    let source = registry.template_source("commit_message_xml");
    let has_user = registry.has_user_template("commit_message_xml");

    // Use the variables to avoid dead code warnings
    let _ = (source, has_user);

    let _ = writeln!(std::io::stdout(), "{}Initializing user templates directory...{}", colors.bold(), colors.reset());
    let _ = writeln!(std::io::stdout(), "  Location: {}{}{}", colors.cyan(), templates_dir.display(), colors.reset());
    let _ = writeln!(std::io::stdout());

    // Check if directory already exists
    if templates_dir.exists() {
        if force {
            let _ = writeln!(std::io::stdout(), "{}Warning: {}Directory already exists. Overwriting...{}", colors.yellow(), colors.reset(), colors.reset());
        } else {
            let _ = writeln!(std::io::stdout(), "{}Error: {}Directory already exists. Use --force to overwrite.{}", colors.red(), colors.reset(), colors.reset());
            let _ = writeln!(std::io::stdout());
            let _ = writeln!(std::io::stdout(), "To reinitialize with defaults, run:");
            let _ = writeln!(std::io::stdout(), "  ralph --template-init --force");
            return Err(anyhow::anyhow!("Templates directory already exists"));
        }
    }

    // Create directory structure
    fs::create_dir_all(&templates_dir)?;

    let shared_dir = templates_dir.join("shared");
    fs::create_dir_all(&shared_dir)?;

    let reviewer_dir = templates_dir.join("reviewer");
    fs::create_dir_all(&reviewer_dir)?;

    // Copy all templates from the embedded templates
    let templates = get_all_templates();
    let mut copied = 0;
    let mut skipped = 0;

    for (name, (content, _)) in &templates {
        let target_path = if name.starts_with("reviewer/") {
            let parts: Vec<&str> = name.split('/').collect();
            if parts.len() == 2 {
                let Some(filename) = parts.get(1) else {
                    continue;
                };
                templates_dir
                    .join("reviewer")
                    .join(format!("{}.txt", filename))
            } else {
                continue;
            }
        } else {
            templates_dir.join(format!("{name}.txt"))
        };

        // Skip if file exists and not forcing
        if target_path.exists() && !force {
            skipped += 1;
            continue;
        }

        fs::write(&target_path, content)?;
        copied += 1;
    }

    // Copy shared partials
    let partials = get_shared_partials();
    for (name, content) in &partials {
        let target_path = templates_dir.join(format!("{name}.txt"));
        if target_path.exists() && !force {
            skipped += 1;
            continue;
        }
        fs::write(&target_path, content)?;
        copied += 1;
    }

    let _ = writeln!(std::io::stdout(), "{}Successfully initialized user templates!{}", colors.green(), colors.reset());
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "  {copied} templates copied");
    if skipped > 0 {
        let _ = writeln!(std::io::stdout(), "  {skipped} templates skipped (already exists)");
    }
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "You can now edit templates in:");
    let _ = writeln!(std::io::stdout(), "  {}", templates_dir.display());
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "Changes to user templates will override the built-in templates.");

    Ok(())
}
