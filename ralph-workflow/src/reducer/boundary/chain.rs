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
        let models_per_agent = resolve_drain_models(ctx, &agents);

        log_chain_info(ctx, drain, &agents, self.state.agent_chain.current_drain);

        let event = PipelineEvent::agent_chain_initialized(
            drain,
            agents,
            models_per_agent,
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
                .filter(|name| commit_drain_agent_supported(ctx, drain, name.as_str()))
                .map(|s| AgentName::from(s.clone()))
                .collect()
        })
}

fn commit_drain_agent_supported(ctx: &PhaseContext<'_>, drain: AgentDrain, name: &str) -> bool {
    if drain != AgentDrain::Commit {
        return true;
    }

    let Some(cfg) = ctx.registry.resolve_config(name) else {
        return false;
    };
    if !cfg.can_commit {
        return false;
    }
    let agent_type = crate::agents::harness::applicator::detect_agent_type(&cfg.cmd);
    let is_ccs = cfg
        .cmd
        .split_whitespace()
        .next()
        .map(|first| {
            let token = first.rsplit('/').next().unwrap_or(first);
            token.eq_ignore_ascii_case("ccs")
        })
        .unwrap_or(false);
    !matches!(
        agent_type,
        crate::agents::harness::applicator::AgentType::OpenCode
    ) && !is_ccs
}

