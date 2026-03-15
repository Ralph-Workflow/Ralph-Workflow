use super::MainEffectHandler;
use crate::agents::AgentDrain;
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
        let resolved_drains = ctx.registry.resolved_drains();

        // Resolve the concrete chain for this drain.
        let agents = ctx
            .registry
            .resolved_drain(drain)
            .map_or_else(Vec::new, |binding| binding.agents.clone());

        ctx.logger.info(&format!(
            "Agent fallback chain for drain {drain}: {}",
            agents.join(", ")
        ));

        // Log drain transition when switching to a different drain
        let current_drain = self.state.agent_chain.current_drain;
        if drain != current_drain {
            ctx.logger.info(&format!("🔄 Switching to {drain} drain"));
        }

        let event = PipelineEvent::agent_chain_initialized(
            drain,
            agents,
            resolved_drains.max_cycles,
            resolved_drains.retry_delay_ms,
            resolved_drains.backoff_multiplier,
            resolved_drains.max_backoff_ms,
        );

        // Emit phase transition when entering a new major phase
        let ui_events = match drain {
            AgentDrain::Planning if self.state.phase == PipelinePhase::Planning => {
                vec![UIEvent::PhaseTransition {
                    from: None,
                    to: PipelinePhase::Planning,
                }]
            }
            AgentDrain::Review if self.state.phase == PipelinePhase::Review => {
                vec![self.phase_transition_ui(PipelinePhase::Review)]
            }
            _ => vec![],
        };

        EffectResult::with_ui(event, ui_events)
    }
}
