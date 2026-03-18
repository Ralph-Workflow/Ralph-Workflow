// Agent loading and discovery logic.
// Includes loading from config files, applying unified config, and creating/merging agent configs.

impl AgentRegistry {
    fn merge_general_runtime_settings(
        base: &crate::agents::fallback::ResolvedDrainConfig,
        general: &crate::config::unified::GeneralConfig,
    ) -> crate::agents::fallback::ResolvedDrainConfig {
        crate::agents::fallback::ResolvedDrainConfig {
            bindings: base.bindings.clone(),
            provider_fallback: if general.provider_fallback.is_empty() {
                base.provider_fallback.clone()
            } else {
                general.provider_fallback.clone()
            },
            max_retries: general.max_retries,
            retry_delay_ms: general.retry_delay_ms,
            backoff_multiplier: general.backoff_multiplier,
            max_backoff_ms: general.max_backoff_ms,
            max_cycles: general.max_cycles,
        }
    }

    /// Load custom agents from a TOML configuration file.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn load_from_file<P: AsRef<Path>>(&mut self, path: P) -> Result<usize, AgentConfigError> {
        match AgentsConfigFile::load_from_file(path)? {
            Some(config) => {
                let count = config.agents.len();
                let resolved_drains = config.resolve_drains_checked()?;

                // Collect agents functionally - build new HashMap with chain
                let agents_to_register: HashMap<_, _> = config
                    .agents
                    .into_iter()
                    .map(|(name, agent_toml)| (name, AgentConfig::from(agent_toml)))
                    .collect();
                // Rebuild with chain instead of extend
                self.agents = self
                    .agents
                    .clone()
                    .into_iter()
                    .chain(agents_to_register)
                    .collect();

                self.resolved_drains = resolved_drains.unwrap_or_else(|| {
                    Self::merge_general_runtime_settings(
                        &self.resolved_drains,
                        &crate::config::unified::GeneralConfig::default(),
                    )
                });
                Ok(count)
            }
            None => Ok(0),
        }
    }

    /// Apply settings from the unified config (`~/.config/ralph-workflow.toml`).
    ///
    /// This merges (in increasing priority):
    /// 1. Built-in defaults (embedded `examples/agents.toml`)
    /// 2. Unified config: `[agents]`, `[ccs_aliases]`, and any resolved
    ///    `[agent_chains]` / `[agent_drains]` or legacy compatibility metadata
    ///
    /// Returns the number of agents loaded from unified config, including CCS aliases.
    ///
    /// # Errors
    ///
    /// Returns an error when the named drain configuration is invalid.
    #[expect(
        clippy::arithmetic_side_effects,
        reason = "small counter that cannot realistically overflow"
    )]
    pub fn apply_unified_config(
        &mut self,
        unified: &crate::config::UnifiedConfig,
    ) -> Result<usize, AgentConfigError> {
        let ccs_loaded = self.apply_ccs_aliases(unified);
        let agent_loaded = self.apply_agent_overrides(unified);
        let loaded = ccs_loaded + agent_loaded;

        self.resolved_drains = unified
            .resolve_agent_drains_checked()
            .map_err(AgentConfigError::InvalidDrainConfig)?
            .unwrap_or_else(|| {
                Self::merge_general_runtime_settings(&self.resolved_drains, &unified.general)
            });

        Ok(loaded)
    }

    /// Apply CCS aliases from the unified config.
    fn apply_ccs_aliases(&mut self, unified: &crate::config::UnifiedConfig) -> usize {
        if unified.ccs_aliases.is_empty() {
            return 0;
        }

        let loaded = unified.ccs_aliases.len();
        let aliases = unified
            .ccs_aliases
            .iter()
            .map(|(name, v)| (name.clone(), v.as_config()))
            .collect::<HashMap<_, _>>();
        self.set_ccs_aliases(&aliases, unified.ccs.clone());
        loaded
    }

    /// Apply agent overrides from the unified config.
    fn apply_agent_overrides(&mut self, unified: &crate::config::UnifiedConfig) -> usize {
        if unified.agents.is_empty() {
            return 0;
        }

        // First, collect all registrations functionally
        let registrations: Vec<_> = unified
            .agents
            .iter()
            .filter_map(|(name, overrides)| {
                self.agents
                    .get(name)
                    .cloned()
                    .map(|existing| (name.clone(), Self::merge_agent_config(existing, overrides)))
                    .or_else(|| {
                        Self::create_new_agent_config(overrides)
                            .map(|config| (name.clone(), config))
                    })
            })
            .collect();

        // Then register them (requires mutation - use for_each which is a method call, not a for loop)
        registrations
            .iter()
            .for_each(|(name, config)| self.register(name, config.clone()));

        registrations.len()
    }

    /// Create a new agent config from unified config overrides.
    fn create_new_agent_config(
        overrides: &crate::config::unified::AgentConfigToml,
    ) -> Option<AgentConfig> {
        let cmd = overrides
            .cmd
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())?;

        let json_parser = overrides
            .json_parser
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("generic");

        Some(AgentConfig {
            cmd: cmd.to_string(),
            output_flag: overrides.output_flag.clone().unwrap_or_default(),
            yolo_flag: overrides.yolo_flag.clone().unwrap_or_default(),
            verbose_flag: overrides.verbose_flag.clone().unwrap_or_default(),
            can_commit: overrides.can_commit.unwrap_or(true),
            json_parser: JsonParserType::parse(json_parser),
            model_flag: overrides.model_flag.clone(),
            print_flag: overrides.print_flag.clone().unwrap_or_default(),
            streaming_flag: overrides.streaming_flag.clone().unwrap_or_else(|| {
                // Default to "--include-partial-messages" for Claude/CCS agents
                if cmd.starts_with("claude") || cmd.starts_with("ccs") {
                    "--include-partial-messages".to_string()
                } else {
                    String::new()
                }
            }),
            session_flag: overrides.session_flag.clone().unwrap_or_else(|| {
                // Default session continuation flags for known agents
                // These flags are verified from CLI --help output:
                // - Claude: --resume <session_id> (from `claude --help`)
                // - OpenCode: -s <session_id> (from `opencode run --help`)
                // - Codex: Uses `codex exec resume <id>` subcommand, not a flag - not supported
                if cmd.starts_with("claude") || cmd.starts_with("ccs") {
                    "--resume {}".to_string()
                } else if cmd.starts_with("opencode") {
                    "-s {}".to_string()
                } else {
                    String::new()
                }
            }),
            env_vars: std::collections::HashMap::new(),
            display_name: overrides
                .display_name
                .as_ref()
                .filter(|s| !s.is_empty())
                .cloned(),
        })
    }

    /// Merge overrides with existing agent config.
    fn merge_agent_config(
        existing: AgentConfig,
        overrides: &crate::config::unified::AgentConfigToml,
    ) -> AgentConfig {
        AgentConfig {
            cmd: overrides
                .cmd
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .unwrap_or(existing.cmd),
            output_flag: overrides
                .output_flag
                .clone()
                .unwrap_or(existing.output_flag),
            yolo_flag: overrides.yolo_flag.clone().unwrap_or(existing.yolo_flag),
            verbose_flag: overrides
                .verbose_flag
                .clone()
                .unwrap_or(existing.verbose_flag),
            can_commit: overrides.can_commit.unwrap_or(existing.can_commit),
            json_parser: overrides
                .json_parser
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map_or(existing.json_parser, JsonParserType::parse),
            model_flag: overrides.model_flag.clone().or(existing.model_flag),
            print_flag: overrides.print_flag.clone().unwrap_or(existing.print_flag),
            streaming_flag: overrides
                .streaming_flag
                .clone()
                .unwrap_or(existing.streaming_flag),
            session_flag: overrides
                .session_flag
                .clone()
                .unwrap_or(existing.session_flag),
            // Do NOT inherit env_vars from the existing agent to prevent
            // CCS env vars from one agent from leaking into another.
            // The unified config (unified::AgentConfigToml) doesn't support
            // ccs_profile or env_vars fields, so we always start fresh.
            env_vars: std::collections::HashMap::new(),
            // Preserve existing display name unless explicitly overridden
            // Empty string explicitly clears the display name
            display_name: match &overrides.display_name {
                Some(s) if s.is_empty() => None,
                Some(s) => Some(s.clone()),
                None => existing.display_name,
            },
        }
    }
}
