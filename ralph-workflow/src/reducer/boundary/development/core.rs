//! Development phase core handler logic.
//!
//! This module handles:
//! - Context preparation (backup file creation)
//! - Agent invocation
//! - XML cleanup and archival
//! - Outcome application
//! - Continuation context writing

use super::super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::reducer::effect::{ContinuationContextData, EffectResult};
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::workspace::Workspace;
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Prepare development context.
    ///
    /// Creates backup files for large inputs (PROMPT.md.backup). This is a preparatory
    /// step that ensures file references are available before prompt generation.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentContextPrepared` event.
    pub(in crate::reducer::boundary) fn prepare_development_context(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let _ = crate::files::create_prompt_backup_with_workspace(ctx.workspace);
        EffectResult::event(PipelineEvent::development_context_prepared(iteration))
    }

    /// Invoke development agent.
    ///
    /// Normalizes agent chain state for determinism, reads the prepared prompt from
    /// `.agent/tmp/development_prompt.txt`, selects the current agent from the chain
    /// (or falls back to default developer agent), and invokes the agent.
    ///
    /// If invocation succeeds, emits an additional `DevelopmentAgentInvoked` event to
    /// track iteration-specific progress.
    ///
    /// # Agent Selection
    ///
    /// The agent is selected from the current position in the agent chain. If no chain
    /// is active, the default developer agent is used.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace and agent configuration
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `AgentEvent::InvocationSucceeded` or `AgentEvent::InvocationFailed`,
    /// plus `DevelopmentAgentInvoked` event on success.
    pub(in crate::reducer::boundary) fn invoke_development_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Development);

        let prompt = ctx
            .workspace
            .read(Path::new(".agent/tmp/development_prompt.txt"))
            .map_err(|_| ErrorEvent::DevelopmentPromptMissing { iteration })?;

        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.developer_agent.to_string());

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Development,
            AgentRole::Developer,
            &agent,
            None,
            prompt,
        )?;
        let result = if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            result.with_additional_event(PipelineEvent::development_agent_invoked(iteration))
        } else {
            result
        };
        Ok(result)
    }

    /// Archive development XML.
    ///
    /// Moves `.agent/tmp/development_result.xml` to `.agent/tmp/development_result.xml.processed`.
    /// This preserves the validated XML output for XSD retry fallback while clearing the
    /// active output path for the next attempt.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentXmlArchived` event.
    pub(in crate::reducer::boundary) fn archive_development_xml(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;

        archive_xml_file_with_workspace(
            ctx.workspace,
            Path::new(xml_paths::DEVELOPMENT_RESULT_XML),
        );
        EffectResult::event(PipelineEvent::development_xml_archived(iteration))
    }

    /// Apply development outcome.
    ///
    /// Verifies that a validated development outcome exists in state for the given iteration,
    /// then emits `DevelopmentOutcomeApplied` to signal the reducer to transition to the next
    /// phase or iteration.
    ///
    /// This is a verification step ensuring the orchestrator doesn't proceed without validated
    /// output. The actual state transition happens in the reducer.
    ///
    /// # Arguments
    ///
    /// * `_ctx` - Phase context (unused)
    /// * `iteration` - Current development iteration number
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentOutcomeApplied` event, or error if no validated outcome exists.
    pub(in crate::reducer::boundary) fn apply_development_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        self.state
            .development_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.iteration == iteration)
            .ok_or(ErrorEvent::ValidatedDevelopmentOutcomeMissing { iteration })?;

        Ok(EffectResult::event(
            PipelineEvent::development_outcome_applied(iteration),
        ))
    }
}

/// Write continuation context to workspace.
///
/// Generates a continuation context markdown file at `.agent/tmp/continuation_context.md`
/// containing the previous attempt's status, summary, files changed, and recommended next steps.
///
/// This file is included in continuation prompts to provide context about what was accomplished
/// and what remains to be done.
///
/// # Continuation Context Format
///
/// ```markdown
/// # Development Continuation Context
///
/// - Iteration: 1
/// - Continuation attempt: 2
/// - Previous status: partial
///
/// ## Previous summary
///
/// [Summary from previous attempt]
///
/// ## Files changed
///
/// - file1.rs
/// - file2.rs
///
/// ## Recommended next steps
///
/// [Next steps from previous attempt]
///
/// ## Reference files (do not modify)
///
/// - PROMPT.md
/// - .agent/PLAN.md
/// ```
///
/// # Arguments
///
/// * `workspace` - Workspace for file operations
/// * `logger` - Logger for info messages
/// * `data` - Continuation context data (iteration, attempt, status, summary, files, `next_steps`)
///
/// # Returns
///
/// Ok on success, or `ErrorEvent::WorkspaceWriteFailed` if writing fails.
pub(in crate::reducer::boundary) fn write_continuation_context_to_workspace(
    workspace: &dyn Workspace,
    logger: &crate::logger::Logger,
    data: &ContinuationContextData,
) -> Result<()> {
    let tmp_dir = Path::new(".agent/tmp");
    if !workspace.exists(tmp_dir) {
        workspace.create_dir_all(tmp_dir).map_err(|err| {
            ErrorEvent::WorkspaceCreateDirAllFailed {
                path: tmp_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }

    let content = format!(
        "# Development Continuation Context\n\n\
- Iteration: {iteration}\n\
- Continuation attempt: {attempt}\n\
- Previous status: {status}\n\n\
## Previous summary\n\n\
{summary}\n\
{files_section}\
{steps_section}\
## Reference files (do not modify)\n\n\
- PROMPT.md\n\
- .agent/PLAN.md\n",
        iteration = data.iteration,
        attempt = data.attempt,
        status = data.status,
        summary = data.summary,
        files_section = data.files_changed.as_ref().map_or(String::new(), |files| {
            let file_list = files.iter().map(|f| format!("- {f}\n")).collect::<String>();
            format!("\n## Files changed\n\n{}", file_list)
        }),
        steps_section = data.next_steps.as_ref().map_or(String::new(), |steps| {
            format!("\n## Recommended next steps\n\n{steps}\n")
        }),
    );

    workspace
        .write(Path::new(".agent/tmp/continuation_context.md"), &content)
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: ".agent/tmp/continuation_context.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;

    logger.info("Continuation context written to .agent/tmp/continuation_context.md");

    Ok(())
}
