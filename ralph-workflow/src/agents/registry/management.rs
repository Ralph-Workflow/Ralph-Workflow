// Registry management and lookup operations.
// Includes the AgentRegistry struct definition and core lookup/management methods.

/// Typed error for agent chain validation failures.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentChainValidationError {
    /// No agent chain configured at all (no drain has any binding with agents).
    NoChainConfigured { searched_sources: String },
    /// A specific drain has no binding.
    NoDrainBinding { drain: String, searched_sources: String },
    /// A specific drain's bound chain is empty.
    EmptyDrainChain { drain: String, searched_sources: String },
    /// All agents in a drain have `can_commit=false`.
    NoWorkflowCapableAgents { drain: String },
}

impl std::fmt::Display for AgentChainValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoChainConfigured { searched_sources } => write!(
                f,
                "No agent chain configured. \
                Searched: {searched_sources}.\n\
                Please add [agent_chains] and [agent_drains] sections to your config.\n\
                Legacy [agent_chain] input is deprecated but still accepted with default drain bindings.\n\
                Run 'ralph --init-global' to create a default configuration."
            ),
            Self::NoDrainBinding { drain, searched_sources } => write!(
                f,
                "No {drain} agent chain configured. \
                Searched: {searched_sources}.\n\
                Bind the {drain} drain in [agent_drains] to a chain from [agent_chains].\n\
                Use --list-agents to see available agents."
            ),
            Self::EmptyDrainChain { drain, searched_sources } => write!(
                f,
                "No {drain} agent chain configured. \
                Searched: {searched_sources}.\n\
                Bind the {drain} drain in [agent_drains] to a non-empty chain from [agent_chains].\n\
                Use --list-agents to see available agents."
            ),
            Self::NoWorkflowCapableAgents { drain } => write!(
                f,
                "No workflow-capable agents found for {drain}.\n\
                All agents in the {drain} drain binding have can_commit=false.\n\
                Fix: set can_commit=true for at least one agent or update [agent_chains]/[agent_drains]."
            ),
        }
    }
}

impl std::error::Error for AgentChainValidationError {}

/// Agent registry with CCS alias and `OpenCode` dynamic provider/model support.
///
/// CCS aliases are eagerly resolved and registered as regular agents
/// when set via `set_ccs_aliases()`. This allows `get()` to work
/// uniformly for both regular agents and CCS aliases.
///
/// `OpenCode` provider/model combinations are resolved on-the-fly using
/// the `opencode/` prefix.
#[derive(Debug)]
pub struct AgentRegistry {
    agents: HashMap<String, AgentConfig>,
    resolved_drains: crate::agents::fallback::ResolvedDrainConfig,
    /// CCS alias resolver for `ccs/alias` syntax.
    ccs_resolver: CcsAliasResolver,
    /// `OpenCode` resolver for `opencode/provider/model` syntax.
    opencode_resolver: Option<OpenCodeResolver>,
    /// Retry timer provider for controlling sleep behavior in retry logic.
    retry_timer: Arc<dyn RetryTimerProviderDebug>,
}

impl AgentRegistry {
    /// Create a new registry with default agents.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new() -> Result<Self, AgentConfigError> {
        let config: AgentsConfigFile =
            toml::from_str(DEFAULT_AGENTS_TOML).map_err(AgentConfigError::DefaultTemplateToml)?;
        let resolved_drains = config.resolve_drains_checked()?.unwrap_or_else(|| {
            crate::agents::fallback::ResolvedDrainConfig::from_legacy(&FallbackConfig::default())
        });
        let agents = config.agents;

        // Create agents map functionally
        let agents: HashMap<_, _> = agents
            .into_iter()
            .map(|(name, agent_toml)| (name, AgentConfig::from(agent_toml)))
            .collect();

