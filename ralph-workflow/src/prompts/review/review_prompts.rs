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
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_review_xml_with_context(
    context: &TemplateContext,
    _prompt_content: &str,
    plan_content: &str,
    changes_content: &str,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let plan_value = if plan_content.trim().is_empty() {
        "(no plan available)".to_string()
    } else {
        plan_content.to_string()
    };
    let changes_value = if changes_content.trim().is_empty() {
        "(no diff available)".to_string()
    } else {
        changes_content.to_string()
    };

    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());

    // Base variables for review prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PLAN", plan_value),
        ("CHANGES", changes_value),
        (
            "ISSUES_XML_PATH",
            workspace.absolute_str(".agent/tmp/issues.xml"),
        ),
        (
            "ISSUES_XSD_PATH",
            workspace.absolute_str(".agent/tmp/issues.xsd"),
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
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `refs` - Content references for PLAN and CHANGES (diff)
/// * `workspace` - Workspace for resolving absolute paths
/// * `template_name` - Name of the template for logging
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_review_xml_with_references_and_log(
    context: &TemplateContext,
    refs: &crate::prompts::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> RenderedTemplate {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());

    // Base variables for review prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
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
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_review_xml_with_references(
    context: &TemplateContext,
    refs: &crate::prompts::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("review_xml")
        .unwrap_or_else(|_| include_str!("../templates/review_xml.txt").to_string());

    // Base variables for review prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
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
            let plan = refs.plan_for_template();
            let changes = refs.diff_for_template();
            format!("REVIEW MODE\n\nPLAN:\n{plan}\n\nCHANGES:\n{changes}\n")
        })
}
