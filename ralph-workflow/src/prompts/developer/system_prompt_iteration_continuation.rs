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
    let mut prompt = String::new();
    let _ = writeln!(prompt, "You are in IMPLEMENTATION MODE. Recover from the previous failure to fully complete the entire plan.\n");
    prompt.push_str(include_str!("../templates/shared/_unattended_mode.txt"));
    prompt.push_str("\n\n");
    prompt.push_str(include_str!("../templates/shared/_no_git_commit.txt"));
    prompt.push_str("\n\n");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════"
    );
    let _ = writeln!(prompt, "IMPORTANT: EXECUTION CONTEXT");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════\n"
    );
    let _ = writeln!(
        prompt,
        "- No assumptions about downstream processing: do not reason about what happens"
    );
    let _ = writeln!(prompt, "  next in the pipeline.");
    let _ = writeln!(
        prompt,
        "- Your only job is implementation work in this repository."
    );
    let _ = writeln!(
        prompt,
        "- What matters is the WORK you do: the files you create/modify and the commands"
    );
    let _ = writeln!(prompt, "  you run.");
    let _ = writeln!(
        prompt,
        "- There is NO time limit. Take as long as needed to do the work correctly."
    );
    let _ = writeln!(
        prompt,
        "- Focus on making COMPLETE progress. Don't stop early or leave work half-done."
    );
    let _ = writeln!(
        prompt,
        "- You are an agent - keep going until the task is fully resolved.\n"
    );
    let _ = writeln!(prompt, "COMMUNICATION BOUNDARY (CRITICAL):");
    let _ = writeln!(
        prompt,
        "- Do NOT write summaries, status reports, or handoff notes in markdown files."
    );
    let _ = writeln!(
        prompt,
        "- Do NOT create STATUS.md, CURRENT_STATUS.md, CURRENT_IMPLEMENTATION.md, or"
    );
    let _ = writeln!(prompt, "  any similarly named context-transfer file.");
    let _ = writeln!(
        prompt,
        "- Do NOT create any file whose purpose is to communicate \"what happened\"."
    );
    let _ = writeln!(prompt, "- Keep context in code changes and tests only.\n");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════"
    );
    let _ = writeln!(prompt, "CONTINUATION CONTEXT");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════\n"
    );
    let _ = writeln!(
        prompt,
        "This is continuation attempt #{continuation_attempt}. Continuation is an exception path: it exists only because the previous run did not fully complete the entire plan. The previous attempt returned status \"{status}\".\n"
    );
    let _ = writeln!(
        prompt,
        "Blocker preventing full-plan completion: {summary}\n"
    );
    let _ = writeln!(
        prompt,
        "Success means finishing the entire remaining plan to completion, not merely advancing one blocked area. Going beyond the plan is acceptable when it produces more complete progress. You must do whatever it takes to complete the entire remaining plan and complete the entire remaining plan by whatever work is required. The plan is the goal, not the checklist. Success is completing the plan, not finishing the checklist. You must verify against the entire plan, not just the checklist, and verify that you completed the entire remaining plan before stopping."
    );
    let _ = writeln!(
        prompt,
        "Use the previous summary and checklist as execution context only. Focus on completing the entire remaining plan. Then give a comprehensive, detailed, ordered, actionable checklist for finishing the remaining plan. The checklist should resolve the remaining plan when completed, must be specific enough for the next run to continue without ambiguity, and should preserve any remaining non-plan follow-up work discovered during verification plus any failed verification commands or checks.\n"
    );
    if let Some(next_steps) = next_steps {
        let _ = writeln!(prompt, "Comprehensive, detailed, ordered, actionable checklist for the remaining plan that should resolve the remaining plan when completed. This is an ordered, actionable checklist for the remaining work needed to finish the entire plan. Treat this checklist as a starting point, not the boundary of the remaining work. It must be actionable and specific enough for the next run to continue without ambiguity. The next run must verify completion against the entire plan, not just these checklist items:");
        let _ = writeln!(prompt, "{next_steps}\n");
    }
    let _ = writeln!(prompt, "ORIGINAL REQUEST");
    let _ = writeln!(prompt, "====================\n");
    prompt.push_str(prompt_content);
    let _ = writeln!(prompt, "\n");
    let _ = writeln!(prompt, "IMPLEMENTATION PLAN");
    let _ = writeln!(prompt, "====================\n");
    prompt.push_str(plan_content);
    prompt.push_str("\n\n");
    prompt.push_str(include_str!(
        "../templates/shared/_developer_iteration_guidance.txt"
    ));
    prompt.push_str("\n\n");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════"
    );
    let _ = writeln!(prompt, "WHAT MATTERS");
    let _ = writeln!(
        prompt,
        "═══════════════════════════════════════════════════════════════════════════════\n"
    );
    let _ = writeln!(prompt, "1. Code changes you make");
    let _ = writeln!(
        prompt,
        "2. Meeting ALL requirements from plan and original request"
    );
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
            let builtin_template = Template::new(include_str!(
                "../templates/developer_iteration_continuation_xml.txt"
            ));

            builtin_template
                .render_with_partials(&variables, &partials)
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
            let prompt_content = Template::new(include_str!(
                "../templates/developer_iteration_continuation_xml.txt"
            ))
            .render_with_partials(&variables, &partials)
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
