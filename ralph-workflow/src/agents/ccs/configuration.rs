// CCS configuration handling - config types, aliases, environment variables
//
// # Conditional Visibility Pattern
//
// This module uses a conditional compilation pattern for several functions:
// - `pub fn` when `test` or `test-utils` feature is enabled (for test access)
// - `pub(crate) fn` otherwise (internal-only in production)
//
// Both variants call the same `_impl` function, avoiding code duplication while
// providing conditional API visibility. This pattern appears repetitive but is
// intentional: it keeps test utilities accessible in tests while hiding them
// from the public API surface in production builds.
//
// Functions using this pattern:
// - ccs_env_var_debug_summary
// - resolve_ccs_command
// - build_ccs_agent_config

/// Result type for CCS command resolution with diagnostics.
#[derive(Debug, Clone)]
pub struct CcsCommandResult {
    pub command: String,
}

/// Resolve a CCS alias to an `AgentConfig`.
///
/// Given a CCS alias and a map of aliases to commands, this function
/// generates an `AgentConfig` that can be used to run CCS.
///
/// # Arguments
///
/// * `alias` - The alias name (e.g., "work", "gemini", or "" for default)
/// * `aliases` - Map of alias names to CCS commands
///
/// # Returns
///
/// Returns `Some(AgentConfig)` if the alias is found or if using default,
/// `None` if the alias is not found in the map.
#[must_use]
pub fn resolve_ccs_agent<S: std::hash::BuildHasher>(
    alias: &str,
    aliases: &HashMap<String, CcsAliasConfig, S>,
    defaults: &CcsConfig,
) -> Option<AgentConfig> {
    // Empty alias means use default CCS
    let (cmd, display_name) = if alias.is_empty() {
        (
            CcsAliasConfig {
                cmd: "ccs".to_string(),
                ..CcsAliasConfig::default()
            },
            "ccs".to_string(),
        )
    } else if let Some(cfg) = aliases.get(alias) {
        (cfg.clone(), format!("ccs-{alias}"))
    } else {
        // Unknown alias - return None so caller can fall back
        return None;
    };

    Some(build_ccs_agent_config(&cmd, defaults, display_name, alias))
}

/// Build an `AgentConfig` for a CCS command.
///
/// CCS wraps Claude Code, so it uses Claude's stream-json format
/// and similar flags.
///
/// # JSON Parser Selection
///
/// CCS (Claude Code Switcher) defaults to the Claude parser (`json_parser = "claude"`)
/// because CCS wraps the `claude` CLI tool and uses Claude's stream-json output format.
///
/// **Why Claude parser by default?** CCS uses Claude Code's CLI interface and output format.
/// The `--output-format=stream-json` flag produces Claude's NDJSON format, which the
/// Claude parser is designed to handle.
///
/// **Parser override:** Users can override the parser via `json_parser` in their config.
/// The alias-specific `json_parser` takes precedence over the CCS default. This allows
/// advanced users to use alternative parsers if needed for specific providers.
///
/// Example: `ccs glm` -> uses Claude parser by default (from `defaults.json_parser`)
///          `ccs gemini` -> uses Claude parser by default
///          With override: `json_parser = "generic"` in alias config overrides default
///
/// Display name format: CCS aliases are shown as "ccs-{alias}" (e.g., "ccs-glm", "ccs-gemini")
/// in output/logs to make it clearer which provider is actually being used, while still using
/// the Claude parser under the hood.
///
/// # Environment Variable Loading
///
/// This function automatically loads environment variables for the resolved CCS profile using
/// CCS config mappings (`~/.ccs/config.json` / `~/.ccs/config.yaml`) and common settings file
/// naming (`~/.ccs/{profile}.settings.json` / `~/.ccs/{profile}.setting.json`). This allows
/// Log CCS environment variables loading status (debug mode only).
///
/// Only logs whitelisted "safe" environment variable keys to prevent accidental
/// leakage of sensitive credential values. Keys containing patterns like "token",
/// "key", "secret", "password", "auth" are always filtered out regardless of
/// their actual value, to protect against custom credential formats.

const fn is_glm_alias(alias_name: &str) -> bool {
    alias_name.eq_ignore_ascii_case("glm")
}

/// Resolve the CCS command, potentially bypassing the ccs wrapper for direct claude binary.
///
/// For CCS aliases, we try to use `claude` directly instead of the `ccs` wrapper
/// because the wrapper does not pass through all flags properly (especially
/// streaming-related flags like --include-partial-messages).
///
/// We only bypass the wrapper when:
/// - The agent name is `ccs/<alias>` (not plain `ccs`)
/// - We successfully loaded at least one env var for that profile
/// - The configured command targets that profile (e.g. `ccs <profile>` or `ccs api <profile>`
#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn resolve_ccs_command(
    alias_config: &CcsAliasConfig,
    alias_name: &str,
    env_vars_loaded: bool,
    profile_used_for_env: Option<&String>,
    debug_mode: bool,
) -> CcsCommandResult {
    resolve_ccs_command_impl(
        alias_config,
        alias_name,
        env_vars_loaded,
        profile_used_for_env,
        debug_mode,
    )
}

#[cfg(not(any(test, feature = "test-utils")))]
#[must_use]
pub fn resolve_ccs_command(
    alias_config: &CcsAliasConfig,
    alias_name: &str,
    env_vars_loaded: bool,
    profile_used_for_env: Option<&String>,
    debug_mode: bool,
) -> CcsCommandResult {
    resolve_ccs_command_impl(
        alias_config,
        alias_name,
        env_vars_loaded,
        profile_used_for_env,
        debug_mode,
    )
}

fn resolve_ccs_command_impl(
    alias_config: &CcsAliasConfig,
    alias_name: &str,
    env_vars_loaded: bool,
    profile_used_for_env: Option<&String>,
    _debug_mode: bool,
) -> CcsCommandResult {
    let original_cmd = alias_config.cmd.as_str();

    find_claude_binary().map_or_else(
        || CcsCommandResult {
            command: original_cmd.to_string(),
        },
        |claude_path| {
            let can_bypass_wrapper = is_glm_alias(alias_name) && env_vars_loaded;

            if !can_bypass_wrapper {
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                };
            }

            let Ok(parts) = split_command(original_cmd) else {
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                };
            };

            let profile = ccs_profile_from_command(original_cmd)
                .or_else(|| profile_used_for_env.cloned())
                .unwrap_or_else(|| alias_name.to_string());
            let is_ccs_cmd = parts.first().is_some_and(|p| looks_like_ccs_executable(p));
            let skip = if parts.get(1).is_some_and(|p| p == &profile) {
                Some(2)
            } else if parts.get(1).is_some_and(|p| p == "api")
                && parts.get(2).is_some_and(|p| p == &profile)
            {
                Some(3)
            } else {
                None
            };
            let is_profile_ccs_cmd = is_ccs_cmd && skip.is_some();

            if !is_profile_ccs_cmd {
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                };
            }

            let skip = skip.unwrap_or(2);
            let new_parts: Vec<String> = std::iter::once(claude_path.to_string_lossy().to_string())
                .chain(parts.into_iter().skip(skip))
                .collect();
            let new_cmd = shell_words::join(&new_parts);

            CcsCommandResult { command: new_cmd }
        },
    )
}

include!("agent_config.rs");
