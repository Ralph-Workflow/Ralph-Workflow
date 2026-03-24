//! Analysis agent effect handlers.

use crate::agents::AgentRole;
use crate::files::write_diff_backup_with_workspace;
use crate::phases::PhaseContext;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, DevelopmentEvent, PipelineEvent, ReviewEvent};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Invoke analysis agent to verify development results.
    ///
    /// TIMING: This handler runs after EVERY development iteration where
    /// `InvokeDevelopmentAgent` completed, regardless of iteration count.
    ///
    /// This handler:
    /// 1. Reads PLAN.md content
    /// 2. Generates git diff since HEAD (working-tree vs. last commit)
    /// 3. Builds analysis prompt with both inputs
    /// 4. Invokes agent to produce `development_result.xml`
    /// 5. Emits `AnalysisAgentInvoked` event
    ///
    /// The analysis agent has NO context from development execution,
    /// ensuring an objective assessment based purely on observable changes.
    ///
    /// Empty diff handling: The analysis agent receives empty diff and must
    /// determine if this means "no changes needed" (status=completed) or
    /// "dev agent failed to execute" (status=failed) based on PLAN.md context.
    pub(super) fn invoke_analysis_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let plan_content = read_plan_content_with_fallback(ctx);
        let diff_content = read_diff_content_with_backup(ctx);
        let prompt = crate::prompts::analysis::generate_analysis_prompt(
            &plan_content,
            &diff_content,
            self.state.continuation.is_continuation(),
            ctx.workspace,
        );
        let prompt = apply_xsd_retry_note(prompt, self.state.continuation.xsd_retry_pending);
        let prompt = apply_same_agent_retry_prefix(
            prompt,
            self.state.continuation.same_agent_retry_pending,
            &self.state.continuation,
        );
        let result = invoke_analysis_agent_with_prompt(self, ctx, prompt)?;
        let result = maybe_add_analysis_invoked_event(result, iteration);
        Ok(result)
    }

    /// Invoke fix analysis agent to verify fix results.
    ///
    /// TIMING: This handler runs after EVERY fix agent invocation to verify
    /// whether the fix addressed the review issues.
    ///
    /// This handler:
    /// 1. Reads ISSUES.md (review issues)
    /// 2. Generates git diff since HEAD
    /// 3. Reads `fix_result.xml` (fix agent's self-assessment)
    /// 4. Builds fix analysis prompt with all inputs
    /// 5. Invokes agent to produce `development_result.xml`
    /// 6. Emits `FixAnalysisAgentInvoked` event
    ///
    /// The fix analysis agent has NO context from fix agent execution,
    /// ensuring an objective assessment based purely on observable changes.
    pub(super) fn invoke_fix_analysis_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        let issues_content = read_issues_content(ctx);
        let diff_content = read_diff_content_with_backup(ctx);
        let fix_result_content = read_fix_result_content(ctx);
        let prompt = crate::prompts::analysis::generate_fix_analysis_prompt(
            &issues_content,
            &diff_content,
            &fix_result_content,
            self.state.continuation.fix_continue_pending,
            ctx.workspace,
        );
        let prompt = apply_xsd_retry_note(prompt, self.state.continuation.xsd_retry_pending);
        let prompt = apply_same_agent_retry_prefix(
            prompt,
            self.state.continuation.same_agent_retry_pending,
            &self.state.continuation,
        );
        let result = invoke_analysis_agent_with_prompt(self, ctx, prompt)?;
        let result = maybe_add_fix_analysis_invoked_event(result, pass);
        Ok(result)
    }
}

