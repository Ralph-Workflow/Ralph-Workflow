// System prompt template and generation (planning).
//
// Contains functions for generating planning prompts and XSD retry prompts.

/// Generate prompt for planning phase.
///
/// The orchestrator provides requirements via the planning task context.
/// The plan content is returned as structured output (captured by JSON parser)
/// and the orchestrator writes it to .agent/PLAN.md.
///
/// This prompt is designed to be agent-agnostic and follows best practices
/// from Claude Code's plan mode implementation.
///
/// Reference: <https://github.com/Piebald-AI/claude-code-system-prompts>
///
/// # Panics
///
/// Panics if the current working directory cannot be determined.
#[cfg(test)]
#[must_use]
pub fn prompt_plan(prompt_content: Option<&str>) -> String {
    use crate::workspace::{Workspace, WorkspaceFs};
    use std::env;

    let workspace = WorkspaceFs::new(env::current_dir().unwrap());
    let partials = get_shared_partials();
    let template_content = include_str!("../templates/planning_xml.txt");
    let template = Template::new(template_content);
    let prompt_md = prompt_content.unwrap_or("No requirements provided");
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_md.to_string()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);
    let caps = crate::agents::session::CapabilitySet::defaults_for_drain(
        crate::agents::session::SessionDrain::Planning,
    );
    let flags = crate::agents::session::PolicyFlagSet::defaults_for_drain(
        crate::agents::session::SessionDrain::Planning,
    );
    let capability_vars = capability_template_variables(&caps, &flags);
    let variables: HashMap<String, String> = base_vars
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .collect();
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt_md}\n\nIdentify critical files and implementation steps.\n\nOutput format: <ralph-plan><ralph-summary>Summary</ralph-summary><ralph-implementation-steps>Steps</ralph-implementation-steps></ralph-plan>\n"
            )
        })
}

/// Generate prompt for planning phase using template registry.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_plan_with_context(
    context: &TemplateContext,
    prompt_content: Option<&str>,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("planning_xml")
        .unwrap_or_else(|_| {
            // Fallback to embedded template if registry fails
            include_str!("../templates/planning_xml.txt").to_string()
        });
    let template = Template::new(&template_content);
    let prompt_md = prompt_content.unwrap_or("No requirements provided");

    // Base variables for planning prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_md.to_string()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt_md}\n\nIdentify critical files and implementation steps.\n\nOutput format: <ralph-plan><ralph-summary>Summary</ralph-summary><ralph-implementation-steps>Steps</ralph-implementation-steps></ralph-plan>\n"
            )
        })
}

/// Generate XML-based planning prompt using template registry.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - The original user request (PROMPT.md content)
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_planning_xml_with_context(
    context: &TemplateContext,
    prompt_content: Option<&str>,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    // Write the XSD schema file so it's available for the agent to reference
    write_planning_xsd_schema_file(workspace);

    let template_content = context
        .registry()
        .get_template("planning_xml")
        .unwrap_or_else(|_| include_str!("../templates/planning_xml.txt").to_string());
    let template = Template::new(&template_content);
    let prompt_md = prompt_content.unwrap_or("No requirements provided");

    // Base variables for planning prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_md.to_string()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            format!(
                "PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt_md}\n\n\
             Output format: <ralph-plan><ralph-summary>Summary</ralph-summary><ralph-implementation-steps>Steps</ralph-implementation-steps></ralph-plan>\n"
            )
        })
}

/// Generate planning prompt with size-aware content references and substitution log.
///
/// This is the new log-based version that returns both content and substitution tracking.
/// Use this version in handlers to enable log-based validation.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_ref` - Content reference for PROMPT
/// * `workspace` - Workspace for resolving absolute paths
/// * `template_name` - Name of the template for logging
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_planning_xml_with_references_and_log(
    context: &TemplateContext,
    prompt_ref: &super::content_reference::PromptContentReference,
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };

    let partials = get_shared_partials();
    // Write the XSD schema file so it's available for the agent to reference
    write_planning_xsd_schema_file(workspace);

    let template_content = context
        .registry()
        .get_template("planning_xml")
        .unwrap_or_else(|_| include_str!("../templates/planning_xml.txt").to_string());
    let template = Template::new(&template_content);

    // Base variables for planning prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_ref.render_for_template()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
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

            let prompt = prompt_ref.render_for_template();
            let prompt_content =
                format!("PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt}\n");
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: template_name.to_string(),
                    substituted: vec![SubstitutionEntry {
                        name: "PROMPT".to_string(),
                        source: SubstitutionSource::Value,
                    }],
                    unsubstituted,
                },
            }
        }
    }
}

/// Generate planning prompt with size-aware content references.
///
/// This version uses `PromptContentReference` which automatically handles
/// oversized content by referencing file paths instead of embedding inline.
/// Use this when content may exceed CLI argument limits.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_ref` - Content reference for PROMPT
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_planning_xml_with_references(
    context: &TemplateContext,
    prompt_ref: &super::content_reference::PromptContentReference,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    let partials = get_shared_partials();
    // Write the XSD schema file so it's available for the agent to reference
    write_planning_xsd_schema_file(workspace);

    let template_content = context
        .registry()
        .get_template("planning_xml")
        .unwrap_or_else(|_| include_str!("../templates/planning_xml.txt").to_string());
    let template = Template::new(&template_content);

    // Base variables for planning prompt
    let base_vars: HashMap<&str, String> = HashMap::from([
        ("PROMPT", prompt_ref.render_for_template()),
        (
            "PLAN_XML_PATH",
            workspace.absolute_str(".agent/tmp/plan.xml"),
        ),
        (
            "PLAN_XSD_PATH",
            workspace.absolute_str(".agent/tmp/plan.xsd"),
        ),
    ]);

    // Compute capability variables from session capabilities
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

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            let prompt = prompt_ref.render_for_template();
            format!("PLANNING MODE\n\nCreate an implementation plan for:\n\n{prompt}\n")
        })
}



