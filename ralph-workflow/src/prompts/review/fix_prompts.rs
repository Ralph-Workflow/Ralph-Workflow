// Fix prompt generation functions for the review module.

/// Content struct for fix prompt parameters.
/// Bundles prompt, plan, and issues content for cleaner API.
#[derive(Debug)]
pub struct FixPromptContent {
    /// Content of PROMPT.md for context about the original request
    prompt_content: String,
    /// Content of PLAN.md for context about the implementation plan
    plan_content: String,
    /// Content of ISSUES.md for context about issues to fix
    issues_content: String,
}

impl FixPromptContent {
    pub fn new(prompt_content: &str, plan_content: &str, issues_content: &str) -> Self {
        Self {
            prompt_content: prompt_content.to_string(),
            plan_content: plan_content.to_string(),
            issues_content: issues_content.to_string(),
        }
    }
}

/// Format the list of files to modify for the fix mode prompt.
///
/// This function takes a list of file paths and formats them into a string
/// suitable for display in the fix mode prompt templates.
///
/// # Arguments
///
/// * `files` - Slice of file paths that may be modified
///
/// # Returns
///
/// A formatted string listing the files, or a message indicating no specific files were found.
fn format_files_section_xml(files: &[String]) -> String {
    if files.is_empty() {
        "No specific files identified - you may modify any files needed to fix the issues."
            .to_string()
    } else {
        format!("Files identified in issues:\n{}\n\nNOTE: If the issue references a file that is not listed here, you may still modify it.",
            files.iter()
                .map(|f| format!("- {f}"))
                .collect::<Vec<_>>()
                .join("\n"))
    }
}

/// Generate fix prompt with substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `content` - Bundled prompt/plan/issues content
/// * `files_to_modify` - List of files that may be modified
/// * `workspace` - Workspace for resolving absolute paths
/// * `template_name` - Name of the template for logging
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_fix_xml_with_log(
    context: &TemplateContext,
    content: FixPromptContent,
    files_to_modify: &[String],
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> RenderedTemplate {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("fix_mode_xml")
        .unwrap_or_else(|_| include_str!("../templates/fix_mode_xml.txt").to_string());

    // Base variables for fix prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", content.prompt_content.clone()),
        ("PLAN", content.plan_content.clone()),
        ("ISSUES", content.issues_content.clone()),
        ("FILES_TO_MODIFY", format_files_section_xml(files_to_modify)),
        (
            "FIX_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
    ]);

    // Compute capability variables using provided capabilities and policy flags
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    match Template::new(&template_content).render_with_log(template_name, &variables_ref, &partials) {
        Ok(rendered) => rendered,
        Err(err) => {
            // Extract missing variable from error
            let unsubstituted = match &err {
                crate::prompts::template_engine::TemplateError::MissingVariable(name) => {
                    vec![name.clone()]
                }
                _ => vec![],
            };

            let prompt_content = format!(
                "FIX MODE\n\nFix the issues:\n\n{}\n\n\
                 Based on requirements:\n{}\n\nPlan:\n{}\n\n\
                 Output format: <ralph-fix-result><ralph-summary>Summary</ralph-summary><ralph-fixes-applied>Changes made</ralph-fixes-applied></ralph-fix-result>\n",
                content.issues_content,
                content.prompt_content,
                content.plan_content
            );
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: template_name.to_string(),
                    substituted: vec![
                        SubstitutionEntry {
                            name: "PROMPT".to_string(),
                            source: SubstitutionSource::Value,
                        },
                        SubstitutionEntry {
                            name: "PLAN".to_string(),
                            source: SubstitutionSource::Value,
                        },
                        SubstitutionEntry {
                            name: "ISSUES".to_string(),
                            source: SubstitutionSource::Value,
                        },
                    ],
                    unsubstituted,
                },
            }
        }
    }
}

