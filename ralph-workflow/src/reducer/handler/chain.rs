use super::MainEffectHandler;
use crate::agents::{AgentDrain, AgentRole};
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::ui_event::UIEvent;
impl MainEffectHandler {
    pub(super) fn initialize_agent_chain(
        &self,
        ctx: &PhaseContext<'_>,
        drain: AgentDrain,
    ) -> EffectResult {
        let role = drain.role();
        let fallback_config = ctx.registry.fallback_config();

        // Resolve the concrete chain for this drain.
        let agents = ctx
            .registry
            .resolved_drain(drain)
            .map_or_else(Vec::new, |binding| binding.agents.clone());

        ctx.logger.info(&format!(
            "Agent fallback chain for drain {drain}: {}",
            agents.join(", ")
        ));

        let event = PipelineEvent::agent_chain_initialized(
            drain,
            agents,
            fallback_config.max_cycles,
            fallback_config.retry_delay_ms,
            fallback_config.backoff_multiplier,
            fallback_config.max_backoff_ms,
        );

        // Emit phase transition when entering a new major phase
        let ui_events = match role {
            AgentRole::Developer if self.state.phase == PipelinePhase::Planning => {
                vec![UIEvent::PhaseTransition {
                    from: None,
                    to: PipelinePhase::Planning,
                }]
            }
            AgentRole::Reviewer if self.state.phase == PipelinePhase::Review => {
                vec![self.phase_transition_ui(PipelinePhase::Review)]
            }
            _ => vec![],
        };

        EffectResult::with_ui(event, ui_events)
    }
}
