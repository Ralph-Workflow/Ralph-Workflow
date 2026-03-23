/// Handle template validation command.
pub fn handle_template_validate(colors: Colors) {
    let _ = writeln!(
        crate::cli::handlers::boundary::stdout(),
        "{}Validating templates...{}",
        colors.bold(),
        colors.reset()
    );
    let _ = writeln!(crate::cli::handlers::boundary::stdout());

    let templates = get_all_templates();
    let partials_set: std::collections::HashSet<String> =
        get_shared_partials().keys().cloned().collect();

    // Collect templates and sort by name using functional pipeline
    let sorted_templates: Vec<_> = templates.iter().sorted_by(|a, b| a.0.cmp(b.0)).collect();

    // Process all templates and accumulate results using fold
    let (total_errors, total_warnings) =
        sorted_templates
            .iter()
            .fold((0u32, 0u32), |(err, warn), (name, (content, _))| {
                let result = validate_template(content, &partials_set);

                if result.is_valid {
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "{}✓{} {}{}{}",
                        colors.green(),
                        colors.reset(),
                        colors.cyan(),
                        name,
                        colors.reset()
                    );
                } else {
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "{}✗{} {}{}{}",
                        colors.red(),
                        colors.reset(),
                        colors.cyan(),
                        name,
                        colors.reset()
                    );
                }

                // Print errors using iterator for_each
                result.errors.iter().for_each(|error| {
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "  {}error:{} {}",
                        colors.red(),
                        colors.reset(),
                        format_error(error)
                    );
                });

                // Print warnings using iterator for_each
                result.warnings.iter().for_each(|warning| {
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "  {}warning:{} {}",
                        colors.yellow(),
                        colors.reset(),
                        format_warning(warning)
                    );
                });

                if !result.variables.is_empty() {
                    let var_names: Vec<&str> =
                        result.variables.iter().map(|v| v.name.as_str()).collect();
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "  {}variables:{} {}",
                        colors.dim(),
                        colors.reset(),
                        var_names.join(", ")
                    );
                }

                if !result.partials.is_empty() {
                    let _ = writeln!(
                        crate::cli::handlers::boundary::stdout(),
                        "  {}partials:{} {}",
                        colors.dim(),
                        colors.reset(),
                        result.partials.join(", ")
                    );
                }

                // Accumulate counts without mutation
                (
                    err + result.errors.len() as u32,
                    warn + result.warnings.len() as u32,
                )
            });

    let _ = writeln!(crate::cli::handlers::boundary::stdout());
    if total_errors == 0 {
        let _ = writeln!(
            crate::cli::handlers::boundary::stdout(),
            "{}All templates validated successfully!{}",
            colors.green(),
            colors.reset()
        );
        if total_warnings > 0 {
            let _ = writeln!(
                crate::cli::handlers::boundary::stdout(),
                "{total_warnings} warnings"
            );
        }
    } else {
        let _ = writeln!(
            crate::cli::handlers::boundary::stdout(),
            "{}Validation failed with {} error(s){}",
            colors.red(),
            total_errors,
            colors.reset()
        );
        if total_warnings > 0 {
            let _ = writeln!(
                crate::cli::handlers::boundary::stdout(),
                "{total_warnings} warnings"
            );
        }
        crate::cli::handlers::boundary::exit_with_code(1);
    }
}