        Ok(Self {
            agents,
            resolved_drains,
            ccs_resolver: CcsAliasResolver::empty(),
            opencode_resolver: None,
            retry_timer: production_timer(),
        })
    }

    /// Set the `OpenCode` API catalog for dynamic provider/model resolution.
    ///
    /// This enables resolution of `opencode/provider/model` agent references.
    pub fn set_opencode_catalog(&mut self, catalog: ApiCatalog) {
        self.opencode_resolver = Some(OpenCodeResolver::new(catalog));
    }

    /// Set CCS aliases for the registry.
    ///
    /// This eagerly registers CCS aliases as agents so they can be
    /// resolved with `resolve_config()`.
    pub fn set_ccs_aliases(
        self,
        aliases: &HashMap<String, CcsAliasConfig>,
        defaults: CcsConfig,
    ) -> Self {
        let ccs_resolver = CcsAliasResolver::new(aliases.clone(), defaults);
        // Compute all agents to insert - functional style with chain
        let new_agents: HashMap<_, _> = aliases
            .keys()
            .filter_map(|alias_name| {
                let agent_name = format!("ccs/{alias_name}");
                ccs_resolver
                    .try_resolve(&agent_name)
                    .map(|config| (agent_name, config))
            })
            .collect();
        // Rebuild with chain - functional style
        let agents = self.agents.into_iter().chain(new_agents).collect();
        Self {
            agents,
            resolved_drains: self.resolved_drains,
            ccs_resolver,
            opencode_resolver: self.opencode_resolver,
            retry_timer: self.retry_timer,
        }
    }

    /// Register a new agent.
    pub fn register(self, name: &str, config: AgentConfig) -> Self {
        // Rebuild with chain - functional style instead of mutating insert
        let agents = self
            .agents
            .into_iter()
            .chain(std::iter::once((name.to_string(), config)))
            .collect();
        Self {
            agents,
            resolved_drains: self.resolved_drains,
            ccs_resolver: self.ccs_resolver,
            opencode_resolver: self.opencode_resolver,
            retry_timer: self.retry_timer,
        }
    }

    /// Create a registry with only built-in agents (no config file loading).
    ///
    /// This is useful for integration tests that need a minimal registry
    /// without loading from config files or environment variables.
    ///
    /// # Test-Utils Only
    ///
    /// # Panics
    ///
    /// Panics if invariants are violated.
    ///
    /// This function is only available when the `test-utils` feature is enabled.
    #[cfg(feature = "test-utils")]
    #[must_use]
    #[expect(
        clippy::expect_used,
        reason = "built-in agents are hardcoded and always valid"
    )]
    pub fn with_builtins_only() -> Self {
        Self::new().expect("Built-in agents should always be valid")
    }

    /// Resolve an agent's configuration, including on-the-fly CCS and `OpenCode` references.
    ///
    /// CCS supports direct execution via `ccs/<alias>` even when the alias isn't
    /// pre-registered in config; those are resolved lazily here.
    ///
    /// `OpenCode` supports dynamic provider/model via `opencode/provider/model` syntax;
    /// those are validated against the API catalog and resolved lazily here.
    #[must_use]
    pub fn resolve_config(&self, name: &str) -> Option<AgentConfig> {
        self.agents
            .get(name)
            .cloned()
            .or_else(|| self.ccs_resolver.try_resolve(name))
            .or_else(|| {
                self.opencode_resolver
                    .as_ref()
                    .and_then(|r| r.try_resolve(name))
            })
    }

    /// Get display name for an agent.
    ///
    /// Returns the agent's custom display name if set (e.g., "ccs-glm" for CCS aliases),
    /// otherwise returns the agent's registry name.
    ///
    /// # Arguments
    ///
    /// * `name` - The agent's registry name (e.g., "ccs/glm", "claude")
    ///
    /// # Examples
    ///
    /// ```ignore
    /// assert_eq!(registry.display_name("ccs/glm"), "ccs-glm");
    /// assert_eq!(registry.display_name("claude"), "claude");
    /// ```
    #[must_use]
    pub fn display_name(&self, name: &str) -> String {
        self.resolve_config(name)
            .and_then(|config| config.display_name)
            .unwrap_or_else(|| name.to_string())
    }

    /// Find the registry name for an agent given its log file name.
    ///
    /// Log file names use a sanitized form of the registry name where `/` is
    /// replaced with `-` to avoid creating subdirectories. This function
    /// reverses that sanitization to find the original registry name.
    ///
    /// This is used for session continuation, where the agent name is extracted
    /// from log file names (e.g., "ccs-glm", "opencode-anthropic-claude-sonnet-4")
    /// but we need to look up the agent in the registry (which uses names like
    /// "ccs/glm", "opencode/anthropic/claude-sonnet-4").
    ///
    /// # Strategy
    ///
    /// 1. Check if the name is already a valid registry key (no sanitization needed)
    /// 2. Search registered agents for one whose sanitized name matches
    /// 3. Try common patterns like "ccs-X" → "ccs/X", "opencode-X-Y" → "opencode/X/Y"
    ///
    /// # Arguments
    ///
    /// * `logfile_name` - The agent name extracted from a log file (e.g., "ccs-glm")
    ///
    /// # Returns
    ///
    /// The registry name if found (e.g., "ccs/glm"), or `None` if no match.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// assert_eq!(registry.resolve_from_logfile_name("ccs-glm"), Some("ccs/glm".to_string()));
    /// assert_eq!(registry.resolve_from_logfile_name("claude"), Some("claude".to_string()));
    /// assert_eq!(registry.resolve_from_logfile_name("opencode-anthropic-claude-sonnet-4"),
    ///            Some("opencode/anthropic/claude-sonnet-4".to_string()));
    /// ```
    #[must_use]
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "bounds-checked index arithmetic"
    )]
    pub fn resolve_from_logfile_name(&self, logfile_name: &str) -> Option<String> {
        // First check if the name is exactly a registry name (no sanitization was needed)
        if self.agents.contains_key(logfile_name) {
            return Some(logfile_name.to_string());
        }

        // Search registered agents for one whose sanitized name matches - use iterator
        let match_result = self
            .agents
            .keys()
            .find(|name| name.replace('/', "-") == logfile_name);
        if let Some(name) = match_result {
            return Some(name.clone());
        }

        // Try to resolve dynamically for unregistered agents
        // CCS pattern: "ccs-alias" → "ccs/alias"
        if let Some(alias) = logfile_name.strip_prefix("ccs-") {
            let registry_name = format!("ccs/{alias}");
            // CCS agents can be resolved dynamically even if not pre-registered
            return Some(registry_name);
        }

        // OpenCode pattern: "opencode-provider-model" → "opencode/provider/model"
        // Note: This is a best-effort heuristic for log file name parsing.
        // Provider names may contain hyphens (e.g., "zai-coding-plan"), making it
        // impossible to reliably split "opencode-zai-coding-plan-glm-4.7".
        // The preferred approach is to pass the original agent name through
        // SessionInfo rather than relying on log file name parsing.
        if let Some(rest) = logfile_name.strip_prefix("opencode-") {
            if let Some(first_hyphen) = rest.find('-') {
                let provider = &rest[..first_hyphen];
                let model = &rest[first_hyphen + 1..];
                let registry_name = format!("opencode/{provider}/{model}");
                return Some(registry_name);
            }
        }

        // No match found
        None
    }

    /// Resolve a fuzzy agent name to a canonical agent name.
    ///
    /// This handles common typos and alternative forms:
    /// - `ccs/<unregistered>`: Returns the name as-is for direct CCS execution
    /// - `opencode/provider/model`: Returns the name as-is for dynamic resolution
    /// - Other fuzzy matches: Returns the canonical name if a match is found
    /// - Exact matches: Returns the name as-is
    ///
    /// Returns `None` if the name cannot be resolved to any known agent.
    #[must_use]
    pub fn resolve_fuzzy(&self, name: &str) -> Option<String> {
        // First check if it's an exact match
        if self.agents.contains_key(name) {
            return Some(name.to_string());
        }

        // Handle ccs/<unregistered> pattern - return as-is for direct CCS execution
        if name.starts_with("ccs/") {
            return Some(name.to_string());
        }

        // Handle opencode/provider/model pattern - return as-is for dynamic resolution
        if name.starts_with("opencode/") {
            // Validate that it has the right format (opencode/provider/model)
            let parts: Vec<&str> = name.split('/').collect();
            if parts.len() == 3 && parts.first().is_some_and(|p| *p == "opencode") {
                return Some(name.to_string());
            }
        }

        // Handle common typos/alternatives
        let normalized = name.to_lowercase();
        let alternatives = Self::get_fuzzy_alternatives(&normalized);

        // Find first matching alternative
        alternatives.into_iter().find_map(|alt| {
            // If it's a ccs/ pattern, return it for direct CCS execution
            if alt.starts_with("ccs/") {
                return Some(alt);
            }
            // If it's an opencode/ pattern, validate the format
            if alt.starts_with("opencode/") {
                let parts: Vec<&str> = alt.split('/').collect();
                if parts.len() == 3 && parts.first().is_some_and(|p| *p == "opencode") {
                    return Some(alt);
                }
            }
            // Otherwise check if it exists in the registry
            if self.agents.contains_key(&alt) {
                return Some(alt);
            }
            None
        })
    }

    /// Get fuzzy alternatives for a given agent name.
    ///
    /// Returns a list of potential canonical names to try, in order of preference.
    pub(crate) fn get_fuzzy_alternatives(name: &str) -> Vec<String> {
        // Start with exact match first
        let alternatives = std::iter::once(name.to_string());

        // Handle common typos and variations
        let variations: Vec<String> = match name {
            // ccs variations
            n if n.starts_with("ccs-") => vec![name.replace("ccs-", "ccs/")],
            n if n.contains('_') => vec![name.replace('_', "-"), name.replace('_', "/")],

            // claude variations
            "claud" | "cloud" => vec!["claude".to_string()],

            // codex variations
            "codeex" | "code-x" => vec!["codex".to_string()],

            // cursor variations
            "crusor" => vec!["cursor".to_string()],

            // opencode variations
            "opencode" | "open-code" => vec!["opencode".to_string()],

            // gemini variations
            "gemeni" | "gemni" => vec!["gemini".to_string()],

            // qwen variations
            "quen" | "quwen" => vec!["qwen".to_string()],

            // aider variations
            "ader" => vec!["aider".to_string()],

            // vibe variations
            "vib" => vec!["vibe".to_string()],

            // cline variations
            "kline" => vec!["cline".to_string()],

            _ => vec![],
        };

        alternatives.chain(variations).collect()
    }

    /// List all registered agents.
    #[must_use]
    pub fn list(&self) -> Vec<(&str, &AgentConfig)> {
        self.agents.iter().map(|(k, v)| (k.as_str(), v)).collect()
    }

    /// Get command for developer role.
    #[must_use]
    pub fn developer_cmd(&self, agent_name: &str) -> Option<String> {
        self.resolve_config(agent_name)
            .map(|c| c.build_cmd(true, true, true))
    }

    /// Get command for reviewer role.
    #[must_use]
    pub fn reviewer_cmd(&self, agent_name: &str) -> Option<String> {
        self.resolve_config(agent_name)
            .map(|c| c.build_cmd(true, true, false))
    }

    #[must_use]
    pub fn resolved_drain(
        &self,
        drain: crate::agents::AgentDrain,
    ) -> Option<&crate::agents::fallback::ResolvedDrainBinding> {
        self.resolved_drains.binding(drain)
    }

    #[must_use]
    pub const fn resolved_drains(&self) -> &crate::agents::fallback::ResolvedDrainConfig {
        &self.resolved_drains
    }

    /// Get a compatibility projection of the resolved drain bindings.
    ///
    /// Runtime code should prefer `resolved_drains()` / `resolved_drain()`.
    #[must_use]
    pub fn fallback_config(&self) -> FallbackConfig {
        self.resolved_drains.to_legacy_fallback()
    }

    /// Get the retry timer provider.
    #[must_use]
    pub fn retry_timer(&self) -> Arc<dyn RetryTimerProvider> {
        Arc::clone(&self.retry_timer) as Arc<dyn RetryTimerProvider>
    }

    /// Set the retry timer provider (for testing purposes).
    ///
    /// This is used to inject a test timer that doesn't actually sleep,
    /// enabling fast test execution without waiting for retry delays.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn set_retry_timer(&mut self, timer: Arc<dyn RetryTimerProviderDebug>) {
        self.retry_timer = timer;
    }

    /// Get all fallback agents for a role that are registered in this registry.
    #[must_use]
    pub fn available_fallbacks(&self, role: AgentRole) -> Vec<&str> {
        self.available_fallbacks_for_drain(crate::agents::AgentDrain::from(role))
    }

    /// Get all fallback agents for a drain that are registered in this registry.
    #[must_use]
    pub fn available_fallbacks_for_drain(&self, drain: crate::agents::AgentDrain) -> Vec<&str> {
        self.resolved_drain(drain)
            .map_or(&[][..], |binding| binding.agents.as_slice())
            .iter()
            .filter(|name| self.is_agent_available(name))
            // Agents with can_commit=false are chat-only / non-tool agents and will stall Ralph.
            .filter(|name| {
                self.resolve_config(name.as_str())
                    .is_some_and(|cfg| cfg.can_commit)
            })
            .map(std::string::String::as_str)
            .collect()
    }

    /// Validate that every built-in runtime drain has workflow-capable coverage.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn validate_agent_chains(&self, searched_sources: &str) -> Result<(), AgentChainValidationError> {
        let drain_bindings: Vec<_> = crate::agents::AgentDrain::all()
            .into_iter()
            .map(|drain| (drain, self.resolved_drain(drain)))
            .collect();

        let has_any_binding = drain_bindings
            .iter()
            .any(|(_, binding)| binding.is_some_and(|binding| !binding.agents.is_empty()));

        if !has_any_binding {
            return Err(AgentChainValidationError::NoChainConfigured {
                searched_sources: searched_sources.to_string(),
            });
        }

        // Validate each drain - fail on first error
        drain_bindings
            .iter()
            .try_fold((), |(), (drain, binding)| {
                let binding = binding.ok_or_else(|| {
                    AgentChainValidationError::NoDrainBinding {
                        drain: drain.to_string(),
                        searched_sources: searched_sources.to_string(),
                    }
                })?;

                if binding.agents.is_empty() {
                    return Err(AgentChainValidationError::EmptyDrainChain {
                        drain: drain.to_string(),
                        searched_sources: searched_sources.to_string(),
                    });
                }

                let has_capable = binding
                    .agents
                    .iter()
                    .any(|name| self.resolve_config(name).is_some_and(|cfg| cfg.can_commit));
                if !has_capable {
                    return Err(AgentChainValidationError::NoWorkflowCapableAgents {
                        drain: drain.to_string(),
                    });
                }

                Ok(())
            })
    }

    /// Check if an agent is available (command exists and is executable).
    #[must_use]
    pub fn is_agent_available(&self, name: &str) -> bool {
        if let Some(config) = self.resolve_config(name) {
            let Ok(parts) = crate::common::split_command(&config.cmd) else {
                return false;
            };
            let Some(base_cmd) = parts.first() else {
                return false;
            };

            // Check if the command exists in PATH
            which::which(base_cmd).is_ok()
        } else {
            false
        }
    }

    /// List all available (installed) agents.
    pub fn list_available(&self) -> Vec<&str> {
        self.agents
            .keys()
            .filter(|name| self.is_agent_available(name))
            .map(std::string::String::as_str)
            .collect()
    }
}
