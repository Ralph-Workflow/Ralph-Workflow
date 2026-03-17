/// Handle template render command.
pub fn handle_template_render(name: &str, colors: Colors) -> anyhow::Result<()> {
    let templates = get_all_templates();

    let (content, _) = templates
        .get(name)
        .ok_or_else(|| anyhow::anyhow!("Template '{name}' not found"))?;

    // Build variables map using functional pattern
    let variables: BTreeMap<String, String> = [
        ("PROMPT", "Example prompt content"),
        ("PLAN", "Example plan content"),
        ("DIFF", "+ example line"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect();

    let _ = writeln!(std::io::stdout(), "{}Rendering template '{}'...{}", colors.bold(), name, colors.reset());
    let _ = writeln!(std::io::stdout());

    let partials = get_shared_partials();
    let template = Template::new(content);

    match template.render_with_partials(
        &variables
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect(),
        &partials,
    ) {
        Ok(rendered) => {
            let _ = writeln!(std::io::stdout(), "{}", colors.dim());
            let _ = writeln!(std::io::stdout(), "{rendered}");
            let _ = writeln!(std::io::stdout(), "{}", colors.reset());
        }
        Err(e) => {
            let _ = writeln!(std::io::stdout(), "{}Render error: {}{}{}", colors.red(), e, colors.reset(), colors.reset());
            let _ = writeln!(std::io::stdout());
            let _ = writeln!(std::io::stdout(), "{}Tip:{}", colors.yellow(), colors.reset());
            let _ = writeln!(std::io::stdout(), "  Use --template-variables to see which variables are required.");
        }
    }

    Ok(())
}
