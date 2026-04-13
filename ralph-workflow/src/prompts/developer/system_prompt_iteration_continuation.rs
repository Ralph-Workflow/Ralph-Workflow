// Developer iteration continuation prompt generation functions.
//
// Contains functions for generating continuation prompts when previous
// attempts returned status="partial" or "failed".

fn fallback_continuation_prompt(
    continuation_attempt: u32,
    status: &str,
    summary: &str,
    next_steps: Option<&str>,
    prompt_content: &str,
    plan_content: &str,
) -> String {
    let sections: Vec<String> = vec![
        format!(
            "You are in IMPLEMENTATION MODE. Recover from the previous failure to fully complete the entire plan.\n"
        ),
        include_str!("../templates/shared/_unattended_mode.txt").to_string(),
        "\n\n".to_string(),
        include_str!("../templates/shared/_no_git_commit.txt").to_string(),
        "\n\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n".to_string(),
        "IMPORTANT: EXECUTION CONTEXT\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n".to_string(),
        "- No assumptions about downstream processing: do not reason about what happens\n".to_string(),
        "  next in the pipeline.\n".to_string(),
        "- Your only job is implementation work in this repository.\n".to_string(),
        "- What matters is the WORK you do: the files you create/modify and the commands\n".to_string(),
        "  you run.\n".to_string(),
        "- There is NO time limit. Take as long as needed to do the work correctly.\n".to_string(),
        "- Focus on making COMPLETE progress. Don't stop early or leave work half-done.\n".to_string(),
        "- You are an agent - keep going until the task is fully resolved.\n".to_string(),
        "COMMUNICATION BOUNDARY (CRITICAL):\n".to_string(),
        "- Do NOT write summaries, status reports, or handoff notes in markdown files.\n".to_string(),
        "- Do NOT create STATUS.md, CURRENT_STATUS.md, CURRENT_IMPLEMENTATION.md, or\n".to_string(),
        "  any similarly named context-transfer file.\n".to_string(),
        "- Do NOT create any file whose purpose is to communicate \"what happened\".\n".to_string(),
        "- Keep context in code changes and tests only.\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n".to_string(),
        "CONTINUATION CONTEXT\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n".to_string(),
        format!(
            "This is continuation attempt #{continuation_attempt}. Continuation is an exception path: it exists only because the previous run did not fully complete the entire plan. The previous attempt returned status \"{status}\".\n"
        ),
        format!("Blocker preventing full-plan completion: {summary}\n"),
        "Success means finishing the entire remaining plan to completion, not merely advancing one blocked area. Going beyond the plan is acceptable when it produces more complete progress. You must do whatever it takes to complete the entire remaining plan and complete the entire remaining plan by whatever work is required. The plan is the goal, not the checklist. Success is completing the plan, not finishing the checklist. You must verify against the entire plan, not just the checklist, and verify that you completed the entire remaining plan before stopping.\n".to_string(),
        "Use the previous summary and checklist as execution context only. Focus on completing the entire remaining plan. Then give a comprehensive, detailed, ordered, actionable checklist for finishing the remaining plan. The checklist should resolve the remaining plan when completed, must be specific enough for the next run to continue without ambiguity, and should preserve any remaining non-plan follow-up work discovered during verification plus any failed verification commands or checks.\n".to_string(),
    ];

    let sections = if let Some(next_steps) = next_steps {
        sections
            .into_iter()
            .chain([
                "Comprehensive, detailed, ordered, actionable checklist for the remaining plan that should resolve the remaining plan when completed. This is an ordered, actionable checklist for the remaining work needed to finish the entire plan. Treat this checklist as a starting point, not the boundary of the remaining work. It must be actionable and specific enough for the next run to continue without ambiguity. The next run must verify completion against the entire plan, not just these checklist items:\n".to_string(),
                format!("{next_steps}\n"),
            ])
            .collect::<Vec<_>>()
    } else {
        sections
    };

    let more_sections = vec![
        "ORIGINAL REQUEST\n".to_string(),
        "====================\n".to_string(),
        format!("{prompt_content}\n"),
        "IMPLEMENTATION PLAN\n".to_string(),
        "====================\n".to_string(),
        format!("{plan_content}\n"),
        include_str!("../templates/shared/_developer_iteration_guidance.txt").to_string(),
        "\n\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n"
            .to_string(),
        "WHAT MATTERS\n".to_string(),
        "═══════════════════════════════════════════════════════════════════════════════\n"
            .to_string(),
        "1. Code changes you make\n".to_string(),
        "2. Meeting ALL requirements from plan and original request\n".to_string(),
    ];

    sections.into_iter().chain(more_sections).collect()
}

