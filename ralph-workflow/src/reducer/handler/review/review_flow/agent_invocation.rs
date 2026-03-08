// Review phase agent invocation.
//
// Contains the invoke_review_agent method for invoking the reviewer agent.

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn invoke_review_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use std::path::Path;

        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, AgentRole::Reviewer);

        let prompt = match ctx
            .workspace
            .read(Path::new(".agent/tmp/review_prompt.txt"))
        {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::ReviewPromptMissing { pass }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/review_prompt.txt".to_string(),
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
            .unwrap_or_else(|| ctx.reviewer_agent.to_string());

        let mut result = self.invoke_agent(ctx, AgentRole::Reviewer, &agent, None, prompt)?;
        if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            result = result.with_additional_event(PipelineEvent::review_agent_invoked(pass));
        }
        Ok(result)
    }
}
