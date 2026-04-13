// Fix prompt generation functions.

/// Generate fix prompt (applies to either role).
///
/// This prompt is agent-agnostic and works with any AI coding assistant.
/// Uses a template-based approach for consistency with review prompts.
///
/// # Agent-Orchestrator Separation
///
/// The fix agent receives ISSUES content (embedded by the orchestrator after extracting
/// from the reviewer's JSON output) and modifies source code files to fix issues.
/// The agent returns structured output (completion status) that the orchestrator
/// captures via JSON logging.
///
/// ISSUES.md is an orchestrator-managed file - the agent should NOT modify it.
/// The orchestrator writes ISSUES.md before invoking the fix agent and may
/// delete it after fix cycles (e.g., in isolation mode).
///
/// # Constraints
///
/// The fix agent is constrained to ONLY work on files mentioned in the ISSUES content.
/// This prevents the agent from exploring the repository and keeps changes
/// focused on the issues identified during review.
///
/// # Arguments
///
/// * `prompt_content` - Content of PROMPT.md for context about the original request
/// * `plan_content` - Content of PLAN.md for context about the implementation plan
/// * `issues_content` - Content of ISSUES.md for context about issues to fix
///
/// # Panics
///
/// Panics if the current working directory cannot be determined.
#[cfg(test)]
#[must_use]
pub fn prompt_fix(prompt_content: &str, plan_content: &str, issues_content: &str) -> String {
    use crate::workspace::WorkspaceFs;
    use std::env;

    let workspace = WorkspaceFs::new(env::current_dir().unwrap());
    let partials = get_shared_partials();
    let template_content = ralph_workflow_policy::FIX_MODE_TEMPLATE;

    // Extract file paths from ISSUES content to provide explicit list
    let files_to_modify = extract_file_paths_from_issues(issues_content);
    let files_section = format_files_section(&files_to_modify);

    let variables = HashMap::from([
        ("PROMPT", prompt_content.to_string()),
        ("PLAN", plan_content.to_string()),
        ("ISSUES", issues_content.to_string()),
        ("FILES_TO_MODIFY", files_section),
        (
            "FIX_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH",
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
    ]);
    Template::new(template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "FIX MODE\n\nRead .agent/ISSUES.md and fix the issues found.\n\nContext:\nPROMPT:\n{prompt_content}\n\nPLAN:\n{plan_content}\n\nOutput format: <ralph-fix-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-fix-result>\n"
            )
        })
}

/// Generate fix prompt using template registry.
///
/// This version uses the template registry which supports user template overrides.
/// It's the recommended way to generate prompts going forward.
///
/// # Arguments
///
/// * `context` - Template context containing the template registry
/// * `prompt_content` - Content of PROMPT.md for context about the original request
/// * `plan_content` - Content of PLAN.md for context about the implementation plan
/// * `issues_content` - Content of ISSUES.md for context about issues to fix
/// * `workspace` - Workspace for resolving absolute paths
/// * `session_caps` - Session capabilities for RFC-009 capability injection
pub fn prompt_fix_with_context(
    context: &TemplateContext,
    prompt_content: &str,
    plan_content: &str,
    issues_content: &str,
    workspace: &dyn Workspace,
    session_caps: SessionCapabilities<'_>,
) -> String {
    let partials = get_shared_partials();
    let template_content = context
        .registry()
        .get_template("fix_mode")
        .unwrap_or_else(|_| ralph_workflow_policy::FIX_MODE_TEMPLATE.to_string());

    // Extract file paths from ISSUES content to provide explicit list
    let files_to_modify = extract_file_paths_from_issues(issues_content);
    let files_section = format_files_section(&files_to_modify);

    // Generate capability template variables for RFC-009 session capability injection
    let cap_vars = capability_template_variables(session_caps.capabilities, session_caps.policy_flags);

    let base_vars: HashMap<String, String> = HashMap::from([
        ("PROMPT".to_string(), prompt_content.to_string()),
        ("PLAN".to_string(), plan_content.to_string()),
        ("ISSUES".to_string(), issues_content.to_string()),
        ("FILES_TO_MODIFY".to_string(), files_section),
        (
            "FIX_RESULT_XML_PATH".to_string(),
            workspace.absolute_str(".agent/tmp/fix_result.xml"),
        ),
        (
            "FIX_RESULT_XSD_PATH".to_string(),
            workspace.absolute_str(".agent/tmp/fix_result.xsd"),
        ),
    ]);

    // Merge base variables with capability variables
    let merged_vars: HashMap<String, String> = base_vars
        .into_iter()
        .chain(cap_vars)
        .collect();

    // Convert to HashMap<&str, String> for render_with_partials
    let variables_ref: HashMap<&str, String> = merged_vars
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    Template::new(&template_content)
        .render_with_partials(&variables_ref, &partials)
        .unwrap_or_else(|_| {
            // Embedded fallback template (XML format)
            format!(
                "FIX MODE\n\nRead .agent/ISSUES.md and fix the issues found.\n\nContext:\nPROMPT:\n{prompt_content}\n\nPLAN:\n{plan_content}\n\nOutput format: <ralph-fix-result><ralph-status>completed|partial|failed</ralph-status><ralph-summary>Summary</ralph-summary></ralph-fix-result>\n"
            )
        })
}

/// Format the files section for the fix prompt.
///
/// If files are found, formats them as a bulleted list with a clear header.
/// If no files are found, provides a fallback message indicating that the
/// agent may work on any files in the repository to fix the issues.
fn format_files_section(files: &[String]) -> String {
    if files.is_empty() {
        "================================================================================
 FILES YOU MAY MODIFY
 ================================================================================

 (No specific files were extracted from ISSUES content)

 PERMISSIONS: FAIL CLOSED (NO FILE LIST PROVIDED)

 IMPORTANT: In fix mode, you MUST ONLY work on files mentioned in the ISSUES content.

 If no file paths were extracted, do NOT modify any files. Report `issues_remain`
 and include that the orchestrator must provide an explicit file list (or update
 the issue format to include file paths) to proceed safely.

 The ISSUES content is already embedded in this prompt - review it carefully.

 ================================================================================
 END OF FILES SECTION
 ================================================================================
 "
        .to_string()
    } else {
        let files_list: String = files.iter().map(|file| format!("- {file}\n")).collect();
        format!(
            "================================================================================
 FILES YOU MAY MODIFY
 ================================================================================

 {files_list}
 IMPORTANT: Work ONLY with the files listed above. The issues
 content is already embedded in this prompt - you do NOT need to
 read or discover any files to know what to fix.

 ================================================================================
 END OF FILES SECTION
 ================================================================================
 "
        )
    }
}
