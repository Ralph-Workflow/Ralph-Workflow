// Registry management and lookup operations.
// Includes the AgentRegistry struct definition and core lookup/management methods.

/// Agent registry with CCS alias and `OpenCode` dynamic provider/model support.
///
/// CCS aliases are eagerly resolved and registered as regular agents
/// when set via `set_ccs_aliases()`. This allows `get()` to work
/// uniformly for both regular agents and CCS aliases.
///
/// `OpenCode` provider/model combinations are resolved on-the-fly using
/// the `opencode/` prefix.
pub struct AgentRegistry {
    agents: HashMap<String, AgentConfig>,
    fallback: FallbackConfig,
    resolved_drains: crate::agents::fallback::ResolvedDrainConfig,
    /// CCS alias resolver for `ccs/alias` syntax.
    ccs_resolver: CcsAliasResolver,
    /// `OpenCode` resolver for `opencode/provider/model` syntax.
    opencode_resolver: Option<OpenCodeResolver>,
    /// Retry timer provider for controlling sleep behavior in retry logic.
    retry_timer: Arc<dyn RetryTimerProvider>,
}

impl AgentRegistry {
    /// Create a new registry with default agents.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new() -> Result<Self, AgentConfigError> {
        let AgentsConfigFile { agents, fallback } =
            toml::from_str(DEFAULT_AGENTS_TOML).map_err(AgentConfigError::DefaultTemplateToml)?;

        let mut registry = Self {
            agents: HashMap::new(),
            fallback: fallback.clone(),
            resolved_drains: crate::agents::fallback::ResolvedDrainConfig::from_legacy(&fallback),
            ccs_resolver: CcsAliasResolver::empty(),
            opencode_resolver: None,
            retry_timer: production_timer(),
        };

        for (name, agent_toml) in agents {
            registry.register(&name, AgentConfig::from(agent_toml));
        }