fn resolve_drain_models(ctx: &PhaseContext<'_>, agents: &[AgentName]) -> Vec<Vec<String>> {
    let provider_fallback = &ctx.registry.resolved_drains().provider_fallback;
    agents
        .iter()
        .map(|agent| {
            let agent_str = agent.as_str();
            // Provider is the first path segment when the name contains a slash.
            // e.g., "opencode/zai/glm-4.7" → provider "opencode"
            // e.g., "claude" → no slash, no provider, no model fallback
            // Provider key is the first path segment for slash-prefixed names
            // (e.g., "opencode/zai/glm-4.7" → "opencode"), or the full name for
            // plain agent names (e.g., "opencode" → "opencode").
            // This lets provider_fallback entries match both naming conventions.
            let provider = if agent_str.contains('/') {
                agent_str.split('/').next()
            } else {
                Some(agent_str)
            };
            provider
                .and_then(|p| provider_fallback.get(p))
                .cloned()
                .unwrap_or_default()
        })
        .collect()
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

#[cfg(test)]
mod tests {
    use super::resolve_drain_models;
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::checkpoint::RunContext;
    use crate::common::domain_types::AgentName;
    use crate::config::{Config, UnifiedConfig};
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::logging::RunLogContext;
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::Path;
    use std::sync::Arc;

    /// Build a `PhaseContext` pointing to a registry configured via TOML string.
    ///
    /// The fixture handles all required context fields; callers only need to
    /// supply a registry (assembled outside the macro-like helper to keep
    /// lifetimes manageable).
    struct CtxFixture {
        config: Config,
        colors: Colors,
        logger: Logger,
        timer: Timer,
        template_context: TemplateContext,
        executor_arc: Arc<dyn crate::executor::ProcessExecutor>,
        workspace: MemoryWorkspace,
        workspace_arc: Arc<dyn crate::workspace::Workspace>,
        run_log_context: RunLogContext,
        git_env: crate::runtime::environment::mock::MockGitEnvironment,
    }

    impl CtxFixture {
        fn new() -> Self {
            let colors = Colors { enabled: false };
            let executor_arc =
                Arc::new(MockProcessExecutor::new()) as Arc<dyn crate::executor::ProcessExecutor>;
            let workspace = MemoryWorkspace::new(Path::new("/test").to_path_buf());
            let workspace_arc = Arc::new(workspace.clone()) as Arc<dyn crate::workspace::Workspace>;
            let run_log_context = RunLogContext::new(&workspace).expect("run log context");
            Self {
                config: Config::default(),
                colors,
                logger: Logger::new(colors),
                timer: Timer::new(),
                template_context: TemplateContext::default(),
                executor_arc,
                workspace,
                workspace_arc,
                run_log_context,
                git_env: crate::runtime::environment::mock::MockGitEnvironment::new(),
            }
        }

        fn make_ctx<'a>(&'a mut self, registry: &'a AgentRegistry) -> PhaseContext<'a> {
            PhaseContext {
                config: &self.config,
                registry,
                logger: &self.logger,
                colors: &self.colors,
                timer: &mut self.timer,
                developer_agent: "codex",
                reviewer_agent: "codex",
                review_guidelines: None,
                template_context: &self.template_context,
                run_context: RunContext::new(),
                execution_history: ExecutionHistory::new(),
                executor: self.executor_arc.as_ref(),
                executor_arc: Arc::clone(&self.executor_arc),
                repo_root: Path::new("/test"),
                workspace: &self.workspace,
                workspace_arc: Arc::clone(&self.workspace_arc),
                run_log_context: &self.run_log_context,
                cloud_reporter: None,
                cloud: &self.config.cloud,
                env: &self.git_env,
                active_session: None,
                audit_trail: crate::agents::session::AuditTrail::new(),
            }
        }
    }

    fn registry_with_provider_fallback(toml: &str) -> AgentRegistry {
        let unified: UnifiedConfig = toml::from_str(toml).expect("valid toml");
        AgentRegistry::new()
            .expect("registry")
            .apply_unified_config(&unified)
            .expect("apply config")
    }

    // -----------------------------------------------------------------------
    // resolve_drain_models — boundary unit tests
    // -----------------------------------------------------------------------

    /// A slash-prefixed agent whose provider key IS present in `provider_fallback`
    /// must return the configured model list.
    #[test]
    fn test_resolve_drain_models_slash_prefixed_with_configured_provider() {
        let registry = registry_with_provider_fallback(
            r#"
[agent_chains]
dev = ["opencode/zai/glm-4.7"]

[agent_drains]
planning = "dev"
development = "dev"
analysis = "dev"
review = "dev"
fix = "dev"
commit = "dev"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
"#,
        );

        let mut fixture = CtxFixture::new();
        let ctx = fixture.make_ctx(&registry);

        let agents = vec![AgentName::from("opencode/zai/glm-4.7")];
        let models = resolve_drain_models(&ctx, &agents);

        assert_eq!(models.len(), 1, "one entry per agent");
        assert_eq!(
            models[0],
            vec![
                "-m opencode/glm-4.7-free".to_string(),
                "-m opencode/claude-sonnet-4".to_string()
            ],
            "slash-prefixed agent must resolve model list from provider_fallback"
        );
    }

    /// A non-slash-prefixed agent (e.g. `claude`) must produce an empty model list
    /// because there is no provider segment to look up.
    #[test]
    fn test_resolve_drain_models_non_slashed_agent_returns_empty() {
        let registry = registry_with_provider_fallback(
            r#"
[agent_chains]
dev = ["claude"]

[agent_drains]
planning = "dev"
development = "dev"
analysis = "dev"
review = "dev"
fix = "dev"
commit = "dev"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free"]
"#,
        );

        let mut fixture = CtxFixture::new();
        let ctx = fixture.make_ctx(&registry);

        let agents = vec![AgentName::from("claude")];
        let models = resolve_drain_models(&ctx, &agents);

        assert_eq!(models.len(), 1, "one entry per agent");
        assert!(
            models[0].is_empty(),
            "non-slash-prefixed agent must return an empty model list"
        );
    }

    /// A slash-prefixed agent whose provider key is NOT present in `provider_fallback`
    /// must produce an empty model list rather than panic or error.
    #[test]
    fn test_resolve_drain_models_slashed_agent_missing_provider_key_returns_empty() {
        let registry = registry_with_provider_fallback(
            r#"
[agent_chains]
dev = ["zai/glm-4.7"]

[agent_drains]
planning = "dev"
development = "dev"
analysis = "dev"
review = "dev"
fix = "dev"
commit = "dev"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free"]
"#,
        );

        let mut fixture = CtxFixture::new();
        let ctx = fixture.make_ctx(&registry);

        let agents = vec![AgentName::from("zai/glm-4.7")];
        let models = resolve_drain_models(&ctx, &agents);

        assert_eq!(models.len(), 1, "one entry per agent");
        assert!(
            models[0].is_empty(),
            "slashed agent with no matching provider key must return an empty model list"
        );
    }

    /// A plain (non-slash-prefixed) agent name whose full name IS present in `provider_fallback`
    /// must return the configured model list.
    ///
    /// Regression test: previously the `else { None }` branch unconditionally returned no provider,
    /// so "opencode" (no slash) would silently get an empty model list even when
    /// `provider_fallback.opencode` was configured.
    #[test]
    fn test_resolve_drain_models_plain_agent_name_matches_provider_key() {
        let registry = registry_with_provider_fallback(
            r#"
[agent_chains]
dev = ["opencode"]

[agent_drains]
planning = "dev"
development = "dev"
analysis = "dev"
review = "dev"
fix = "dev"
commit = "dev"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
"#,
        );

        let mut fixture = CtxFixture::new();
        let ctx = fixture.make_ctx(&registry);

        let agents = vec![AgentName::from("opencode")];
        let models = resolve_drain_models(&ctx, &agents);

        assert_eq!(models.len(), 1, "one entry per agent");
        assert_eq!(
            models[0],
            vec![
                "-m opencode/glm-4.7-free".to_string(),
                "-m opencode/claude-sonnet-4".to_string()
            ],
            "plain 'opencode' agent must resolve model list from provider_fallback['opencode']"
        );
    }

    /// Mixed agent list: one with a configured provider and one without.
    /// Verifies that the parallel indexing matches the input agents slice.
    #[test]
    fn test_resolve_drain_models_mixed_agents_preserves_order() {
        let registry = registry_with_provider_fallback(
            r#"
[agent_chains]
dev = ["opencode/zai/glm-4.7", "claude", "zai/glm-4.7"]

[agent_drains]
planning = "dev"
development = "dev"
analysis = "dev"
review = "dev"
fix = "dev"
commit = "dev"

[general.provider_fallback]
opencode = ["-m opencode/glm-4.7-free", "-m opencode/claude-sonnet-4"]
"#,
        );

        let mut fixture = CtxFixture::new();
        let ctx = fixture.make_ctx(&registry);

        let agents = vec![
            AgentName::from("opencode/zai/glm-4.7"),
            AgentName::from("claude"),
            AgentName::from("zai/glm-4.7"),
        ];
        let models = resolve_drain_models(&ctx, &agents);

        assert_eq!(models.len(), 3);
        assert_eq!(
            models[0],
            vec![
                "-m opencode/glm-4.7-free".to_string(),
                "-m opencode/claude-sonnet-4".to_string()
            ],
            "index 0 (opencode/*) must have provider models"
        );
        assert!(models[1].is_empty(), "index 1 (claude) must have no models");
        assert!(
            models[2].is_empty(),
            "index 2 (zai/*) has no provider key in fallback → empty"
        );
    }
}
