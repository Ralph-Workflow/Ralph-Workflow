/// Build the final `AgentConfig` from alias config and defaults.
fn build_ccs_config_from_flags(
    alias_config: &CcsAliasConfig,
    defaults: &CcsConfig,
    cmd: String,
    env_vars: HashMap<String, String>,
    display_name: String,
) -> AgentConfig {
    let output_flag = alias_config
        .output_flag
        .clone()
        .unwrap_or_else(|| defaults.output_flag.clone());
    let yolo_flag = alias_config
        .yolo_flag
        .clone()
        .unwrap_or_else(|| defaults.yolo_flag.clone());
    let verbose_flag = alias_config
        .verbose_flag
        .clone()
        .unwrap_or_else(|| defaults.verbose_flag.clone());
    // CCS headless behavior: when invoking via the `ccs` wrapper, avoid `-p` because CCS
    // interprets `-p`/`--prompt` as its own headless delegation mode.
    // Use Claude's long-form `--print` flag for non-interactive mode instead.
    // If defaults.print_flag is empty (missing config), fall back to "--print".
    let print_flag = alias_config.print_flag.clone().unwrap_or_else(|| {
        let pf = defaults.print_flag.clone();
        if pf.is_empty() {
            // Hardcoded safety fallback: use --print to avoid CCS delegation interception
            "--print".to_string()
        } else {
            pf
        }
    });

    // Parser selection: alias-specific override takes precedence over CCS default.
    // This allows users to customize parser per CCS alias if needed.
    // See function docstring above for detailed explanation.
    let json_parser = alias_config
        .json_parser
        .as_deref()
        .unwrap_or(&defaults.json_parser);
    let can_commit = alias_config.can_commit.unwrap_or(defaults.can_commit);

    // Get streaming flag from alias override or defaults
    let streaming_flag = alias_config
        .streaming_flag
        .clone()
        .unwrap_or_else(|| defaults.streaming_flag.clone());

    // Session continuation flag: prefer alias-specific override, then unified-config CCS defaults.
    // This is used for XSD retry loops to continue an existing conversation.
    let session_flag = alias_config
        .session_flag
        .clone()
        .unwrap_or_else(|| defaults.session_flag.clone());

    AgentConfig {
        cmd, // Uses `claude` directly if found, otherwise falls back to original command
        output_flag,
        yolo_flag,
        verbose_flag,
        can_commit,
        json_parser: JsonParserType::parse(json_parser),
        model_flag: alias_config.model_flag.clone(),
        print_flag, // Default: --print (safe for `ccs` wrapper); user can override per-alias
        streaming_flag, // Required for JSON streaming when using -p
        session_flag, // Session continuation flag for XSD retries
        env_vars,   // Loaded from CCS settings for the resolved profile, if available
        display_name: Some(display_name),
    }
}

/// Build an `AgentConfig` for CCS, loading credentials and determining command to use.
///
/// CCS aliases to use their configured credentials without requiring manual environment variable
/// configuration, while avoiding hard-coded assumptions about CCS' internal schema.
#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn build_ccs_agent_config(
    alias_config: &CcsAliasConfig,
    defaults: &CcsConfig,
    display_name: String,
    alias_name: &str,
) -> AgentConfig {
    build_ccs_agent_config_impl(alias_config, defaults, display_name, alias_name)
}

#[cfg(not(any(test, feature = "test-utils")))]
pub fn build_ccs_agent_config(
    alias_config: &CcsAliasConfig,
    defaults: &CcsConfig,
    display_name: String,
    alias_name: &str,
) -> AgentConfig {
    build_ccs_agent_config_impl(alias_config, defaults, display_name, alias_name)
}

fn build_ccs_agent_config_impl(
    alias_config: &CcsAliasConfig,
    defaults: &CcsConfig,
    display_name: String,
    alias_name: &str,
) -> AgentConfig {
    // Check for CCS_DEBUG env var to enable detailed logging
    let debug_mode = std::env::var("RALPH_CCS_DEBUG").is_ok();

    let mut profile_used_for_env: Option<String> = None;
    let (env_vars, env_vars_loaded) = if alias_name.is_empty() {
        (HashMap::new(), false)
    } else if is_glm_alias(alias_name) {
        let original_cmd = alias_config.cmd.as_str();
        let profile =
            ccs_profile_from_command(original_cmd).unwrap_or_else(|| alias_name.to_string());
        profile_used_for_env = Some(profile.clone());
        match load_ccs_env_vars_with_guess(&profile) {
            Ok((vars, guessed)) => {
                if let Some(guessed) = guessed {
                    eprintln!("Info: CCS profile '{profile}' not found; using '{guessed}'");
                }
                let loaded = !vars.is_empty();
                (vars, loaded)
            }
            Err(err) => {
                let suggestions = find_ccs_profile_suggestions(&profile);
                eprintln!("Warning: failed to load CCS env vars for profile '{profile}': {err}");
                if !suggestions.is_empty() {
                    eprintln!("Tip: available/nearby CCS profiles:");
                    for s in suggestions {
                        eprintln!("  - {s}");
                    }
                }
                (HashMap::new(), false)
            }
        }
    } else {
        // Non-GLM CCS aliases must execute `ccs ...` directly.
        // Do not inject GLM/Anthropic-style env vars for other providers.
        (HashMap::new(), false)
    };

    // Debug logging: Show env vars loaded
    log_ccs_env_vars_loaded(
        debug_mode,
        alias_name,
        profile_used_for_env.as_ref(),
        env_vars_loaded,
        &env_vars,
    );

    // Determine the command to use
    let cmd = resolve_ccs_command(
        alias_config,
        alias_name,
        env_vars_loaded,
        profile_used_for_env.as_ref(),
        debug_mode,
    );

    // Build the final AgentConfig
    build_ccs_config_from_flags(alias_config, defaults, cmd, env_vars, display_name)
}

/// CCS alias resolver that can be used by the agent registry.
#[derive(Debug, Clone, Default)]
pub struct CcsAliasResolver {
    aliases: HashMap<String, CcsAliasConfig>,
    defaults: CcsConfig,
}

impl CcsAliasResolver {
    /// Create a new CCS alias resolver with the given aliases.
    #[must_use]
    pub const fn new(aliases: HashMap<String, CcsAliasConfig>, defaults: CcsConfig) -> Self {
        Self { aliases, defaults }
    }

    /// Create an empty resolver (no aliases configured).
    #[must_use]
    pub fn empty() -> Self {
        Self::default()
    }

    /// Try to resolve an agent name as a CCS reference.
    ///
    /// Returns `Some(AgentConfig)` if the name is a valid CCS reference.
    /// For known aliases (or default `ccs`), uses the configured command.
    /// For unknown aliases (e.g., `ccs/random`), generates a default CCS config
    /// to allow direct CCS execution without configuration.
    /// Returns `None` if the name is not a CCS reference (doesn't start with "ccs").
    #[must_use]
    pub fn try_resolve(&self, agent_name: &str) -> Option<AgentConfig> {
        let alias = parse_ccs_ref(agent_name)?;
        // Try to resolve from configured aliases
        if let Some(config) = resolve_ccs_agent(alias, &self.aliases, &self.defaults) {
            return Some(config);
        }
        // For unknown CCS aliases, generate a default config for direct execution
        // This allows commands like `ccs random` to work without pre-configuration
        let cmd = CcsAliasConfig {
            cmd: format!("ccs {alias}"),
            ..CcsAliasConfig::default()
        };
        let display_name = format!("ccs-{alias}");
        Some(build_ccs_agent_config(
            &cmd,
            &self.defaults,
            display_name,
            alias,
        ))
    }
}
