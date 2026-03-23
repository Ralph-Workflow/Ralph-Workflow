use super::MainEffectHandler;
use crate::agents::AgentDrain;
use crate::common::domain_types::AgentName;
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
        let agents = resolve_drain_agents(ctx, drain);

        log_chain_info(ctx, drain, &agents, self.state.agent_chain.current_drain);

        let event = PipelineEvent::agent_chain_initialized(
            drain,
            agents,
            resolved_drains.max_cycles,
            resolved_drains.retry_delay_ms,
            resolved_drains.backoff_multiplier,
            resolved_drains.max_backoff_ms,
        );

        let ui_events = chain_phase_transition_ui_events(self, drain);

        EffectResult::with_ui(event, ui_events)
    }
}

fn resolve_drain_agents(ctx: &PhaseContext<'_>, drain: AgentDrain) -> Vec<AgentName> {
    ctx.registry
        .resolved_drain(drain)
        .map_or_else(Vec::new, |binding| {
            binding
                .agents
                .iter()
                .map(|s| AgentName::from(s.clone()))
                .collect()
        })
}

fn log_chain_info(
    ctx: &PhaseContext<'_>,
    drain: AgentDrain,
    agents: &[AgentName],
    current_drain: AgentDrain,
) {
    ctx.logger.info(&format!(
        "Agent fallback chain for drain {drain}: {}",
        agents
            .iter()
            .map(|a| a.to_string())
            .collect::<Vec<_>>()
            .join(", ")
    ));
    if drain != current_drain {
        ctx.logger.info(&format!("🔄 Switching to {drain} drain"));
    }
}

fn chain_phase_transition_ui_events(
    handler: &MainEffectHandler,
    drain: AgentDrain,
) -> Vec<UIEvent> {
    match drain {
        AgentDrain::Planning if handler.state.phase == PipelinePhase::Planning => {
            vec![UIEvent::PhaseTransition {
                from: None,
                to: PipelinePhase::Planning,
            }]
        }
        AgentDrain::Review if handler.state.phase == PipelinePhase::Review => {
            vec![handler.phase_transition_ui(PipelinePhase::Review)]
        }
        _ => vec![],
    }
}
