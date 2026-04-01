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
    continuation_mode: bool,
) -> String {
    // Write context files to .agent/tmp/ for the agent to read
    write_dev_iteration_xsd_retry_files(workspace, last_output);
    prompt_developer_iteration_xsd_retry_with_context_files(
        context,
        xsd_error,
        workspace,
        continuation_mode,
    )
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
    continuation_mode: bool,
) -> String {
    use std::path::Path;

    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_dev_iteration_xsd_retry_schema_files(workspace);

    let schema_relative_path = ".agent/tmp/development_result.xsd";

    // Check that required files exist
    let schema_path = Path::new(schema_relative_path);
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let diagnostic_prefix = if !schema_exists || !last_output_exists {
        let parts: Vec<String> =
            std::iter::once("⚠️  WARNING: Required XSD retry files are missing:\n".to_string())
                .chain(if !schema_exists {
                    Some(format!(
                        "  - Schema file: {} (workspace.root() = {})\n",
                        workspace.absolute_str(schema_relative_path),
                        workspace.root().display()
                    ))
                } else {
                    None
                })
                .chain(if !last_output_exists {
                    Some(format!(
                        "  - Last output: {} (workspace.root() = {})\n",
                        workspace.absolute_str(".agent/tmp/last_output.xml"),
                        workspace.root().display()
                    ))
                } else {
                    None
                })
                .chain(std::iter::once(
                    "This likely indicates CWD != workspace.root() path mismatch.\n\n".to_string(),
                ))
                .collect();
        parts.concat()
    } else {
        String::new()
    };

    // If any required retry-context file is missing, return the deterministic fallback.
    if !schema_exists || !last_output_exists {
        return fallback_xsd_retry_prompt(
            &diagnostic_prefix,
            xsd_error,
            schema_relative_path,
            continuation_mode,
        );
    }

    // Proceed with normal XSD retry prompt generation only when all required retry-context files exist.
    let template_name = if continuation_mode {
        "developer_iteration_xsd_retry_continuation"
    } else {
        "developer_iteration_xsd_retry"
    };
    let template_content = context
        .registry()
        .get_template(template_name)
        .unwrap_or_else(|_| {
            if continuation_mode {
                include_str!("../templates/developer_iteration_xsd_retry_continuation.txt")
                    .to_string()
            } else {
                include_str!("../templates/developer_iteration_xsd_retry.txt").to_string()
            }
        });
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(schema_relative_path),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    let rendered_prompt = Template::new(&template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            fallback_xsd_retry_render_error_prompt(
                xsd_error,
                schema_relative_path,
                continuation_mode,
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
    continuation_mode: bool,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };
    use std::path::Path;

    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_dev_iteration_xsd_retry_schema_files(workspace);

    let schema_relative_path = ".agent/tmp/development_result.xsd";

    // Check that required files exist
    let schema_path = Path::new(schema_relative_path);
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let diagnostic_prefix = if !schema_exists || !last_output_exists {
        let parts: Vec<String> =
            std::iter::once("⚠️  WARNING: Required XSD retry files are missing:\n".to_string())
                .chain(if !schema_exists {
                    Some(format!(
                        "  - Schema file: {} (workspace.root() = {})\n",
                        workspace.absolute_str(schema_relative_path),
                        workspace.root().display()
                    ))
                } else {
                    None
                })
                .chain(if !last_output_exists {
                    Some(format!(
                        "  - Last output: {} (workspace.root() = {})\n",
                        workspace.absolute_str(".agent/tmp/last_output.xml"),
                        workspace.root().display()
                    ))
                } else {
                    None
                })
                .chain(std::iter::once(
                    "This likely indicates CWD != workspace.root() path mismatch.\n\n".to_string(),
                ))
                .collect();
        parts.concat()
    } else {
        String::new()
    };

    // If any required retry-context file is missing, return the deterministic fallback.
    if !schema_exists || !last_output_exists {
        let prompt_content = fallback_xsd_retry_prompt(
            &diagnostic_prefix,
            xsd_error,
            schema_relative_path,
            continuation_mode,
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

    // Proceed with normal XSD retry prompt generation only when all required retry-context files exist.
    let actual_template_name = if continuation_mode {
        "developer_iteration_xsd_retry_continuation"
    } else {
        template_name
    };
    let template_content = context
        .registry()
        .get_template(actual_template_name)
        .unwrap_or_else(|_| {
            if continuation_mode {
                include_str!("../templates/developer_iteration_xsd_retry_continuation.txt")
                    .to_string()
            } else {
                include_str!("../templates/developer_iteration_xsd_retry.txt").to_string()
            }
        });
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(schema_relative_path),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    let template = Template::new(&template_content);
    template
        .render_with_log(actual_template_name, &variables, &partials)
        .map(|mut rendered| {
            if !diagnostic_prefix.is_empty() {
                rendered.content = format!("{}\n{}", diagnostic_prefix, rendered.content);
            }
            rendered
        })
        .unwrap_or_else(|_| {
            let prompt_content = fallback_xsd_retry_render_error_prompt(
                xsd_error,
                schema_relative_path,
                continuation_mode,
            );
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: actual_template_name.to_string(),
                    substituted: vec![SubstitutionEntry {
                        name: "XSD_ERROR".to_string(),
                        source: SubstitutionSource::Value,
                    }],
                    unsubstituted: vec![],
                },
            }
        })
}

fn fallback_xsd_retry_prompt(
    diagnostic_prefix: &str,
    xsd_error: &str,
    schema_relative_path: &str,
    continuation_mode: bool,
) -> String {
    if continuation_mode {
        format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - FIX XML ONLY\n\n\
             Error: {xsd_error}\n\n\
             This is an XML formatting/schema issue. Stay in the same session. Do not redo analysis or implementation work.\n\n\
             The schema and previous output files could not be found right now. Read {schema_relative_path} when available, then resend corrected continuation XML: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Why the full plan was not completed</ralph-summary><ralph-next-steps>1. Ordered recovery step for finishing the remaining plan.</ralph-next-steps></ralph-development-result>\n"
        )
    } else {
        format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - FIX XML ONLY\n\n\
             Error: {xsd_error}\n\n\
             This is an XML formatting/schema issue. Stay in the same session. Do not redo analysis or implementation work.\n\n\
             The schema and previous output files could not be found right now. Read {schema_relative_path} when available, then resend corrected XML in this format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
        )
    }
}

fn fallback_xsd_retry_render_error_prompt(
    xsd_error: &str,
    schema_relative_path: &str,
    continuation_mode: bool,
) -> String {
    if continuation_mode {
        format!(
            "XSD VALIDATION FAILED - FIX XML ONLY\n\nError: {xsd_error}\n\n\
             This is an XML formatting/schema issue. Stay in the same session. Do not redo analysis or implementation work.\n\n\
             Read {schema_relative_path} and .agent/tmp/last_output.xml, then resend corrected continuation XML that explains why the full plan was not completed and provides ordered recovery steps for finishing the remaining plan.\n"
        )
    } else {
        format!(
            "XSD VALIDATION FAILED - FIX XML ONLY\n\nError: {xsd_error}\n\n\
             This is an XML formatting/schema issue. Stay in the same session. Do not redo analysis or implementation work.\n\n\
             Read {schema_relative_path} and .agent/tmp/last_output.xml, then resend corrected development XML conforming to the XSD schema.\n"
        )
    }
}
