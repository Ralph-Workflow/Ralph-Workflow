//! Effect handler implementation for pipeline side effects.
//!
//! This module implements the [`EffectHandler`] trait to execute pipeline effects
//! through the reducer architecture. Effect handlers perform actual work (agent
//! invocation, git operations, file I/O) and emit events that drive state transitions.
//!
//! # Architecture Contract
//!
//! ```text
//! State → Orchestrator → Effect → Handler → Event → Reducer → State
//!                                  ^^^^^^^
//!                                  Impure execution (this module)
//! ```
//!
//! ## Handler Responsibilities
//!
//! - **Execute effects**: Perform the I/O operation specified by the effect
//! - **Report outcomes**: Emit events describing what happened (success/failure)
//! - **Use workspace abstraction**: All filesystem access via `ctx.workspace`
//! - **Single-task execution**: Execute exactly one effect, no hidden retry logic
//!
//! ## Reducer Responsibilities (NOT handler)
//!
//! - **Pure state transitions**: Process events to update state
//! - **Policy decisions**: Retry, fallback, phase progression
//! - **Control flow**: Determine what happens next based on events
//!
//! # Key Principle: Handlers Report, Reducers Decide
//!
//! Handlers must NOT contain decision logic. Examples:
//!
//! ```ignore
//! // WRONG - Handler decides to retry
//! fn handle_invoke_agent() -> Result<EffectResult> {
//!     for attempt in 0..3 {  // NO! Reducer controls retry
//!         if let Ok(output) = invoke_agent() {
//!             return Ok(output);
//!         }
//!     }
//! }
//!
//! // CORRECT - Handler reports outcome, reducer decides
//! fn handle_invoke_agent() -> Result<EffectResult> {
//!     match invoke_agent() {
//!         Ok(output) => Ok(EffectResult::event(
//!             AgentEvent::InvocationSucceeded { output }
//!         )),
//!         Err(e) => Ok(EffectResult::event(
//!             AgentEvent::InvocationFailed { error: e, retriable: true }
//!         )),
//!     }
//! }
//! ```
//!
//! The reducer processes `InvocationFailed` and decides whether to retry
//! (increment retry count, emit retry effect) or fallback (advance chain).
//!
//! # Workspace Abstraction
//!
//! All filesystem operations MUST use `ctx.workspace`:
//!
//! ```ignore
//! // CORRECT
//! ctx.workspace.write(path, content)?;
//! let content = ctx.workspace.read(path)?;
//!
//! // WRONG - Never use std::fs in handlers
//! std::fs::write(path, content)?;
//! ```
//!
//! This abstraction enables:
//! - In-memory testing with `MemoryWorkspace`
//! - Proper error handling and path resolution
//! - Consistent file operations across the pipeline
//!
//! See [`docs/agents/workspace-trait.md`] for details.
//!
//! # Testing Handlers
//!
//! Handlers require mocks for I/O (workspace) but NOT for reducer/orchestration:
//!
//! ```ignore
//! #[test]
//! fn test_invoke_agent_emits_success_event() {
//!     let workspace = MemoryWorkspace::new_test();
//!     let mut ctx = create_test_context(&workspace);
//!
//!     let result = handler.execute(
//!         Effect::InvokeAgent { role, agent, prompt },
//!         &mut ctx
//!     )?;
//!
//!     assert!(matches!(
//!         result.event,
//!         PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
//!     ));
//! }
//! ```
//!
//! # Module Organization
//!
//! - `agent` - Agent invocation and chain management
//! - `planning` - Planning phase effects (prompt, XML, validation)
//! - `development` - Development phase effects (iteration, continuation)
//! - `review` - Review phase effects (issue detection, fix application)
//! - `commit` - Commit phase effects (message generation, commit creation)
//! - `rebase` - Rebase effects (conflict resolution, validation)
//! - `checkpoint` - Checkpoint save/restore
//! - `context` - Context preparation and cleanup
//!
//! [`docs/agents/workspace-trait.md`]: https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/agents/workspace-trait.md

