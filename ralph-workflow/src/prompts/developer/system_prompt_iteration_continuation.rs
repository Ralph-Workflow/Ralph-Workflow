// Developer iteration continuation prompt generation functions.
//
// Contains functions for generating continuation prompts when previous
// attempts returned status="partial" or "failed".

/// Generate continuation prompt for development iteration.
///
/// Used when the previous attempt returned status="partial" or "failed".
/// Includes context about what was previously done and guidance to continue.
pub fn prompt_developer_iteration_continuation_xml(
    context: &TemplateContext,
    continuation_state: &crate::reducer::state::ContinuationState,
    workspace: &dyn Workspace,
) -> String {
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

    let previous_files_changed = continuation_state
        .previous_files_changed
        .as_ref()
        .map(|files| files.join("\n"));

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
    variables.insert("PROMPT", prompt_content);
    variables.insert("PLAN", plan_content);
    variables.insert(
        "CONTINUATION_ATTEMPT",
        continuation_state.continuation_attempt.to_string(),
    );
    variables.insert(
        "CONTINUATION_PROGRESS",
        format!(
            "continuation {} of {}",
            continuation_state.continuation_attempt,
            continuation_state.max_continue_count
        ),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XML_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xml"),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XSD_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xsd"),
    );

    // Optional fields - add if present
    if let Some(files) = previous_files_changed {
        variables.insert("PREVIOUS_FILES_CHANGED", files);
    }
    if let Some(next_steps) = previous_next_steps {
        variables.insert("PREVIOUS_NEXT_STEPS", next_steps);
    }

    template
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            // Fallback template if rendering fails
            let status = continuation_state
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
            format!(
                "CONTINUATION MODE\n\n\
                 This is continuation attempt #{}. Previous status: {}\n\n\
                 Previous summary: {}\n\n\
                 Continue the implementation from where you left off.\n\
                 Read PROMPT.md and .agent/PLAN.md for the full context.\n\n\
                 Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n",
                continuation_state.continuation_attempt,
                status,
                summary
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

    let previous_files_changed = continuation_state
        .previous_files_changed
        .as_ref()
        .map(|files| files.join("\n"));

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
    variables.insert("PROMPT", prompt_content);
    variables.insert("PLAN", plan_content);
    variables.insert(
        "CONTINUATION_ATTEMPT",
        continuation_state.continuation_attempt.to_string(),
    );
    variables.insert(
        "CONTINUATION_PROGRESS",
        format!(
            "continuation {} of {}",
            continuation_state.continuation_attempt,
            continuation_state.max_continue_count
        ),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XML_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xml"),
    );
    variables.insert(
        "DEVELOPMENT_RESULT_XSD_PATH",
        workspace.absolute_str(".agent/tmp/development_result.xsd"),
    );

    // Optional fields - add if present
    if let Some(files) = previous_files_changed {
        variables.insert("PREVIOUS_FILES_CHANGED", files);
    }
    if let Some(next_steps) = previous_next_steps {
        variables.insert("PREVIOUS_NEXT_STEPS", next_steps);
    }

    template.render_with_log(template_name, &variables, &partials).unwrap_or_else(|_| {
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
        let prompt_content = format!(
            "CONTINUATION MODE\n\n\
             This is continuation attempt #{}. Previous status: {}\n\n\
             Previous summary: {}\n\n\
             Continue the implementation from where you left off.\n\
             Read PROMPT.md and .agent/PLAN.md for the full context.\n\n\
             Output format: <ralph-development-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-development-result>\n",
            continuation_state.continuation_attempt,
            status,
            summary
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
