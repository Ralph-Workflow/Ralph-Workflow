// Developer iteration XML prompt generation functions.
//
// Contains the core XML-based iteration prompt functions.

/// Generate developer iteration prompt.
///
/// Note: We do NOT tell the agent how many total iterations exist.
/// This prevents "context pollution" - the agent should complete their task fully
/// without knowing when the loop ends.
///
/// This prompt is agent-agnostic and works with any AI coding assistant.
/// Instructions for NOTES.md are intentionally vague to avoid creating
/// overly-specific context that could contaminate future runs.
///
/// # Arguments
///
/// * `iteration` - The current iteration number (accepted for API compatibility, not exposed to agent)
/// * `total` - The total number of iterations (accepted for API compatibility, not exposed to agent)
/// * `context` - The context level (minimal or normal) (accepted for API compatibility, not used in template)
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `plan_content` - The implementation plan (.agent/PLAN.md content)
#[cfg(test)]
#[must_use]
pub fn prompt_developer_iteration(
    iteration: u32,
    total: u32,
    context: ContextLevel,
    prompt_content: &str,
    plan_content: &str,
) -> String {
    let partials = get_shared_partials();
    // Note: iteration, total, and context are accepted for API compatibility
    // but are intentionally not exposed to the agent to prevent context pollution.
    let _ = (iteration, total, context);

    let template_content = include_str!("../templates/developer_iteration_xml.txt");
    let template = Template::new(template_content);
    let variables = HashMap::from([
        ("PROMPT", prompt_content.to_string()),
        ("PLAN", plan_content.to_string()),
    ]);

    template
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt_content}\n\nIMPLEMENTATION PLAN:\n{plan_content}\n\nExecute the next steps from the plan above.\n\nOutput format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
            )
        })
}

/// Generate developer iteration prompt using template registry.
///
/// This version uses the template registry which supports user template overrides.
/// It's the recommended way to generate prompts going forward.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `iteration` - The current iteration number (accepted for API compatibility, not exposed to agent)
/// * `total` - The total number of iterations (accepted for API compatibility, not exposed to agent)
/// * `ctx_level` - The context level (minimal or normal) (accepted for API compatibility, not used in template)
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `plan_content` - The implementation plan (.agent/PLAN.md content)
/// * `session_caps` - Bundled session capabilities and policy flags
#[must_use]
pub fn prompt_developer_iteration_with_context(
    context: &TemplateContext,
    iteration: u32,
    total: u32,
    ctx_level: ContextLevel,
    prompt_content: &str,
    plan_content: &str,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    // Note: iteration, total, and ctx_level are accepted for API compatibility
    // but are intentionally not exposed to the agent to prevent context pollution.
    let _ = (iteration, total, ctx_level);

    let template_content = context
        .registry()
        .get_template("developer_iteration_xml")
        .unwrap_or_else(|_| {
            // Fallback to embedded template if registry fails
            include_str!("../templates/developer_iteration_xml.txt").to_string()
        });
    let template = Template::new(&template_content);

    // Base variables for developer iteration prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_content.to_string()),
        ("PLAN", plan_content.to_string()),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
    );

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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt_content}\n\nIMPLEMENTATION PLAN:\n{plan_content}\n\nExecute the next steps from the plan above.\n\nOutput format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
            )
        })
}

/// Generate XML-based developer iteration prompt using template registry.
///
/// This version uses XML output format with XSD validation for reliable parsing.
/// It's the recommended format for development iteration going forward.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `plan_content` - The implementation plan (.agent/PLAN.md content)
/// * `workspace` - Workspace for resolving absolute paths
/// * `capabilities` - The session's capability set for capability-driven template variables
/// * `policy_flags` - The session's policy flag set for policy-driven template variables
pub fn prompt_developer_iteration_xml_with_context(
    context: &TemplateContext,
    prompt_content: &str,
    plan_content: &str,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("developer_iteration_xml")
        .unwrap_or_else(|_| include_str!("../templates/developer_iteration_xml.txt").to_string());
    let template = Template::new(&template_content);

    // Base variables for developer iteration prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_content.to_string()),
        ("PLAN", plan_content.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
    );

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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            format!(
                "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt_content}\n\n\
             IMPLEMENTATION PLAN:\n{plan_content}\n\n\
             Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n"
            )
        })
}

/// Generate developer iteration prompt with size-aware content references and substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `refs` - Content references for PROMPT and PLAN
/// * `workspace` - Workspace for resolving absolute paths
/// * `template_name` - Name of the template for logging
/// * `capabilities` - The session's capability set for capability-driven template variables
/// * `policy_flags` - The session's policy flag set for policy-driven template variables
pub fn prompt_developer_iteration_xml_with_references_and_log(
    context: &TemplateContext,
    refs: &super::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };

    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("developer_iteration_xml")
        .unwrap_or_else(|_| include_str!("../templates/developer_iteration_xml.txt").to_string());
    let template = Template::new(&template_content);

    // Base variables for developer iteration prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", refs.prompt_for_template()),
        ("PLAN", refs.plan_for_template()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
    );

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

    match template.render_with_log(template_name, &variables_ref, &partials) {
        Ok(rendered) => rendered,
        Err(err) => {
            // Extract missing variable from error
            let unsubstituted = match &err {
                crate::prompts::template_engine::TemplateError::MissingVariable(name) => {
                    vec![name.clone()]
                }
                _ => vec![],
            };

            let prompt = refs.prompt_for_template();
            let plan = refs.plan_for_template();
            let prompt_content = format!(
                "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt}\n\n\
             IMPLEMENTATION PLAN:\n{plan}\n\n\
             Output format: <ralph-development-result>...</ralph-development-result>\n"
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
                    ],
                    unsubstituted,
                },
            }
        }
    }
}

/// Generate developer iteration prompt with size-aware content references.
///
/// This version uses `PromptContentReferences` which automatically handles
/// oversized content by referencing file paths instead of embedding inline.
/// Use this when content may exceed CLI argument limits.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `refs` - Content references for PROMPT and PLAN
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_developer_iteration_xml_with_references(
    context: &TemplateContext,
    refs: &super::content_builder::PromptContentReferences,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("developer_iteration_xml")
        .unwrap_or_else(|_| include_str!("../templates/developer_iteration_xml.txt").to_string());
    let template = Template::new(&template_content);

    // Base variables for developer iteration prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", refs.prompt_for_template()),
        ("PLAN", refs.plan_for_template()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
    let capability_vars = capability_template_variables(
        session_caps.capabilities,
        session_caps.policy_flags,
    );

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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            let prompt = refs.prompt_for_template();
            let plan = refs.plan_for_template();
            format!(
                "IMPLEMENTATION MODE\n\nORIGINAL REQUEST:\n{prompt}\n\n\
             IMPLEMENTATION PLAN:\n{plan}\n\n\
             Output format: <ralph-development-result>...</ralph-development-result>\n"
            )
        })
}