/// Generate continuation prompt for development iteration.
///
/// Used when the previous attempt returned status="partial" or "failed".
/// Includes context about what was previously done and guidance to continue.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `continuation_state` - Continuation state with previous attempt info
/// * `workspace` - Workspace for resolving paths
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_developer_iteration_continuation_xml(
    context: &TemplateContext,
    continuation_state: &crate::reducer::state::ContinuationState,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities,
) -> String {
    write_dev_iteration_xsd_schema_file(workspace);

    let template_content = context
        .registry()
        .get_template("developer_iteration_continuation_xml")
        .unwrap_or_else(|_| {
            include_str!("../templates/developer_iteration_continuation_xml.txt").to_string()
        });
    let template = Template::new(&template_content);
    let partials = get_shared_partials();

    let previous_status = continuation_state
        .previous_status
        .as_ref()
        .map_or_else(|| "unknown".to_string(), |s| format!("{s}"));

    let previous_summary = continuation_state
        .previous_summary
        .clone()
        .unwrap_or_else(|| "No summary available".to_string());

    let previous_next_steps = continuation_state.previous_next_steps.clone();
    let prompt_content = workspace
        .read(std::path::Path::new("PROMPT.md"))
        .unwrap_or_else(|_| "(no prompt available)".to_string());
    let plan_content = workspace
        .read(std::path::Path::new(".agent/PLAN.md"))
        .unwrap_or_else(|_| "(no plan available)".to_string());

    let base_variables = HashMap::from([
        ("PROMPT_PATH", "PROMPT.md".to_string()),
        ("PLAN_PATH", ".agent/PLAN.md".to_string()),
        ("PREVIOUS_STATUS", previous_status),
        ("PREVIOUS_SUMMARY", previous_summary),
        ("PROMPT", prompt_content.clone()),
        ("PLAN", plan_content.clone()),
        (
            "CONTINUATION_ATTEMPT",
            continuation_state.continuation_attempt.to_string(),
        ),
        (
            "CONTINUATION_PROGRESS",
            format!(
                "continuation {} of {}",
                continuation_state.continuation_attempt, continuation_state.max_continue_count
            ),
        ),
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
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base variables, capability variables, and optional next_steps using functional style
    let variables: HashMap<String, String> = base_variables
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .chain(previous_next_steps.map(|s| ("PREVIOUS_NEXT_STEPS".to_string(), s)))
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            let status =
                continuation_state
                    .previous_status
                    .as_ref()
                    .map_or("unknown", |s| match s {
                        crate::reducer::state::DevelopmentStatus::Completed => "completed",
                        crate::reducer::state::DevelopmentStatus::Partial => "partial",
                        crate::reducer::state::DevelopmentStatus::Failed => "failed",
                    });
            let summary = continuation_state
                .previous_summary
                .as_ref()
                .map_or("No summary", |s| s.as_str());
            let builtin_template = Template::new(include_str!(
                "../templates/developer_iteration_continuation_xml.txt"
            ));

            builtin_template
                .render_with_partials(&variables_ref, &partials)
                .unwrap_or_else(|_| {
                    fallback_continuation_prompt(
                        continuation_state.continuation_attempt,
                        status,
                        summary,
                        continuation_state.previous_next_steps.as_deref(),
                        &prompt_content,
                        &plan_content,
                    )
                })
        })
}

