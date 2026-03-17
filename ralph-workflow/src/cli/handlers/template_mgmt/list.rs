/// Handle template list command.
pub fn handle_template_list(colors: Colors) {
    handle_template_list_impl(colors, false);
}

/// Handle template list all command (including deprecated).
pub fn handle_template_list_all(colors: Colors) {
    handle_template_list_impl(colors, true);
}

/// Implementation of template list command.
fn handle_template_list_impl(colors: Colors, include_deprecated: bool) {
    let all_templates = get_all_templates();
    let filtered_templates: Vec<_> = all_templates
        .iter()
        .filter(|(name, _)| {
            if include_deprecated {
                return true;
            }
            // Check if this is a deprecated template by looking at the catalog
            template_catalog::get_template_metadata(name).is_none_or(|meta| !meta.deprecated)
        })
        .map(|(name, (content, desc))| {
            // For deprecated templates, use their content which points to consolidated versions
            (name, content, desc)
        })
        .collect();

    let header = if include_deprecated {
        "All Templates (including deprecated):"
    } else {
        "Active Templates:"
    };

    let _ = writeln!(std::io::stdout(), "{}{}{}", colors.bold(), header, colors.reset());
    let _ = writeln!(std::io::stdout());

    for (name, _, description) in {
        let mut items: Vec<_> = filtered_templates.clone();
        items.sort_by(|a, b| a.0.cmp(b.0));
        items
    } {
        // Show deprecated marker in the list
        let is_deprecated = template_catalog::get_template_metadata(name).is_some_and(|meta| meta.deprecated);

        let deprecated_marker = if is_deprecated {
            format!("{} [DEPRECATED]{}", colors.yellow(), colors.reset())
        } else {
            String::new()
        };

        let _ = writeln!(std::io::stdout(), "  {}{}{}{}  {}{}{}", colors.cyan(), name, colors.reset(), deprecated_marker, colors.dim(), description, colors.reset());
    }

    let _ = writeln!(std::io::stdout());
    if include_deprecated {
        let deprecated_count = filtered_templates
            .iter()
            .filter(|(name, _, _)| {
                template_catalog::get_template_metadata(name).is_some_and(|meta| meta.deprecated)
            })
            .count();

        let _ = writeln!(std::io::stdout(), "Total: {} templates ({} active, {} deprecated)", filtered_templates.len(), filtered_templates.len() - deprecated_count, deprecated_count);
        let _ = writeln!(std::io::stdout());
        let _ = writeln!(std::io::stdout(), "{}Tip:{}", colors.yellow(), colors.reset());
        let _ = writeln!(std::io::stdout(), "  Edit templates in ~/.config/ralph/templates/");
        let _ = writeln!(std::io::stdout(), "  Deprecated templates are kept for backward compatibility.");
        let _ = writeln!(std::io::stdout(), "  Use {}--list{} to show only active templates.", colors.bold(), colors.reset());
    } else {
        let _ = writeln!(std::io::stdout(), "Total: {} active templates", filtered_templates.len());
        let _ = writeln!(std::io::stdout());
        let _ = writeln!(std::io::stdout(), "{}Tip:{}", colors.yellow(), colors.reset());
        let _ = writeln!(std::io::stdout(), "  Edit templates in ~/.config/ralph/templates/");
        let _ = writeln!(std::io::stdout(), "  Use {}--list-all{} to include deprecated templates", colors.bold(), colors.reset());
    }
}
