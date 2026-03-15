// Fix analysis agent prompt generation.
//
// Generates prompts for the fix analysis agent to produce an objective assessment
// of fix results by comparing git diff against REVIEW ISSUES (from ISSUES.md).

use crate::prompts::content_reference::{DiffContentReference, PlanContentReference};
use crate::prompts::partials::get_shared_partials;
use crate::prompts::template_context::TemplateContext;
use crate::prompts::template_engine::Template;
use std::collections::HashMap;
use std::path::Path;

#[cfg_attr(test, allow(clippy::large_stack_frames))]
pub fn generate_fix_analysis_prompt(
    issues_content: &str,
    diff_content: &str,
    fix_result_content: &str,
    is_continuation: bool,
    workspace: &dyn crate::workspace::Workspace,
) -> String {
    // Use PlanContentReference for issues (same pattern as PLAN)
    let issues_ref = PlanContentReference::from_plan(
        issues_content.to_string(),
        Path::new(".agent/ISSUES.md"),
        None,
    );
    let diff_ref = DiffContentReference::from_diff(
        diff_content.to_string(),
        "",
        Path::new(".agent/DIFF.backup"),
    );

    let partials = get_shared_partials();
    let context = TemplateContext::default();
    let template_content = context
        .registry()
        .get_template("fix_analysis_system_prompt")
        .unwrap_or_else(|_| {
            include_str!("../templates/fix_analysis_system_prompt.txt").to_string()
        });

    let required_output = if is_continuation {
        r"<ralph-development-result>
  <ralph-status>partial|failed</ralph-status>
  <ralph-summary>Brief factual blocker-focused explanation of why the fix did not address the review issues</ralph-summary>
  <ralph-next-steps>comprehensive, detailed, ordered checklist that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks</ralph-next-steps>
</ralph-development-result>"
    } else {
        r"<ralph-development-result>
  <ralph-status>completed|partial|failed</ralph-status>
  <ralph-summary>Brief factual summary of what was fixed vs what the review identified</ralph-summary>
  <ralph-files-changed>Optional list of modified files (from DIFF)</ralph-files-changed>
  <ralph-next-steps>comprehensive, detailed, ordered checklist of remaining work that should resolve the remaining issues when completed, including remaining non-issue follow-up work uncovered during verification and any failed verification commands or checks (optional when status is completed)</ralph-next-steps>
</ralph-development-result>"
    };

    let variables = HashMap::from([
        ("PLAN", issues_ref.render_for_template()), // Reuse PLAN variable name for review issues
        (
            "DIFF",
            diff_ref
                .render_for_template()
                .replace("git diff", "git\u{00A0}diff"),
        ),
        ("FIX_RESULT", fix_result_content.to_string()),
        (
            "DEVELOPMENT_RESULT_XML_PATH",
            workspace.absolute_str(".agent/tmp/development_result.xml"),
        ),
        (
            "DEVELOPMENT_RESULT_XSD_PATH",
            workspace.absolute_str(if is_continuation {
                ".agent/tmp/development_continuation_result.xsd"
            } else {
                ".agent/tmp/development_result.xsd"
            }),
        ),
        ("REQUIRED_OUTPUT_XML", required_output.to_string()),
    ]);

    Template::new(&template_content)
        .render_with_partials(&variables, &partials)
        .unwrap_or_else(|_| {
            let issues = issues_ref.render_for_template();
            let diff = diff_ref.render_for_template();
            let out = workspace.absolute_str(".agent/tmp/development_result.xml");
            let xsd = workspace.absolute_str(if is_continuation {
                ".agent/tmp/development_continuation_result.xsd"
            } else {
                ".agent/tmp/development_result.xsd"
            });
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
        })
}
