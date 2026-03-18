// Review prompt generation functions.

/// Generate XML-based review prompt using template registry.
///
/// This version uses XML output format with XSD validation for reliable parsing.
/// The reviewer is instructed to read `.agent/PROMPT.md.backup` directly for context
/// about the original requirements.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `_prompt_content` - Unused, kept for API compatibility. Reviewer reads PROMPT.md.backup directly.
/// * `plan_content` - Implementation plan
/// * `changes_content` - Description of changes made
/// * `workspace` - Workspace for resolving absolute paths
pub fn prompt_review_xml_with_context(
    context: &TemplateContext,
    _prompt_content: &str,
    plan_content: &str,
    changes_content: &str,
    workspace: &dyn Workspace,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());
    let variables = HashMap::from([
        ("PLAN", plan_content.to_string()),
        ("CHANGES", changes_content.to_string()),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
        ),
    ]);
    Template::new(&template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            format!(
                "REVIEW MODE\n\nReview the implementation against:\n\n\
                 Read `.agent/PROMPT.md.backup` for the original requirements (DO NOT modify it).\n\n\
                 Plan:\n{plan_content}\n\nChanges:\n{changes_content}\n\n\
                 Output format: <ralph-issues><ralph-issue>[Severity] file:line - Description. Fix.</ralph-issue></ralph-issues>\n"
            )
        })
}

/// Generate review prompt with size-aware content references and substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
pub fn prompt_review_xml_with_references_and_log(
    context: &TemplateContext,
    refs: &crate::prompts::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
    template_name: &str,
) -> RenderedTemplate {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());

    let variables = HashMap::from([
        ("PLAN", refs.plan_for_template()),
        ("CHANGES", refs.diff_for_template()),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
        ),
    ]);

    match Template::new(&template_content).render_with_log(template_name, &variables, &partials) {
        Ok(rendered) => rendered,
        Err(err) => {
            // Extract missing variable from error
            let unsubstituted = match &err {
                crate::prompts::template_engine::TemplateError::MissingVariable(name) => {
                    vec![name.clone()]
                }
                _ => vec![],
            };

            let plan = refs.plan_for_template();
            let changes = refs.diff_for_template();
            let prompt_content = format!("REVIEW MODE\n\nPLAN:\n{plan}\n\nCHANGES:\n{changes}\n");
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: template_name.to_string(),
                    substituted: vec![
                        SubstitutionEntry {
                            name: "PLAN".to_string(),
                            source: SubstitutionSource::Value,
                        },
                        SubstitutionEntry {
                            name: "CHANGES".to_string(),
                            source: SubstitutionSource::Value,
                        },
                    ],
                    unsubstituted,
                },
            }
        }
    }
}

/// Generate review prompt with size-aware content references.
///
/// This version uses `PromptContentReferences` which automatically handles
/// oversized content by referencing file paths instead of embedding inline.
/// Use this when content may exceed CLI argument limits.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `refs` - Content references for PLAN and CHANGES (diff)
/// * `workspace` - Workspace for resolving absolute paths
pub fn prompt_review_xml_with_references(
    context: &TemplateContext,
    refs: &crate::prompts::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());

    let variables = HashMap::from([
        ("PLAN", refs.plan_for_template()),
        ("CHANGES", refs.diff_for_template()),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
        ),
    ]);

    Template::new(&template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            let plan = refs.plan_for_template();
            let changes = refs.diff_for_template();
            format!("REVIEW MODE\n\nPLAN:\n{plan}\n\nCHANGES:\n{changes}\n")
        })
}

/// Generate XSD validation retry prompt for review with error feedback.
///
/// This prompt is used when an AI agent produces review XML that fails XSD validation.
/// The XSD schema and last output are written to files at `.agent/tmp/` to avoid
/// bloating the prompt. The agent should read these files.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `_prompt_content` - Original user requirements (unused - kept for API compatibility)
/// * `_plan_content` - Implementation plan (unused - kept for API compatibility)
/// * `_changes_content` - Description of changes made (unused - kept for API compatibility)
/// * `xsd_error` - The XSD validation error message to include in the prompt
/// * `last_output` - The invalid XML output that failed validation
/// * `workspace` - Workspace for writing XSD retry context files
pub fn prompt_review_xsd_retry_with_context(
    context: &TemplateContext,
    _prompt_content: &str,
    _plan_content: &str,
    _changes_content: &str,
    xsd_error: &str,
    last_output: &str,
    workspace: &dyn Workspace,
) -> String {
    // Write context files to .agent/tmp/ for the agent to read
    write_review_xsd_retry_files(workspace, last_output);
    prompt_review_xsd_retry_with_context_files(context, xsd_error, workspace)
}

