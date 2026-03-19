//! Commit agent invocation.
//!
//! This module handles invoking the LLM agent for commit message generation.
//! It follows the reducer architecture pattern:
//! - Single invocation attempt per effect
//! - Returns events describing the outcome
//! - Uses workspace abstraction for file I/O
//!
//! ## Process
//!
//! 1. Normalize agent chain state for determinism
//! 2. Read commit prompt from `.agent/tmp/commit_prompt.txt`
//! 3. Get current agent from agent chain
//! 4. Invoke agent via `invoke_agent` helper
//! 5. Emit `commit_agent_invoked` event on success

use super::super::MainEffectHandler;
use super::current_commit_attempt;
use crate::agents::AgentRole;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::AgentEvent;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Invoke commit message generation agent.
    ///
    /// Reads commit prompt and invokes the current agent from the agent chain.
    ///
    /// # Events Emitted
    ///
    /// - `commit_agent_invoked` - Agent invocation completed successfully
    /// - Plus events from `invoke_agent` (InvocationSucceeded/Failed)
    ///
    /// # Errors
    ///
    /// - `CommitPromptMissing` - Prompt file not found
    /// - `WorkspaceReadFailed` - Error reading prompt file
    /// - `CommitAgentNotInitialized` - Agent chain not initialized
    pub(in crate::reducer::handler) fn invoke_commit_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Commit);

        let attempt = current_commit_attempt(&self.state.commit);
        let prompt = match ctx
            .workspace
            .read(Path::new(".agent/tmp/commit_prompt.txt"))
        {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::CommitPromptMissing { attempt }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/commit_prompt.txt".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .ok_or(ErrorEvent::CommitAgentNotInitialized { attempt })?;

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Commit,
            AgentRole::Commit,
            &agent,
            None,
            prompt,
        )?;
        let result = result
            .additional_events
            .iter()
            .any(|e| {
                matches!(
                    e,
                    PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
                )
            })
            .then(|| {
                result
                    .clone()
                    .with_additional_event(PipelineEvent::commit_agent_invoked(attempt))
            })
            .unwrap_or(result);
        Ok(result)
    }
}