/// Generate continuation prompt for development iteration with substitution log.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `continuation_state` - Continuation state with previous attempt info
/// * `workspace` - Workspace for resolving paths
/// * `template_name` - Name of the template for logging
/// * `capabilities` - The session's capability set for capability-driven template variables
/// * `session_caps` - Bundled session capabilities and policy flags
pub fn prompt_developer_iteration_continuation_xml_with_log(
    context: &TemplateContext,
    continuation_state: &crate::reducer::state::ContinuationState,
    workspace: &dyn Workspace,
    template_name: &str,
    session_caps: SessionCapabilities,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };

    write_dev_iteration_xsd_schema_file(workspace);

    let template_content = context
        .registry()
        .get_template("developer_iteration_continuation_xml")
        .unwrap_or_else(|_| {
            include_str!("../templates/developer_iteration_continuation_xml.txt").to_string()
        });
    let template = Template::new(&template_content);
    let partials = get_shared_partials();

    let previous_status = continuation_state
        .previous_status
        .as_ref()
        .map_or_else(|| "unknown".to_string(), |s| format!("{s}"));

    let previous_summary = continuation_state
        .previous_summary
        .clone()
        .unwrap_or_else(|| "No summary available".to_string());

    let previous_next_steps = continuation_state.previous_next_steps.clone();
    let prompt_content = workspace
        .read(std::path::Path::new("PROMPT.md"))
        .unwrap_or_else(|_| "(no prompt available)".to_string());
    let plan_content = workspace
        .read(std::path::Path::new(".agent/PLAN.md"))
        .unwrap_or_else(|_| "(no plan available)".to_string());

    let base_variables: HashMap<&str, String> = HashMap::from([
        ("PROMPT_PATH", "PROMPT.md".to_string()),
        ("PLAN_PATH", ".agent/PLAN.md".to_string()),
        ("PREVIOUS_STATUS", previous_status),
        ("PREVIOUS_SUMMARY", previous_summary),
        ("PROMPT", prompt_content.clone()),
        ("PLAN", plan_content.clone()),
        (
            "CONTINUATION_ATTEMPT",
            continuation_state.continuation_attempt.to_string(),
        ),
        (
            "CONTINUATION_PROGRESS",
            format!(
                "continuation {} of {}",
                continuation_state.continuation_attempt, continuation_state.max_continue_count
            ),
        ),
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
    let capability_vars =
        capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    // Merge base variables, capability variables, and optional next_steps using functional style
    let variables: HashMap<String, String> = base_variables
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .chain(capability_vars)
        .chain(previous_next_steps.map(|s| ("PREVIOUS_NEXT_STEPS".to_string(), s)))
        .collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    template
        .render_with_log(template_name, &variables_ref, &partials)
        .unwrap_or_else(|_| {
            let status =
                continuation_state
                    .previous_status
                    .as_ref()
                    .map_or("unknown", |s| match s {
                        crate::reducer::state::DevelopmentStatus::Completed => "completed",
                        crate::reducer::state::DevelopmentStatus::Partial => "partial",
                        crate::reducer::state::DevelopmentStatus::Failed => "failed",
                    });
            let summary = continuation_state
                .previous_summary
                .as_ref()
                .map_or("No summary", |s| s.as_str());
            let prompt_content = Template::new(include_str!(
                "../templates/developer_iteration_continuation_xml.txt"
            ))
            .render_with_partials(&variables_ref, &partials)
            .unwrap_or_else(|_| {
                fallback_continuation_prompt(
                    continuation_state.continuation_attempt,
                    status,
                    summary,
                    continuation_state.previous_next_steps.as_deref(),
                    &prompt_content,
                    &plan_content,
                )
            });
            RenderedTemplate {
                content: prompt_content,
                log: SubstitutionLog {
                    template_name: template_name.to_string(),
                    substituted: vec![
                        SubstitutionEntry {
                            name: "PREVIOUS_STATUS".to_string(),
                            source: SubstitutionSource::Value,
                        },
                        SubstitutionEntry {
                            name: "PREVIOUS_SUMMARY".to_string(),
                            source: SubstitutionSource::Value,
                        },
                    ],
                    unsubstituted: vec![],
                },
            }
        })
}