/// Generate XSD validation retry prompt for review with error feedback.
///
/// This variant assumes `.agent/tmp/last_output.xml` is already materialized.
///
/// Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
/// If required files are missing, a deterministic fallback prompt is produced that includes
/// diagnostic information but still provides valid instructions to the agent.
pub fn prompt_review_xsd_retry_with_context_files(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
) -> String {
    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_review_xsd_retry_schema_files(workspace);

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/issues.xsd");
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let diagnostic_prefix = if !schema_exists || !last_output_exists {
        let parts: Vec<String> =
            std::iter::once("⚠️  WARNING: Required XSD retry files are missing:\n".to_string())
                .chain(
                    if !schema_exists {
                        Some(format!(
                            "  - Schema file: {} (workspace.root() = {})\n",
                            workspace.absolute_str(".agent/tmp/issues.xsd"),
                            workspace.root().display()
                        ))
                    } else {
                        None
                    }
                    .into_iter(),
                )
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

    // If both files are missing, return fallback prompt with diagnostics (per AC #5)
    if !schema_exists && !last_output_exists {
        return format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - GENERATE REVIEW\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please review the implementation and provide your feedback.\n\n\
             Output format: <ralph-issues><ralph-issue>[Severity] file:line - Description. Fix.</ralph-issue></ralph-issues>\n"
        );
    }

    // Proceed with normal XSD retry prompt generation if at least schema exists
    let template_content = context
        .registry()
        .get_template("review_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/review_xsd_retry.txt").to_string());
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
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
                "Your previous review failed XSD validation.\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/issues.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
                 Please resend your review in valid XML format conforming to the XSD schema.\n"
            )
        });

    // Prepend diagnostic prefix if files were missing but we continued anyway
    if diagnostic_prefix.is_empty() {
        rendered_prompt
    } else {
        format!("{diagnostic_prefix}\n{rendered_prompt}")
    }
}

/// Generate XSD validation retry prompt for review with substitution log.
///
/// This variant assumes `.agent/tmp/last_output.xml` is already materialized.
pub fn prompt_review_xsd_retry_with_context_files_and_log(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
    template_name: &str,
) -> RenderedTemplate {
    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_review_xsd_retry_schema_files(workspace);

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/issues.xsd");
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    // Build diagnostic prefix for missing files (per acceptance criteria #3)
    let diagnostic_prefix = if !schema_exists || !last_output_exists {
        let parts: Vec<String> =
            std::iter::once("⚠️  WARNING: Required XSD retry files are missing:\n".to_string())
                .chain(
                    if !schema_exists {
                        Some(format!(
                            "  - Schema file: {} (workspace.root() = {})\n",
                            workspace.absolute_str(".agent/tmp/issues.xsd"),
                            workspace.root().display()
                        ))
                    } else {
                        None
                    }
                    .into_iter(),
                )
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

    // If both files are missing, return fallback prompt with diagnostics (per AC #5)
    if !schema_exists && !last_output_exists {
        let prompt_content = format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - GENERATE REVIEW\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please review the implementation and provide your feedback.\n\n\
             Output format: <ralph-issues><ralph-issue>[Severity] file:line - Description. Fix.</ralph-issue></ralph-issues>\n"
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
        .get_template("review_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/review_xsd_retry.txt").to_string());
    let variables = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    let template = Template::new(&template_content);
    template
        .render_with_log(template_name, &variables, &partials)
        .map(|mut rendered| {
            if !diagnostic_prefix.is_empty() {
                rendered.content = format!("{}\n{}", diagnostic_prefix, rendered.content);
            }
            rendered
        })
        .unwrap_or_else(|_| {
            let prompt_content = format!(
                "Your previous review failed XSD validation.\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/issues.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
                 Please resend your review in valid XML format conforming to the XSD schema.\n"
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
        })
}