mod agent;
mod analysis;
mod chain;
mod checkpoint;
mod cloud;
mod commit;
mod commit_helpers;
mod context;
mod development;
mod development_prompt;
mod io_agent;
mod io_commit;
mod json_artifact;
mod lifecycle;
mod parallel;
mod planning;
mod planning_helpers;
mod rebase;
pub(crate) mod retry_guidance;
mod run_fix;
mod run_review;
mod run_review_prompt;

#[cfg(test)]
mod tests;

use crate::agents::session::audit::record_effect_check;
use crate::agents::session::capability_gate::required_capabilities as effect_required_capabilities;
use crate::agents::session::capability_gate::{check_effect_capability, is_ralph_internal_effect};
use crate::phases::PhaseContext;
use crate::prompts::{PromptHistoryEntry, PromptScopeKey};
use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase};
use crate::reducer::state::PipelineState;
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::hash::BuildHasher;

fn find_first_denied_capability(
    session: &crate::agents::session::AgentSession,
    required_caps: &[crate::agents::session::Capability],
) -> String {
    required_caps
        .iter()
        .find(|cap| {
            !matches!(
                session.check_capability(**cap),
                crate::agents::session::PolicyOutcome::Approved
            )
        })
        .map(|c| c.identifier().to_string())
        .unwrap_or_else(|| "unknown".to_string())
}

fn current_unix_timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn make_capability_denied_event(
    session: &crate::agents::session::AgentSession,
    required_caps: &[crate::agents::session::Capability],
    reason: String,
) -> PipelineEvent {
    let denied_capability = find_first_denied_capability(session, required_caps);
    let role = session.drain.into_role();
    PipelineEvent::Agent(AgentEvent::CapabilityDenied {
        role,
        capability: denied_capability,
        reason,
    })
}

fn audit_and_check_outcome(
    session: &crate::agents::session::AgentSession,
    effect: &Effect,
    required_caps: &[crate::agents::session::Capability],
    audit_trail: &crate::agents::session::AuditTrail,
) -> (
    crate::agents::session::AuditTrail,
    crate::agents::session::PolicyOutcome,
) {
    let outcome = check_effect_capability(session, effect);
    let timestamp = current_unix_timestamp();
    let effect_name = crate::agents::session::capability_gate::effect_name(effect);
    let new_trail = record_effect_check(
        audit_trail,
        &session.session_id,
        timestamp,
        &effect_name,
        required_caps,
        &outcome,
    );
    (new_trail, outcome)
}

fn effect_required_caps_if_gateable(
    effect: &Effect,
) -> Option<Vec<crate::agents::session::Capability>> {
    if is_ralph_internal_effect(effect) {
        return None;
    }
    let caps = effect_required_capabilities(effect);
    if caps.is_empty() {
        None
    } else {
        Some(caps)
    }
}

fn check_capability_gate(effect: &Effect, ctx: &mut PhaseContext<'_>) -> Option<EffectResult> {
    let required_caps = effect_required_caps_if_gateable(effect)?;
    let session = ctx.active_session.as_ref()?;
    let (new_trail, outcome) =
        audit_and_check_outcome(session, effect, &required_caps, &ctx.audit_trail);
    ctx.audit_trail = new_trail;
    if let crate::agents::session::PolicyOutcome::Denied { reason } = outcome {
        return Some(EffectResult::event(make_capability_denied_event(
            session,
            &required_caps,
            reason,
        )));
    }
    None
}

fn execute_backoff_wait(
    ctx: &mut PhaseContext<'_>,
    role: crate::agents::AgentRole,
    cycle: u32,
    duration_ms: u64,
) -> Result<EffectResult> {
    ctx.registry
        .retry_timer()
        .sleep(std::time::Duration::from_millis(duration_ms));
    Ok(EffectResult::event(
        PipelineEvent::agent_retry_cycle_started(role, cycle),
    ))
}