        Ok(registry)
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
        &mut self,
        aliases: &HashMap<String, CcsAliasConfig>,
        defaults: CcsConfig,
    ) {
        self.ccs_resolver = CcsAliasResolver::new(aliases.clone(), defaults);
        // Eagerly register CCS aliases as agents
        for alias_name in aliases.keys() {
            let agent_name = format!("ccs/{alias_name}");
            if let Some(config) = self.ccs_resolver.try_resolve(&agent_name) {
                self.agents.insert(agent_name, config);
            }
        }
    }

    /// Register a new agent.
    pub fn register(&mut self, name: &str, config: AgentConfig) {
        self.agents.insert(name.to_string(), config);
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
    pub fn resolve_from_logfile_name(&self, logfile_name: &str) -> Option<String> {
        // First check if the name is exactly a registry name (no sanitization was needed)
        if self.agents.contains_key(logfile_name) {
            return Some(logfile_name.to_string());
        }

        // Search registered agents for one whose sanitized name matches
        for name in self.agents.keys() {
            let sanitized = name.replace('/', "-");
            if sanitized == logfile_name {
                return Some(name.clone());
            }
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
            if parts.len() == 3 && parts[0] == "opencode" {
                return Some(name.to_string());
            }
        }

        // Handle common typos/alternatives
        let normalized = name.to_lowercase();
        let alternatives = Self::get_fuzzy_alternatives(&normalized);

        for alt in alternatives {
            // If it's a ccs/ pattern, return it for direct CCS execution
            if alt.starts_with("ccs/") {
                return Some(alt);
            }
            // If it's an opencode/ pattern, validate the format
            if alt.starts_with("opencode/") {
                let parts: Vec<&str> = alt.split('/').collect();
                if parts.len() == 3 && parts[0] == "opencode" {
                    return Some(alt);
                }
            }
            // Otherwise check if it exists in the registry
            if self.agents.contains_key(&alt) {
                return Some(alt);
            }
        }

        None
    }

    /// Get fuzzy alternatives for a given agent name.
    ///
    /// Returns a list of potential canonical names to try, in order of preference.
    pub(crate) fn get_fuzzy_alternatives(name: &str) -> Vec<String> {
        let mut alternatives = Vec::new();

        // Add exact match first
        alternatives.push(name.to_string());

        // Handle common typos and variations
        match name {
            // ccs variations
            n if n.starts_with("ccs-") => {
                alternatives.push(name.replace("ccs-", "ccs/"));
            }
            n if n.contains('_') => {
                alternatives.push(name.replace('_', "-"));
                alternatives.push(name.replace('_', "/"));
            }

            // claude variations
            "claud" | "cloud" => alternatives.push("claude".to_string()),

            // codex variations
            "codeex" | "code-x" => alternatives.push("codex".to_string()),

            // cursor variations
            "crusor" => alternatives.push("cursor".to_string()),

            // opencode variations
            "opencode" | "open-code" => alternatives.push("opencode".to_string()),

            // gemini variations
            "gemeni" | "gemni" => alternatives.push("gemini".to_string()),

            // qwen variations
            "quen" | "quwen" => alternatives.push("qwen".to_string()),

            // aider variations
            "ader" => alternatives.push("aider".to_string()),

            // vibe variations
            "vib" => alternatives.push("vibe".to_string()),

            // cline variations
            "kline" => alternatives.push("cline".to_string()),

            _ => {}
        }

        alternatives
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

    /// Get the fallback configuration.
    #[must_use]
    pub const fn fallback_config(&self) -> &FallbackConfig {
        &self.fallback
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

    /// Get the retry timer provider.
    #[must_use]
    pub fn retry_timer(&self) -> Arc<dyn RetryTimerProvider> {
        Arc::clone(&self.retry_timer)
    }

    /// Set the retry timer provider (for testing purposes).
    ///
    /// This is used to inject a test timer that doesn't actually sleep,
    /// enabling fast test execution without waiting for retry delays.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn set_retry_timer(&mut self, timer: Arc<dyn RetryTimerProvider>) {
        self.retry_timer = timer;
    }

    /// Get all fallback agents for a role that are registered in this registry.
    pub fn available_fallbacks(&self, role: AgentRole) -> Vec<&str> {
        self.fallback
            .get_fallbacks(role)
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

    /// Validate that agent chains are configured for both roles.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn validate_agent_chains(&self, searched_sources: &str) -> Result<(), String> {
        let dev_chain = self
            .resolved_drain(crate::agents::AgentDrain::Development)
            .map_or(&[][..], |binding| binding.agents.as_slice());
        let review_chain = self
            .resolved_drain(crate::agents::AgentDrain::Review)
            .map_or(&[][..], |binding| binding.agents.as_slice());
        let has_developer = !dev_chain.is_empty();
        let has_reviewer = !review_chain.is_empty();

        if !has_developer && !has_reviewer {
            return Err(format!(
                "No agent chain configured. \
                Searched: {searched_sources}.\n\
                Please add [agent_chains] and [agent_drains] sections to your config.\n\
                Legacy [agent_chain] input is still accepted for compatibility.\n\
                Run 'ralph --init-global' to create a default configuration."
            ));
        }

        if !has_developer {
            return Err(format!(
                "No developer agent chain configured. \
                Searched: {searched_sources}.\n\
                Bind the development drain in [agent_drains] to a chain from [agent_chains].\n\
                Use --list-agents to see available agents."
            ));
        }

        if !has_reviewer {
            return Err(format!(
                "No reviewer agent chain configured. \
                Searched: {searched_sources}.\n\
                Bind the review drain in [agent_drains] to a chain from [agent_chains].\n\
                Use --list-agents to see available agents."
            ));
        }

        // Sanity check: ensure there is at least one workflow-capable agent per role.
        for (role, chain) in [
            (AgentRole::Developer, dev_chain),
            (AgentRole::Reviewer, review_chain),
        ] {
            let has_capable = chain
                .iter()
                .any(|name| self.resolve_config(name).is_some_and(|cfg| cfg.can_commit));
            if !has_capable {
                return Err(format!(
                    "No workflow-capable agents found for {role}.\n\
                    All agents in the {role} chain have can_commit=false.\n\
                    Fix: set can_commit=true for at least one agent or update [agent_chains]/[agent_drains]."
                ));
            }
        }

        Ok(())
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
