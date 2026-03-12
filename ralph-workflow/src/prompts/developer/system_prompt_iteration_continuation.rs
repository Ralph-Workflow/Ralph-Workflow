// Developer iteration continuation prompt generation functions.
//
// Contains functions for generating continuation prompts when previous
// attempts returned status="partial" or "failed".

fn fallback_continuation_prompt(
    continuation_attempt: u32,
    status: &str,
    summary: &str,
    prompt_content: &str,
    plan_content: &str,
) -> String {
    let mut prompt = String::new();
    let _ = writeln!(prompt, "CONTINUATION MODE\n");
    let _ = writeln!(
        prompt,
        "continuation is an exception path because the previous run failed to fully complete the plan and failed to fully complete the entire plan."
    );
    let _ = writeln!(
        prompt,
        "This is continuation attempt #{continuation_attempt}. Previous status: {status}\n"
    );
    let _ = writeln!(
        prompt,
        "Blocker preventing full-plan completion: {summary}\n"
    );
    let _ = writeln!(
        prompt,
        "The agent was expected to fully complete the entire plan. Success means finishing the entire remaining plan to completion, not just advancing one local step. Going beyond the plan is acceptable when it produces more complete progress."
    );
    let _ = writeln!(
        prompt,
        "Focus the continuation on recovery and completion. Return only recovery-critical information: why the full plan was not completed and an ordered, comprehensive checklist for the remaining plan. Provide an ordered, actionable checklist for the remaining plan and the remaining work needed to finish the entire plan. The checklist must be actionable and specific enough for the next run to continue without ambiguity. Do not include file lists or incidental activity summaries. Do not use the continuation to narrate incidental activity.\n"
    );
    let _ = writeln!(prompt, "ORIGINAL REQUEST");
    let _ = writeln!(prompt, "====================\n");
    prompt.push_str(prompt_content);
    let _ = writeln!(prompt, "\n");
    let _ = writeln!(prompt, "IMPLEMENTATION PLAN");
    let _ = writeln!(prompt, "====================\n");
    prompt.push_str(plan_content);
    prompt.push('\n');
    prompt
}

/// Generate continuation prompt for development iteration.
///
/// Used when the previous attempt returned status="partial" or "failed".
/// Includes context about what was previously done and guidance to continue.
pub fn prompt_developer_iteration_continuation_xml(
    context: &TemplateContext,
    continuation_state: &crate::reducer::state::ContinuationState,
    workspace: &dyn Workspace,
) -> String {
    write_dev_iteration_continuation_schema_file(workspace);

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

    let mut variables: HashMap<&str, String> = HashMap::new();
    variables.insert("PROMPT_PATH", "PROMPT.md".to_string());
    variables.insert("PLAN_PATH", ".agent/PLAN.md".to_string());
    variables.insert("PREVIOUS_STATUS", previous_status);
    variables.insert("PREVIOUS_SUMMARY", previous_summary);
    variables.insert("PROMPT", prompt_content.clone());
    variables.insert("PLAN", plan_content.clone());
    variables.insert(
        "CONTINUATION_ATTEMPT",
        continuation_state.continuation_attempt.to_string(),
    );
    variables.insert(
        "CONTINUATION_PROGRESS",
        format!(
            "continuation {} of {}",
            continuation_state.continuation_attempt, continuation_state.max_continue_count
        ),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XML_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xml"),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XSD_PATH",
        workspace.absolute_str(".agent/tmp/development_continuation_result.xsd"),
    );

    if let Some(next_steps) = previous_next_steps {
        variables.insert("PREVIOUS_NEXT_STEPS", next_steps);
    }

    template
        .render_with_partials(&variables, &partials)
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
            fallback_continuation_prompt(
                continuation_state.continuation_attempt,
                status,
                summary,
                &prompt_content,
                &plan_content,
            )
        })
}

/// Generate continuation prompt for development iteration with substitution log.
pub fn prompt_developer_iteration_continuation_xml_with_log(
    context: &TemplateContext,
    continuation_state: &crate::reducer::state::ContinuationState,
    workspace: &dyn Workspace,
    template_name: &str,
) -> crate::prompts::RenderedTemplate {
    use crate::prompts::{
        RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
    };

    write_dev_iteration_continuation_schema_file(workspace);

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

    let mut variables: HashMap<&str, String> = HashMap::new();
    variables.insert("PROMPT_PATH", "PROMPT.md".to_string());
    variables.insert("PLAN_PATH", ".agent/PLAN.md".to_string());
    variables.insert("PREVIOUS_STATUS", previous_status);
    variables.insert("PREVIOUS_SUMMARY", previous_summary);
    variables.insert("PROMPT", prompt_content.clone());
    variables.insert("PLAN", plan_content.clone());
    variables.insert(
        "CONTINUATION_ATTEMPT",
        continuation_state.continuation_attempt.to_string(),
    );
    variables.insert(
        "CONTINUATION_PROGRESS",
        format!(
            "continuation {} of {}",
            continuation_state.continuation_attempt, continuation_state.max_continue_count
        ),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XML_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xml"),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XSD_PATH",
        workspace.absolute_str(".agent/tmp/development_continuation_result.xsd"),
    );

    if let Some(next_steps) = previous_next_steps {
        variables.insert("PREVIOUS_NEXT_STEPS", next_steps);
    }

    template
        .render_with_log(template_name, &variables, &partials)
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
            let prompt_content = fallback_continuation_prompt(
                continuation_state.continuation_attempt,
                status,
                summary,
                &prompt_content,
                &plan_content,
            );
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
