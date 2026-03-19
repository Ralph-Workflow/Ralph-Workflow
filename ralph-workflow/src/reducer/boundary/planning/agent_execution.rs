//! Planning agent execution.
//!
//! Handles the invocation of planning agents and cleanup of XML artifacts.

use super::super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use anyhow::Result;
use std::path::Path;

const PLANNING_PROMPT_PATH: &str = ".agent/tmp/planning_prompt.txt";

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn invoke_planning_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Planning);

        let prompt = match ctx.workspace.read(Path::new(PLANNING_PROMPT_PATH)) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::PlanningPromptMissing { iteration }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: PLANNING_PROMPT_PATH.to_string(),
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
            .unwrap_or_else(|| ctx.developer_agent.to_string());

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Planning,
            AgentRole::Developer,
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
                    .with_additional_event(PipelineEvent::planning_agent_invoked(iteration))
            })
            .unwrap_or(result);
        Ok(result)
    }
}
