/// Handle template validation command.
pub fn handle_template_validate(colors: Colors) {
    let _ = writeln!(std::io::stdout(), "{}Validating templates...{}", colors.bold(), colors.reset());
    let _ = writeln!(std::io::stdout());

    let templates = get_all_templates();
    let partials_set: std::collections::HashSet<String> =
        get_shared_partials().keys().cloned().collect();

    let mut total_errors = 0;
    let mut total_warnings = 0;

    for (name, (content, _)) in {
        let mut items: Vec<_> = templates.iter().collect();
        items.sort_by(|a, b| a.0.cmp(b.0));
        items
    } {
        let result = validate_template(content, &partials_set);

        if result.is_valid {
            let _ = writeln!(std::io::stdout(), "{}✓{} {}{}{}", colors.green(), colors.reset(), colors.cyan(), name, colors.reset());
        } else {
            let _ = writeln!(std::io::stdout(), "{}✗{} {}{}{}", colors.red(), colors.reset(), colors.cyan(), name, colors.reset());
        }

        for error in &result.errors {
            let _ = writeln!(std::io::stdout(), "  {}error:{} {}", colors.red(), colors.reset(), format_error(error));
            total_errors += 1;
        }

        for warning in &result.warnings {
            let _ = writeln!(std::io::stdout(), "  {}warning:{} {}", colors.yellow(), colors.reset(), format_warning(warning));
            total_warnings += 1;
        }

        if !result.variables.is_empty() {
            let var_names: Vec<&str> = result.variables.iter().map(|v| v.name.as_str()).collect();
            let _ = writeln!(std::io::stdout(), "  {}variables:{} {}", colors.dim(), colors.reset(), var_names.join(", "));
        }

        if !result.partials.is_empty() {
            let _ = writeln!(std::io::stdout(), "  {}partials:{} {}", colors.dim(), colors.reset(), result.partials.join(", "));
        }
    }

    let _ = writeln!(std::io::stdout());
    if total_errors == 0 {
        let _ = writeln!(std::io::stdout(), "{}All templates validated successfully!{}", colors.green(), colors.reset());
        if total_warnings > 0 {
            let _ = writeln!(std::io::stdout(), "{total_warnings} warnings");
        }
    } else {
        let _ = writeln!(std::io::stdout(), "{}Validation failed with {} error(s){}", colors.red(), total_errors, colors.reset());
        if total_warnings > 0 {
            let _ = writeln!(std::io::stdout(), "{total_warnings} warnings");
        }
        std::process::exit(1);
    }
}