fn execute_write_continuation_context(
    ctx: &mut PhaseContext<'_>,
    data: &crate::reducer::effect::ContinuationContextData,
) -> Result<EffectResult> {
    development::write_continuation_context_to_workspace(ctx.workspace, ctx.logger, data)?;
    Ok(EffectResult::event(
        PipelineEvent::development_continuation_context_written(data.iteration, data.attempt),
    ))
}

fn ensure_completion_marker_dir(ctx: &PhaseContext<'_>) {
    if let Err(err) = ctx
        .workspace
        .create_dir_all(std::path::Path::new(".agent/tmp"))
    {
        ctx.logger.warn(&format!(
            "Failed to create completion marker directory: {err}"
        ));
    }
}

fn write_completion_marker_content(
    ctx: &PhaseContext<'_>,
    content: &str,
    is_failure: bool,
) -> std::result::Result<(), String> {
    let marker_path = std::path::Path::new(".agent/tmp/completion_marker");
    match ctx.workspace.write(marker_path, content) {
        Ok(()) => {
            ctx.logger.info(&format!(
                "Completion marker written: {}",
                if is_failure { "failure" } else { "success" }
            ));
            Ok(())
        }
        Err(err) => {
            ctx.logger
                .warn(&format!("Failed to write completion marker: {err}"));
            Err(err.to_string())
        }
    }
}

fn get_stored_or_generate_prompt_with_validation<F, S: BuildHasher>(
    scope_key: &PromptScopeKey,
    prompt_history: &std::collections::HashMap<String, PromptHistoryEntry, S>,
    current_content_id: Option<&str>,
    generator: F,
) -> (String, bool, bool)
where
    F: FnOnce() -> (String, bool),
{
    let key = scope_key.to_string();
    match prompt_history.get(&key) {
        Some(entry) if !content_id_mismatch(entry, current_content_id) => {
            (entry.content.clone(), true, false)
        }
        _ => {
            let (prompt, should_validate) = generator();
            (prompt, false, should_validate)
        }
    }
}

fn content_id_mismatch(entry: &PromptHistoryEntry, current_content_id: Option<&str>) -> bool {
    current_content_id.is_some_and(|current_id| entry.content_id.as_deref() != Some(current_id))
}

/// Main effect handler implementation.
///
/// This handler executes effects by calling pipeline subsystems and emitting reducer events.
pub struct MainEffectHandler {
    /// Current pipeline state
    pub state: PipelineState,
    /// Event log for replay/debugging
    pub event_log: Vec<PipelineEvent>,
}

impl MainEffectHandler {
    /// Create a new effect handler.
    #[must_use]
    pub const fn new(state: PipelineState) -> Self {
        Self {
            state,
            event_log: Vec::new(),
        }
    }
}

impl EffectHandler<'_> for MainEffectHandler {
    fn execute(&mut self, effect: Effect, ctx: &mut PhaseContext<'_>) -> Result<EffectResult> {
        if let Some(denied) = check_capability_gate(&effect, ctx) {
            self.event_log.push(denied.event.clone());
            return Ok(denied);
        }
        let result = self.execute_effect(effect, ctx)?;
        self.event_log.push(result.event.clone());
        self.event_log
            .extend(result.additional_events.iter().cloned());
        Ok(result)
    }
}

impl crate::app::event_loop::StatefulHandler for MainEffectHandler {
    fn update_state(&mut self, state: PipelineState) {
        self.state = state;
    }
}

impl MainEffectHandler {
    /// Helper to create phase transition UI event.
    const fn phase_transition_ui(&self, to: PipelinePhase) -> UIEvent {
        UIEvent::PhaseTransition {
            from: Some(self.state.phase),
            to,
        }
    }

    fn write_completion_marker(
        ctx: &PhaseContext<'_>,
        content: &str,
        is_failure: bool,
    ) -> std::result::Result<(), String> {
        ensure_completion_marker_dir(ctx);
        write_completion_marker_content(ctx, content, is_failure)
    }