fn read_plan_content_with_fallback(ctx: &PhaseContext<'_>) -> String {
    let plan_path = Path::new(".agent/PLAN.md");
    match ctx.workspace.read(plan_path) {
        Ok(s) => s,
        Err(err) => {
            let xml_fallback = Path::new(".agent/tmp/plan.xml");
            match ctx.workspace.read(xml_fallback) {
                Ok(xml) => format!(
                    "[PLAN unavailable: failed to read .agent/PLAN.md ({err}); using fallback .agent/tmp/plan.xml]\n\n{xml}"
                ),
                Err(fallback_err) => format!(
                    "[PLAN unavailable: failed to read .agent/PLAN.md ({err}); also failed to read .agent/tmp/plan.xml ({fallback_err})]"
                ),
            }
        }
    }
}

fn read_diff_content_with_backup(ctx: &PhaseContext<'_>) -> String {
    match crate::git_helpers::git_diff_in_repo(ctx.repo_root) {
        Ok(diff) => {
            let _ = write_diff_backup_with_workspace(ctx.workspace, &diff);
            diff
        }
        Err(err) => {
            let placeholder = format!("[DIFF unavailable: failed to generate git diff ({err})]");
            let _ = write_diff_backup_with_workspace(ctx.workspace, &placeholder);
            placeholder
        }
    }
}

fn read_issues_content(ctx: &PhaseContext<'_>) -> String {
    let issues_path = Path::new(".agent/ISSUES.md");
    match ctx.workspace.read(issues_path) {
        Ok(s) => s,
        Err(err) => {
            format!("[REVIEW ISSUES unavailable: failed to read .agent/ISSUES.md ({err})]")
        }
    }
}

fn read_fix_result_content(ctx: &PhaseContext<'_>) -> String {
    let fix_result_path = Path::new(".agent/tmp/fix_result.xml");
    match ctx.workspace.read(fix_result_path) {
        Ok(s) => s,
        Err(err) => {
            format!("[FIX RESULT unavailable: failed to read .agent/tmp/fix_result.xml ({err})]")
        }
    }
}

fn apply_xsd_retry_note(prompt: String, xsd_retry_pending: bool) -> String {
    if xsd_retry_pending {
        let xsd_error_path = ".agent/tmp/development_xsd_error.txt";
        let last_output_path = ".agent/tmp/development_result.xml";
        format!(
            "## XSD Retry Note\n\n\
Your previous XML output failed XSD validation.\n\
- Read the validation error: {xsd_error_path}\n\
- Read your previous invalid output: {last_output_path}\n\
Then produce a corrected development_result.xml that conforms to the schema.\n\n\
{prompt}"
        )
    } else {
        prompt
    }
}

fn apply_same_agent_retry_prefix(
    prompt: String,
    same_agent_retry_pending: bool,
    continuation: &crate::reducer::state::ContinuationState,
) -> String {
    if same_agent_retry_pending {
        let retry_preamble = super::retry_guidance::same_agent_retry_preamble(continuation);
        format!("{retry_preamble}\n{prompt}")
    } else {
        prompt
    }
}

fn invoke_analysis_agent_with_prompt(
    handler: &mut MainEffectHandler,
    ctx: &mut PhaseContext<'_>,
    prompt: String,
) -> Result<EffectResult> {
    handler.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Analysis);
    let agent = handler
        .state
        .agent_chain
        .current_agent()
        .cloned()
        .unwrap_or_else(|| ctx.developer_agent.to_string());
    handler.invoke_agent(
        ctx,
        crate::agents::AgentDrain::Analysis,
        AgentRole::Analysis,
        &agent,
        None,
        |_session: &crate::agents::session::AgentSession| prompt.clone(),
    )
}

fn maybe_add_analysis_invoked_event(result: EffectResult, iteration: u32) -> EffectResult {
    if result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }) {
        result.with_additional_event(PipelineEvent::Development(
            DevelopmentEvent::AnalysisAgentInvoked { iteration },
        ))
    } else {
        result
    }
}

fn maybe_add_fix_analysis_invoked_event(result: EffectResult, pass: u32) -> EffectResult {
    if result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }) {
        result.with_additional_event(PipelineEvent::Review(
            ReviewEvent::FixAnalysisAgentInvoked { pass },
        ))
    } else {
        result
    }
}
