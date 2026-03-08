// Developer iteration XSD retry prompt generation functions.
//
// Contains functions for generating XSD validation retry prompts.

/// Generate XSD validation retry prompt for developer iteration with error feedback.
///
/// This prompt is used when an AI agent produces development result XML that fails XSD validation.
/// The XSD schema and last output are written to files at `.agent/tmp/` to avoid
/// bloating the prompt. The agent should read these files.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `_prompt_content` - The original user request (unused - kept for API compatibility)
/// * `_plan_content` - The implementation plan (unused - kept for API compatibility)
/// * `xsd_error` - The XSD validation error message to include in the prompt
/// * `last_output` - The invalid XML output that failed validation
/// * `workspace` - Workspace for writing XSD retry context files
pub fn prompt_developer_iteration_xsd_retry_with_context(
    context: &TemplateContext,
    _prompt_content: &str,
    _plan_content: &str,
    xsd_error: &str,
    last_output: &str,
    workspace: &dyn Workspace,
) -> String {
    // Write context files to .agent/tmp/ for the agent to read
    write_dev_iteration_xsd_retry_files(workspace, last_output);
    prompt_developer_iteration_xsd_retry_with_context_files(context, xsd_error, workspace)
}

/// Generate XSD validation retry prompt for developer iteration with error feedback.
///
/// This variant assumes `.agent/tmp/last_output.xml` is already materialized.
///
/// Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
/// If required files are missing, a deterministic fallback prompt is produced that includes
/// diagnostic information but still provides valid instructions to the agent.
pub fn prompt_developer_iteration_xsd_retry_with_context_files(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
) -> String {
    use std::path::Path;

    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_dev_iteration_xsd_retry_schema_files(workspace);

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/development_result.xsd");
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let mut diagnostic_prefix = String::new();
    if !schema_exists || !last_output_exists {
        diagnostic_prefix.push_str("⚠️  WARNING: Required XSD retry files are missing:\n");
        if !schema_exists {
            writeln!(diagnostic_prefix, "  - Schema file: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/development_result.xsd"),
                workspace.root().display()).unwrap();
        }
        if !last_output_exists {
            writeln!(diagnostic_prefix, "  - Last output: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/last_output.xml"),
                workspace.root().display()).unwrap();
        }
        diagnostic_prefix
            .push_str("This likely indicates CWD != workspace.root() path mismatch.\n\n");
    }

    // If both files are missing, return fallback prompt with diagnostics (per AC #5)
    if !schema_exists && !last_output_exists {
        return format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - CONTINUE IMPLEMENTATION\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please continue the implementation based on PROMPT.md and PLAN.md.\n\n\
             Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
        );
    }

    // Proceed with normal XSD retry prompt generation if at least schema exists
    let template_content = context
        .registry()
        .get_template("developer_iteration_xsd_retry")
        .unwrap_or_else(|_| {
            include_str!("../templates/developer_iteration_xsd_retry.txt").to_string()
        });
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xsd"),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    let rendered_prompt = Template::new(&template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            format!(
                "Your previous development status failed XSD validation.\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/development_result.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
                 Please resend your status in valid XML format conforming to the XSD schema.\n"
            )
        });

    // Prepend diagnostic prefix if files were missing but we continued anyway
    if diagnostic_prefix.is_empty() {
        rendered_prompt
    } else {
        format!("{diagnostic_prefix}\n{rendered_prompt}")
    }
}

/// Generate XSD validation retry prompt for developer iteration with substitution log.
///
/// This variant assumes `.agent/tmp/last_output.xml` is already materialized.
pub fn prompt_developer_iteration_xsd_retry_with_context_files_and_log(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
    template_name: &str,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };
    use std::path::Path;

    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_dev_iteration_xsd_retry_schema_files(workspace);

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/development_result.xsd");
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let mut diagnostic_prefix = String::new();
    if !schema_exists || !last_output_exists {
        diagnostic_prefix.push_str("⚠️  WARNING: Required XSD retry files are missing:\n");
        if !schema_exists {
            writeln!(diagnostic_prefix, "  - Schema file: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/development_result.xsd"),
                workspace.root().display()).unwrap();
        }
        if !last_output_exists {
            writeln!(diagnostic_prefix, "  - Last output: {} (workspace.root() = {})",
                workspace.absolute_str(".agent/tmp/last_output.xml"),
                workspace.root().display()).unwrap();
        }
        diagnostic_prefix
            .push_str("This likely indicates CWD != workspace.root() path mismatch.\n\n");
    }

    // If both files are missing, return fallback prompt with diagnostics (per AC #5)
    if !schema_exists && !last_output_exists {
        let prompt_content = format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - CONTINUE IMPLEMENTATION\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please continue the implementation based on PROMPT.md and PLAN.md.\n\n\
             Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
        );
        return RenderedTemplate {
            content: prompt_content,
            log: SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![SubstitutionEntry {
                    name: "XSD_ERROR".to_string(),
                    source: SubstitutionSource::Value,
                }],
                unsubstituted: vec![],
            },
        };
    }

    // Proceed with normal XSD retry prompt generation if at least schema exists
    let template_content = context
        .registry()
        .get_template("developer_iteration_xsd_retry")
        .unwrap_or_else(|_| {
            include_str!("../templates/developer_iteration_xsd_retry.txt").to_string()
        });
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xsd"),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    let template = Template::new(&template_content);
    if let Ok(mut rendered) = template.render_with_log(template_name, &variables, &partials) {
        if !diagnostic_prefix.is_empty() {
            rendered.content = format!("{}\n{}", diagnostic_prefix, rendered.content);
        }
        rendered
    } else {
        let prompt_content = format!(
            "Your previous development status failed XSD validation.\n\nError: {xsd_error}\n\n\
             Read .agent/tmp/development_result.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
             Please resend your status in valid XML format conforming to the XSD schema.\n"
        );
        RenderedTemplate {
            content: prompt_content,
            log: SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![SubstitutionEntry {
                    name: "XSD_ERROR".to_string(),
                    source: SubstitutionSource::Value,
                }],
                unsubstituted: vec![],
            },
        }
    }
}
