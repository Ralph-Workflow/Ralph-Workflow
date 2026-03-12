//! Analysis agent effect handlers.

use crate::agents::AgentRole;
use crate::files::write_diff_backup_with_workspace;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, DevelopmentEvent, PipelineEvent};
use crate::reducer::handler::MainEffectHandler;
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
        // Read PLAN.md (non-fatal if missing).
        // Requirements: analysis should still run and report missing inputs.
        let plan_path = Path::new(".agent/PLAN.md");
        let plan_content = match ctx.workspace.read(plan_path) {
            Ok(s) => s,
            Err(err) => {
                // Best-effort fallback: older checkpoints or interrupted runs may still have
                // the XML plan available.
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
        };

        // Generate git diff since HEAD (working-tree vs. last commit), non-fatal if it fails.
        //
        // Use the HEAD baseline (same as commit phase) so that already-committed changes from
        // prior iterations are not included in the analysis context. The start_commit baseline
        // is intentionally sticky and would accumulate all changes since pipeline start.
        //
        // For analysis, we must remain context-free: do NOT instruct git commands, and do NOT
        // silently reuse a potentially stale `.agent/DIFF.backup`.
        //
        // Instead, if diff generation fails, emit an explicit placeholder and also refresh
        // `.agent/DIFF.backup` with that placeholder as best-effort diagnostic state.
        let diff_content = match crate::git_helpers::git_diff_in_repo(ctx.repo_root) {
            Ok(diff) => {
                // Best-effort: persist diff for prompt materialization fallbacks.
                // Missing `.agent/DIFF.backup` must not be fatal.
                let _ = write_diff_backup_with_workspace(ctx.workspace, &diff);
                diff
            }
            Err(err) => {
                let placeholder =
                    format!("[DIFF unavailable: failed to generate git diff ({err})]");
                let _ = write_diff_backup_with_workspace(ctx.workspace, &placeholder);
                placeholder
            }
        };

        // Generate analysis prompt
        let mut prompt = crate::prompts::analysis::generate_analysis_prompt(
            &plan_content,
            &diff_content,
            self.state.continuation.is_continuation(),
            ctx.workspace,
        );

        // XSD retry context: if the last analysis XML was invalid, instruct the agent to
        // read the schema error and previous invalid output from workspace files.
        if self.state.continuation.xsd_retry_pending {
            let xsd_error_path = ".agent/tmp/development_xsd_error.txt";
            let last_output_path = ".agent/tmp/development_result.xml";
            prompt = format!(
                "## XSD Retry Note\n\n\
Your previous XML output failed XSD validation.\n\
- Read the validation error: {xsd_error_path}\n\
- Read your previous invalid output: {last_output_path}\n\
Then produce a corrected development_result.xml that conforms to the schema.\n\n\
{prompt}"
            );
        }

        // Same-agent retry context: include retry guidance for analysis retries too.
        // This is especially critical for TimeoutWithContext retries without session support,
        // where the preamble points the agent to the persisted timeout context file.
        if self.state.continuation.same_agent_retry_pending {
            let retry_preamble =
                super::retry_guidance::same_agent_retry_preamble(&self.state.continuation);
            prompt = format!("{retry_preamble}\n{prompt}");
        }

        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Analysis);

        // Get current agent from chain
        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.developer_agent.to_string());

        // Invoke agent with analysis role
        let mut result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Analysis,
            AgentRole::Analysis,
            &agent,
            None,
            prompt,
        )?;

        // Emit AnalysisAgentInvoked event if agent invocation succeeded
        if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            result = result.with_additional_event(PipelineEvent::Development(
                DevelopmentEvent::AnalysisAgentInvoked { iteration },
            ));
        }

        Ok(result)
    }
}
