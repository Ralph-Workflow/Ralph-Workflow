// Fix analysis agent prompt generation.
//
// Generates prompts for the fix analysis agent to produce an objective assessment
// of fix results by comparing git diff against REVIEW ISSUES (from ISSUES.md).

use crate::prompts::content_reference::{DiffContentReference, PlanContentReference};
use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_engine::Template;
use crate::prompts::template_variables::capability_template_variables;
use std::collections::HashMap;
use std::path::Path;

#[inline(never)]
fn build_template_variables(
    issues_content: &str,
    diff_content: &str,
    fix_result_content: &str,
    workspace: &dyn crate::workspace::Workspace,
    is_continuation: bool,
) -> HashMap<String, String> {
    let diff_for_template = DiffContentReference::from_diff(
        diff_content.to_string(),
        "",
        Path::new(".agent/DIFF.backup"),
    )
    .render_for_template()
    .replace("git diff", "git\u{00A0}diff");

    let plan_for_template = PlanContentReference::from_plan(
        issues_content.to_string(),
        Path::new(".agent/ISSUES.md"),
        None,
    )
    .render_for_template();

    let path_xml = workspace.absolute_str(".agent/tmp/development_result.xml");
    let path_xsd = workspace.absolute_str(if is_continuation {
        ".agent/tmp/development_continuation_result.xsd"
    } else {
        ".agent/tmp/development_result.xsd"
    });

    let required_output = if is_continuation {
        r"<ralph-development-result>
  <ralph-status>completed|partial|failed</ralph-status>
  <ralph-summary>Brief factual summary of what was fixed vs what the review identified</ralph-summary>
  <ralph-next-steps>comprehensive, detailed, ordered checklist that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks (optional when status is completed)</ralph-next-steps>
</ralph-development-result>"
    } else {
        r"<ralph-development-result>
  <ralph-status>completed|partial|failed</ralph-status>
  <ralph-summary>Brief factual summary of what was fixed vs what the review identified</ralph-summary>
  <ralph-files-changed>Optional list of modified files (from DIFF)</ralph-files-changed>
  <ralph-next-steps>comprehensive, detailed, ordered checklist of remaining work that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks (optional when status is completed)</ralph-next-steps>
</ralph-development-result>"
    };

    HashMap::from([
        ("PLAN".to_string(), plan_for_template),
        ("DIFF".to_string(), diff_for_template),
        ("FIX_RESULT".to_string(), fix_result_content.to_string()),
        ("DEVELOPMENT_RESULT_XML_PATH".to_string(), path_xml),
        ("DEVELOPMENT_RESULT_XSD_PATH".to_string(), path_xsd),
        (
            "REQUIRED_OUTPUT_XML".to_string(),
            required_output.to_string(),
        ),
    ])
}

/// Generate fix analysis agent prompt.
///
/// The fix analysis agent receives the ISSUES.md content, git diff, and fix result.
/// It verifies whether the changes satisfy the review requirements.
///
/// # Arguments
///
/// * `issues_content` - The review issues (ISSUES.md content)
/// * `diff_content` - The git diff since HEAD
/// * `fix_result_content` - The fix agent's self-assessment result
/// * `is_continuation` - Whether this is a continuation prompt
/// * `workspace` - Workspace for resolving absolute paths
/// * `capabilities` - The capabilities available to the agent
/// * `policy_flags` - The policy flags in effect
pub fn generate_fix_analysis_prompt(
    issues_content: &str,
    diff_content: &str,
    fix_result_content: &str,
    is_continuation: bool,
    workspace: &dyn crate::workspace::Workspace,
    capabilities: &crate::agents::session::CapabilitySet,
    policy_flags: &crate::agents::session::PolicyFlagSet,
) -> String {
    let partials = get_shared_partials();
    let context = TemplateContext::default();
    let template_content = context
        .registry()
        .get_template("fix_analysis_system_prompt")
        .unwrap_or_else(|_| {
            include_str!("../templates/fix_analysis_system_prompt.txt").to_string()
        });

    let base_vars = build_template_variables(
        issues_content,
        diff_content,
        fix_result_content,
        workspace,
        is_continuation,
    );

    // Compute capability variables using provided capabilities and policy flags
    let capability_vars = capability_template_variables(capabilities, policy_flags);

    // Merge base and capability variables using functional style (no mutation)
    let variables: HashMap<String, String> = base_vars.into_iter().chain(capability_vars).collect();

    // Convert to HashMap<&str, String> for rendering
    let variables_ref: HashMap<&str, String> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    let result = Template::new(&template_content).render_with_partials(&variables_ref, &partials);

    match result {
        Ok(output) => output,
        Err(_) => {
            let issues = PlanContentReference::from_plan(
                issues_content.to_string(),
                Path::new(".agent/ISSUES.md"),
                None,
            )
            .render_for_template();
            let diff = DiffContentReference::from_diff(
                diff_content.to_string(),
                "",
                Path::new(".agent/DIFF.backup"),
            )
            .render_for_template();
            let out = workspace.absolute_str(".agent/tmp/development_result.xml");
            let xsd = workspace.absolute_str(if is_continuation {
                ".agent/tmp/development_continuation_result.xsd"
            } else {
                ".agent/tmp/development_result.xsd"
            });
            let required_output = if is_continuation {
                r"<ralph-development-result>
  <ralph-status>completed|partial|failed</ralph-status>
  <ralph-summary>Brief factual summary of what was fixed vs what the review identified</ralph-summary>
  <ralph-next-steps>comprehensive, detailed, ordered checklist that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks (optional when status is completed)</ralph-next-steps>
</ralph-development-result>"
            } else {
                r"<ralph-development-result>
  <ralph-status>completed|partial|failed</ralph-status>
  <ralph-summary>Brief factual summary of what was fixed vs what the review identified</ralph-summary>
  <ralph-files-changed>Optional list of modified files (from DIFF)</ralph-files-changed>
  <ralph-next-steps>comprehensive, detailed, ordered checklist of remaining work that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks (optional when status is completed)</ralph-next-steps>
</ralph-development-result>"
            };
            format!(
                "FIX ANALYSIS\n\
                ============\n\
                \n\
                REVIEW ISSUES (from .agent/ISSUES.md):\n\
                {issues}\n\
                \n\
                GIT DIFF:\n\
                {diff}\n\
                \n\
                FIX AGENT SELF-ASSESSMENT (from .agent/tmp/fix_result.xml):\n\
                {fix_result_content}\n\
                \n\
                OUTPUT PATH: {out}\n\
                XSD SCHEMA: {xsd}\n\
                \n\
                REQUIRED OUTPUT FORMAT:\n\
                {required_output}",
            )
        }
    }
}