/// Generate XSD validation retry prompt for fix with substitution log.
///
/// This is the log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `xsd_error` - The XSD validation error message to include in the prompt
/// * `last_output` - The invalid XML output that failed validation
/// * `workspace` - Workspace for resolving absolute paths
/// * `template_name` - Name of the template for logging
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_fix_xsd_retry_with_log(
    context: &TemplateContext,
    xsd_error: &str,
    last_output: &str,
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> RenderedTemplate {
    write_fix_xsd_retry_files(workspace, last_output);

    let schema_path = Path::new(".agent/tmp/fix_result.xsd");
    let last_output_path = Path::new(".agent/tmp/last_output.xml");

    let schema_exists = workspace.exists(schema_path);
    let last_output_exists = workspace.exists(last_output_path);

    let diagnostic_prefix = if !schema_exists || !last_output_exists {
        let parts: Vec<String> =
            std::iter::once("⚠️  WARNING: Required XSD retry files are missing:\n".to_string())
                .chain(
                    if !schema_exists {
                        Some(format!(
                            "  - Schema file: {} (workspace.root() = {})\n",
                            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
                            workspace.root().display()
                        ))
                    } else {
                        None
                    },
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

    let build_manual_log = |template_name: &str, xsd_error: &str| {
        if xsd_error.is_empty() {
            SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: Vec::new(),
                unsubstituted: vec!["XSD_ERROR".to_string()],
            }
        } else {
            SubstitutionLog {
                template_name: template_name.to_string(),
                substituted: vec![SubstitutionEntry {
                    name: "XSD_ERROR".to_string(),
                    source: SubstitutionSource::Value,
                }],
                unsubstituted: Vec::new(),
            }
        }
    };

    if !schema_exists && !last_output_exists {
        let prompt_content = format!(
            "{diagnostic_prefix}XSD VALIDATION FAILED - FIX ISSUES\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please fix the issues described in ISSUES.md.\n\n\
             Output format: <ralph-fix-result><ralph-summary>Summary</ralph-summary><ralph-fixes-applied>Changes made</ralph-fixes-applied></ralph-fix-result>\n"
        );
        return RenderedTemplate {
            content: prompt_content,
            log: build_manual_log(template_name, xsd_error),
        };
    }

    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("fix_mode_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/fix_mode_xsd_retry.txt").to_string());

    // Base variables for XSD retry prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "FIX_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    // Compute capability variables using provided capabilities and policy flags
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    let template = Template::new(&template_content);
    template
        .render_with_log(template_name, &variables_ref, &partials)
        .map(|mut rendered| {
            if !diagnostic_prefix.is_empty() {
                rendered.content = format!("{}\n{}", diagnostic_prefix, rendered.content);
            }
            rendered
        })
        .unwrap_or_else(|_| {
            let prompt_content = format!(
                "XSD VALIDATION FAILED - FIX XML ONLY\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/fix_result.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
                 Rewrite .agent/tmp/fix_result.xml with valid XML.\n"
            );
            RenderedTemplate {
                content: prompt_content,
                log: build_manual_log(template_name, xsd_error),
            }
        })
}

/// Generate XML-based fix prompt using template registry.
///
/// This version uses XML output format with XSD validation for reliable parsing.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - Content of PROMPT.md for context about the original request
/// * `plan_content` - Content of PLAN.md for context about the implementation plan
/// * `issues_content` - Content of ISSUES.md for context about issues to fix
/// * `files_to_modify` - List of files that may be modified
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_fix_xml_with_context(
    context: &TemplateContext,
    prompt_content: &str,
    plan_content: &str,
    issues_content: &str,
    files_to_modify: &[String],
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("fix_mode_xml")
        .unwrap_or_else(|_| include_str!("../templates/fix_mode_xml.txt").to_string());

    // Base variables for fix prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_content.to_string()),
        ("PLAN", plan_content.to_string()),
        ("ISSUES", issues_content.to_string()),
        ("FILES_TO_MODIFY", format_files_section_xml(files_to_modify)),
        (
            "FIX_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
    ]);

    // Compute capability variables using provided capabilities and policy flags
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    Template::new(&template_content)
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            format!(
                "FIX MODE\n\nFix the issues:\n\n{issues_content}\n\n\
                 Based on requirements:\n{prompt_content}\n\nPlan:\n{plan_content}\n\n\
                 Output format: <ralph-fix-result><ralph-summary>Summary</ralph-summary><ralph-fixes-applied>Changes made</ralph-fixes-applied></ralph-fix-result>\n"
            )
        })
}

/// Generate XSD validation retry prompt for fix with error feedback.
///
/// This prompt is used when an AI agent produces fix result XML that fails XSD validation.
/// The XSD schema and last output are written to files at `.agent/tmp/` to avoid
/// bloating the prompt. The agent should read these files.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `_issues_content` - Content of ISSUES.md (unused - kept for API compatibility)
/// * `xsd_error` - The XSD validation error message to include in the prompt
/// * `last_output` - The invalid XML output that failed validation
/// * `workspace` - Workspace for writing XSD retry context files
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_fix_xsd_retry_with_context(
    context: &TemplateContext,
    _issues_content: &str,
    xsd_error: &str,
    last_output: &str,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    // Write context files to .agent/tmp/ for the agent to read
    write_fix_xsd_retry_files(workspace, last_output);
    prompt_fix_xsd_retry_with_context_files(context, xsd_error, workspace, session_caps)
}

/// Generate XSD validation retry prompt for fix with error feedback.
///
/// This variant assumes `.agent/tmp/last_output.xml` is already materialized.
///
/// Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
/// If required files are missing, a deterministic fallback prompt is produced that includes
/// diagnostic information but still provides valid instructions to the agent.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `xsd_error` - The XSD validation error message to include in the prompt
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_fix_xsd_retry_with_context_files(
    context: &TemplateContext,
    xsd_error: &str,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    // Ensure schema file exists; last_output.xml is expected to already be present.
    write_fix_xsd_retry_schema_files(workspace);

    // Check that required files exist
    let schema_path = Path::new(".agent/tmp/fix_result.xsd");
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
                            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
                            workspace.root().display()
                        ))
                    } else {
                        None
                    },
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
            "{diagnostic_prefix}XSD VALIDATION FAILED - FIX ISSUES\n\n\
             Error: {xsd_error}\n\n\
             The schema and previous output files could not be found. \
             Please fix the issues described in ISSUES.md.\n\n\
             Output format: <ralph-fix-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-fix-result>\n"
        );
    }

    // Proceed with normal XSD retry prompt generation if at least schema exists
    let template_content = context
        .registry()
        .get_template("fix_mode_xsd_retry")
        .unwrap_or_else(|_| include_str!("../templates/fix_mode_xsd_retry.txt").to_string());

    // Base variables for XSD retry prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("XSD_ERROR", xsd_error.to_string()),
        (
            "FIX_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
        (
            "LAST_OUTPUT_XML_PATH",
            workspace.absolute_str(".agent/tmp/last_output.xml"),
        ),
    ]);

    // Compute capability variables using provided capabilities and policy flags
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    let rendered_prompt = Template::new(&template_content)
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            format!(
                "Your previous fix failed XSD validation.\n\nError: {xsd_error}\n\n\
                 Read .agent/tmp/fix_result.xsd for the schema and .agent/tmp/last_output.xml for your previous output.\n\
                 Please resend your fix in valid XML format conforming to the XSD schema.\n"
            )
        });

    // Prepend diagnostic prefix if files were missing but we continued anyway
    if diagnostic_prefix.is_empty() {
        rendered_prompt
    } else {
        format!("{diagnostic_prefix}\n{rendered_prompt}")
    }
}
