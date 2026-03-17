/// Print a short list of common Work Guides.
///
/// Shows the most commonly used Work Guides with a note to use --list-work-guides for more.
pub fn print_common_work_guides(colors: Colors) {
    let _ = writeln!(std::io::stdout(), "{}Common Work Guides:{}", colors.bold(), colors.reset());
    let _ = writeln!(
        std::io::stdout(),
        "  {}quick{}           Quick/small changes (typos, minor fixes)",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "  {}bug-fix{}         Bug fix with investigation guidance",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "  {}feature-spec{}    Comprehensive product specification",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "  {}refactor{}        Code refactoring with behavior preservation",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(
        std::io::stdout(),
        "Use {}--list-work-guides{} for the complete list of Work Guides.",
        colors.cyan(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());
}

/// Print a template category section.
///
/// Helper function to reduce the length of `handle_list_work_guides`.
fn print_template_category(category_name: &str, templates: &[(&str, &str)], colors: Colors) {
    let _ = writeln!(std::io::stdout(), "{}{}:{}", colors.bold(), category_name, colors.reset());
    for (name, description) in templates {
        let _ = writeln!(
            std::io::stdout(),
            "  {}{}{}  {}",
            colors.cyan(),
            name,
            colors.reset(),
            description
        );
    }
    let _ = writeln!(std::io::stdout());
}

/// Handle the `--list-work-guides` (or `--list-templates`) flag.
///
/// Lists all available PROMPT.md Work Guides with descriptions, organized by category.
///
/// # Arguments
///
/// * `colors` - Terminal color configuration for output
///
/// # Returns
///
/// Returns `true` if the flag was handled (program should exit after).
#[must_use]
pub fn handle_list_work_guides(colors: Colors) -> bool {
    let _ = writeln!(std::io::stdout(), "PROMPT.md Work Guides (use: ralph --init <work-guide>)");
    let _ = writeln!(std::io::stdout());

    // Common templates (most frequently used)
    print_template_category(
        "Common Templates",
        &[
            ("quick", "Quick/small changes (typos, minor fixes)"),
            ("bug-fix", "Bug fix with investigation guidance"),
            ("feature-spec", "Comprehensive product specification"),
            ("refactor", "Code refactoring with behavior preservation"),
        ],
        colors,
    );

    // Testing and documentation
    print_template_category(
        "Testing & Documentation",
        &[
            ("test", "Test writing with edge case considerations"),
            ("docs", "Documentation update with completeness checklist"),
            ("code-review", "Structured code review for pull requests"),
        ],
        colors,
    );

    // Specialized development
    print_template_category(
        "Specialized Development",
        &[
            ("cli-tool", "CLI tool with argument parsing and completion"),
            ("web-api", "REST/HTTP API with error handling"),
            (
                "ui-component",
                "UI component with accessibility and responsive design",
            ),
            ("onboarding", "Learn a new codebase efficiently"),
        ],
        colors,
    );

    // Advanced/Infrastructure
    print_template_category(
        "Advanced & Infrastructure",
        &[
            (
                "performance-optimization",
                "Performance optimization with benchmarking",
            ),
            ("security-audit", "Security audit with OWASP Top 10 coverage"),
            (
                "api-integration",
                "API integration with retry logic and resilience",
            ),
            (
                "database-migration",
                "Database migration with zero-downtime strategies",
            ),
            (
                "dependency-update",
                "Dependency update with breaking change handling",
            ),
            ("data-pipeline", "Data pipeline with ETL and monitoring"),
        ],
        colors,
    );

    // Maintenance
    print_template_category(
        "Maintenance & Operations",
        &[
            ("debug-triage", "Systematic issue investigation and diagnosis"),
            (
                "tech-debt",
                "Technical debt refactoring with prioritization",
            ),
            ("release", "Release preparation with versioning and changelog"),
        ],
        colors,
    );

    let _ = writeln!(std::io::stdout(), "Usage: ralph --init <work-guide>");
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "Example:");
    let _ = writeln!(std::io::stdout(), "  ralph --init bug-fix              # Create bug fix Work Guide");
    let _ = writeln!(std::io::stdout(), "  ralph --init feature-spec         # Create feature spec Work Guide");
    let _ = writeln!(std::io::stdout(), "  ralph --init quick                # Create quick change Work Guide");
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "{}Tip:{}", colors.yellow(), colors.reset());
    let _ = writeln!(std::io::stdout(), "  Use --init without a value to auto-detect what you need.");
    let _ = writeln!(std::io::stdout(), "  Use --force-overwrite to overwrite an existing PROMPT.md.");
    let _ = writeln!(std::io::stdout(), "  Run ralph --extended-help to learn about Work Guides vs Agent Prompts.");

    true
}