    fn execute_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::AgentInvocation {
                role,
                agent,
                model,
                prompt,
            } => self.execute_agent_invocation_effect(ctx, role, agent, model, prompt),
            Effect::InitializeAgentChain { drain, .. } => {
                Ok(self.initialize_agent_chain(ctx, drain))
            }
            e => self.execute_non_agent_effect(e, ctx),
        }
    }

    fn execute_agent_invocation_effect(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        role: crate::agents::AgentRole,
        agent: String,
        model: Option<String>,
        prompt: String,
    ) -> Result<EffectResult> {
        // RFC-009: The closure receives the AgentSession created by invoke_agent.
        // In V1, session capabilities == drain defaults, so the pre-generated prompt
        // is correct. The closure still calls capability_template_variables_from_session
        // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
        self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::from(role),
            role,
            &agent,
            model.as_deref(),
            |session: &crate::agents::session::AgentSession| {
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                prompt.clone()
            },
        )
    }

    fn execute_non_agent_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::BackoffWait {
                role,
                cycle,
                duration_ms,
            } => execute_backoff_wait(ctx, role, cycle, duration_ms),
            Effect::ReportAgentChainExhausted { role, phase, cycle } => Err(
                crate::reducer::event::ErrorEvent::AgentChainExhausted { role, phase, cycle }
                    .into(),
            ),
            e => self.execute_parallel_or_phase_effect(e, ctx),
        }
    }

    fn execute_parallel_or_phase_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::EvaluateParallelPlan { plan } => {
                crate::reducer::boundary::parallel::evaluate_parallel_plan(ctx, &plan)
            }
            Effect::DispatchParallelWorkers { plan } => {
                crate::reducer::boundary::parallel::dispatch_parallel_workers(ctx, &plan)
            }
            Effect::InvokeParallelVerifier {
                plan,
                worker_results,
                iteration,
            } => crate::reducer::boundary::parallel::invoke_parallel_verifier(
                ctx,
                &plan,
                &worker_results,
                iteration,
            ),
            e => self.execute_phase_effect(e, ctx),
        }
    }

    fn execute_phase_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            e @ (Effect::PreparePlanningPrompt { .. }
            | Effect::MaterializePlanningInputs { .. }
            | Effect::CleanupRequiredFiles { .. }
            | Effect::InvokePlanningAgent { .. }
            | Effect::ExtractPlanningXml { .. }
            | Effect::ValidatePlanningXml { .. }
            | Effect::WritePlanningMarkdown { .. }
            | Effect::ArchivePlanningXml { .. }
            | Effect::ApplyPlanningOutcome { .. }) => self.execute_planning_effect(e, ctx),
            e @ (Effect::PrepareDevelopmentContext { .. }
            | Effect::MaterializeDevelopmentInputs { .. }
            | Effect::PrepareDevelopmentPrompt { .. }
            | Effect::InvokeDevelopmentAgent { .. }
            | Effect::InvokeAnalysisAgent { .. }
            | Effect::ExtractDevelopmentXml { .. }
            | Effect::ValidateDevelopmentXml { .. }
            | Effect::ApplyDevelopmentOutcome { .. }
            | Effect::ArchiveDevelopmentXml { .. }) => self.execute_development_effect(e, ctx),
            e => self.execute_phase_effect_b(e, ctx),
        }
    }

    fn execute_phase_effect_b(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            e @ (Effect::PrepareReviewContext { .. }
            | Effect::MaterializeReviewInputs { .. }
            | Effect::PrepareReviewPrompt { .. }
            | Effect::InvokeReviewAgent { .. }
            | Effect::ExtractReviewIssuesXml { .. }
            | Effect::ValidateReviewIssuesXml { .. }
            | Effect::WriteIssuesMarkdown { .. }
            | Effect::ExtractReviewIssueSnippets { .. }
            | Effect::ArchiveReviewIssuesXml { .. }
            | Effect::ApplyReviewOutcome { .. }
            | Effect::PrepareFixPrompt { .. }
            | Effect::InvokeFixAgent { .. }
            | Effect::InvokeFixAnalysisAgent { .. }
            | Effect::ExtractFixResultXml { .. }
            | Effect::ValidateFixResultXml { .. }
            | Effect::ApplyFixOutcome { .. }
            | Effect::ArchiveFixResultXml { .. }) => self.execute_review_effect(e, ctx),
            e @ (Effect::PrepareCommitPrompt { .. }
            | Effect::CheckCommitDiff
            | Effect::MaterializeCommitInputs { .. }
            | Effect::InvokeCommitAgent
            | Effect::ExtractCommitXml
            | Effect::ValidateCommitXml
            | Effect::ApplyCommitMessageOutcome
            | Effect::ArchiveCommitXml
            | Effect::CreateCommit { .. }
            | Effect::SkipCommit { .. }
            | Effect::CheckResidualFiles { .. }
            | Effect::CheckUncommittedChangesBeforeTermination) => {
                self.execute_commit_effect(e, ctx)
            }
            e @ (Effect::RunRebase { .. } | Effect::ResolveRebaseConflicts { .. }) => {
                self.execute_rebase_effect(e, ctx)
            }
            e => self.execute_lifecycle_effect(e, ctx),
        }
    }

    fn execute_planning_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PreparePlanningPrompt {
                iteration,
                prompt_mode,
            } => self.prepare_planning_prompt(ctx, iteration, prompt_mode),
            Effect::MaterializePlanningInputs { iteration } => {
                self.materialize_planning_inputs(ctx, iteration)
            }
            Effect::CleanupRequiredFiles { files } => Ok(self.cleanup_required_files(ctx, &files)),
            Effect::InvokePlanningAgent { iteration } => self.invoke_planning_agent(ctx, iteration),
            e => self.execute_planning_effect_b(e, ctx),
        }
    }

    fn execute_planning_effect_b(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::ExtractPlanningXml { iteration } => {
                Ok(self.extract_planning_xml(ctx, iteration))
            }
            Effect::ValidatePlanningXml { iteration } => self.validate_planning_xml(ctx, iteration),
            Effect::WritePlanningMarkdown { iteration } => {
                self.write_planning_markdown(ctx, iteration)
            }
            Effect::ArchivePlanningXml { iteration } => {
                Ok(Self::archive_planning_xml(ctx, iteration))
            }
            Effect::ApplyPlanningOutcome { iteration, valid } => {
                Ok(self.apply_planning_outcome(ctx, iteration, valid))
            }
            _ => unreachable!("execute_planning_effect called with non-planning effect"),
        }
    }

    fn execute_development_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PrepareDevelopmentContext { iteration } => {
                Ok(Self::prepare_development_context(ctx, iteration))
            }
            Effect::MaterializeDevelopmentInputs { iteration } => {
                self.materialize_development_inputs(ctx, iteration)
            }
            Effect::PrepareDevelopmentPrompt {
                iteration,
                prompt_mode,
            } => self.prepare_development_prompt(ctx, iteration, prompt_mode),
            Effect::InvokeDevelopmentAgent { iteration } => {
                self.invoke_development_agent(ctx, iteration)
            }
            e => self.execute_development_effect_b(e, ctx),
        }
    }

    fn execute_development_effect_b(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::InvokeAnalysisAgent { iteration } => self.invoke_analysis_agent(ctx, iteration),
            Effect::ExtractDevelopmentXml { iteration } => {
                Ok(self.extract_development_xml(ctx, iteration))
            }
            Effect::ValidateDevelopmentXml { iteration } => {
                Ok(self.validate_development_xml(ctx, iteration))
            }
            Effect::ApplyDevelopmentOutcome { iteration } => {
                self.apply_development_outcome(ctx, iteration)
            }
            Effect::ArchiveDevelopmentXml { iteration } => {
                Ok(Self::archive_development_xml(ctx, iteration))
            }
            _ => unreachable!("execute_development_effect called with non-development effect"),
        }
    }

    fn execute_review_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PrepareReviewContext { pass } => Ok(self.prepare_review_context(ctx, pass)),
            Effect::MaterializeReviewInputs { pass } => self.materialize_review_inputs(ctx, pass),
            Effect::PrepareReviewPrompt { pass, prompt_mode } => {
                self.prepare_review_prompt(ctx, pass, prompt_mode)
            }
            Effect::InvokeReviewAgent { pass } => self.invoke_review_agent(ctx, pass),
            Effect::ExtractReviewIssuesXml { pass } => {
                Ok(self.extract_review_issues_xml(ctx, pass))
            }
            Effect::ValidateReviewIssuesXml { pass } => {
                Ok(self.validate_review_issues_xml(ctx, pass))
            }
            e => self.execute_fix_effect(e, ctx),
        }
    }

    fn execute_fix_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::WriteIssuesMarkdown { pass } => self.write_issues_markdown(ctx, pass),
            Effect::ExtractReviewIssueSnippets { pass } => {
                self.extract_review_issue_snippets(ctx, pass)
            }
            Effect::ArchiveReviewIssuesXml { pass } => {
                Ok(Self::archive_review_issues_xml(ctx, pass))
            }
            e => self.execute_fix_outcome_or_agent_effect(e, ctx),
        }
    }

    fn execute_fix_outcome_or_agent_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::ApplyReviewOutcome {
                pass,
                issues_found,
                clean_no_issues,
            } => Ok(Self::apply_review_outcome(
                ctx,
                pass,
                issues_found,
                clean_no_issues,
            )),
            e => self.execute_fix_agent_effect(e, ctx),
        }
    }

    fn execute_fix_agent_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PrepareFixPrompt { pass, prompt_mode } => {
                self.prepare_fix_prompt(ctx, pass, prompt_mode)
            }
            Effect::InvokeFixAgent { pass } => self.invoke_fix_agent(ctx, pass),
            Effect::InvokeFixAnalysisAgent { pass } => self.invoke_fix_analysis_agent(ctx, pass),
            Effect::ExtractFixResultXml { pass } => Ok(self.extract_fix_result_xml(ctx, pass)),
            Effect::ValidateFixResultXml { pass } => Ok(self.validate_fix_result_xml(ctx, pass)),
            Effect::ApplyFixOutcome { pass } => self.apply_fix_outcome(ctx, pass),
            Effect::ArchiveFixResultXml { pass } => Ok(self.archive_fix_result_xml(ctx, pass)),
            _ => unreachable!("execute_fix_effect called with non-fix effect"),
        }
    }

    fn execute_commit_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PrepareCommitPrompt { prompt_mode } => {
                self.prepare_commit_prompt(ctx, prompt_mode)
            }
            Effect::CheckCommitDiff => Self::check_commit_diff(ctx),
            Effect::MaterializeCommitInputs { attempt } => {
                self.materialize_commit_inputs(ctx, attempt)
            }
            Effect::InvokeCommitAgent => self.invoke_commit_agent(ctx),
            Effect::ExtractCommitXml => Ok(self.extract_commit_xml(ctx)),
            Effect::ValidateCommitXml => Ok(self.validate_commit_xml(ctx)),
            e => self.execute_commit_finalization_effect(e, ctx),
        }
    }

    fn execute_commit_finalization_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::ApplyCommitMessageOutcome => self.apply_commit_message_outcome(ctx),
            Effect::ArchiveCommitXml => Ok(self.archive_commit_xml(ctx)),
            Effect::CreateCommit {
                message,
                files,
                excluded_files,
            } => Self::create_commit(ctx, message, &files, &excluded_files),
            Effect::SkipCommit { reason } => Ok(Self::skip_commit(ctx, reason)),
            Effect::CheckResidualFiles { pass } => Self::check_residual_files(ctx, pass),
            Effect::CheckUncommittedChangesBeforeTermination => {
                Self::check_uncommitted_changes_before_termination(ctx)
            }
            _ => unreachable!("execute_commit_effect called with non-commit effect"),
        }
    }

    fn execute_rebase_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::RunRebase {
                phase,
                target_branch,
            } => self.run_rebase(ctx, phase, &target_branch),
            Effect::ResolveRebaseConflicts { strategy } => {
                Ok(Self::resolve_rebase_conflicts(ctx, strategy))
            }
            _ => unreachable!("execute_rebase_effect called with non-rebase effect"),
        }
    }

    fn execute_lifecycle_effect(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::ValidateFinalState => Ok(self.validate_final_state(ctx)),
            Effect::SaveCheckpoint { trigger } => Ok(self.save_checkpoint(ctx, trigger)),
            Effect::EnsureGitignoreEntries => Ok(Self::ensure_gitignore_entries(ctx)),
            Effect::CleanupContext => Self::cleanup_context(ctx),
            Effect::LockPromptPermissions => Ok(Self::lock_prompt_permissions(ctx)),
            Effect::RestorePromptPermissions => Ok(self.restore_prompt_permissions(ctx)),
            Effect::WriteContinuationContext(ref data) => {
                execute_write_continuation_context(ctx, data)
            }
            Effect::CleanupContinuationContext => Self::cleanup_continuation_context(ctx),
            Effect::WriteTimeoutContext {
                role,
                logfile_path,
                context_path,
            } => Self::write_timeout_context(ctx, role, &logfile_path, &context_path),
            e => self.execute_lifecycle_effect_b(e, ctx),
        }
    }

    fn execute_lifecycle_effect_b(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::TriggerLoopRecovery {
                detected_loop,
                loop_count,
            } => Ok(Self::trigger_loop_recovery(ctx, &detected_loop, loop_count)),
            Effect::EmitRecoveryReset {
                reset_type,
                target_phase,
            } => Ok(self.emit_recovery_reset(ctx, &reset_type, target_phase)),
            e => self.execute_lifecycle_effect_recovery_or_c(e, ctx),
        }
    }

    fn execute_lifecycle_effect_recovery_or_c(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::AttemptRecovery {
                level,
                attempt_count,
            } => Ok(self.attempt_recovery(ctx, level, attempt_count)),
            Effect::EmitRecoverySuccess {
                level,
                total_attempts,
            } => Ok(Self::emit_recovery_success(ctx, level, total_attempts)),
            e => self.execute_lifecycle_effect_c(e, ctx),
        }
    }

    fn execute_lifecycle_effect_c(
        &mut self,
        effect: Effect,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        match effect {
            Effect::TriggerDevFixFlow {
                failed_phase,
                failed_role,
                retry_cycle,
            } => Ok(self.trigger_dev_fix_flow(ctx, failed_phase, failed_role, retry_cycle)),
            Effect::EmitCompletionMarkerAndTerminate { is_failure, reason } => Ok(
                Self::emit_completion_marker_and_terminate(ctx, is_failure, reason),
            ),
            Effect::ConfigureGitAuth { auth_method } => {
                Ok(Self::handle_configure_git_auth(ctx, &auth_method))
            }
            e => Self::execute_lifecycle_git_effect(ctx, e),
        }
    }

    fn execute_lifecycle_git_effect(
        ctx: &mut PhaseContext<'_>,
        effect: Effect,
    ) -> Result<EffectResult> {
        match effect {
            Effect::PushToRemote {
                remote,
                branch,
                force,
                commit_sha,
            } => Ok(Self::handle_push_to_remote(
                ctx, remote, branch, force, commit_sha,
            )),
            Effect::CreatePullRequest {
                base_branch,
                head_branch,
                title,
                body,
            } => Ok(Self::handle_create_pull_request(
                ctx,
                &base_branch,
                &head_branch,
                &title,
                &body,
            )),
            _ => unreachable!("execute_lifecycle_effect called with unexpected effect"),
        }
    }
}
