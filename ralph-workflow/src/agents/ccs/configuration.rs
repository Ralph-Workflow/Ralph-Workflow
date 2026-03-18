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

/// Diagnostic messages produced during CCS configuration resolution.
///
/// These replace direct `eprintln!` calls in domain code, allowing
/// the caller to decide where and how to emit diagnostics (I/O boundary).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CcsDiagnostic {
    EnvVarsLoaded {
        count: usize,
        profile: String,
    },
    EnvVarsLoadFailed {
        profile: String,
    },
    EnvVarsKeyListed {
        key: String,
    },
    EnvVarsKeyRedacted {
        count: usize,
    },
    EnvVarsKeysHidden {
        count: usize,
    },
    ProfileNotFound {
        profile: String,
        guessed: Option<String>,
    },
    EnvLoadFailed {
        profile: String,
        error: String,
    },
    ProfileSuggestion {
        suggestion: String,
    },
    ClaudeBinaryNotFound,
    UsingCcsWrapperWarning,
    StreamingFlagsWarning,
    InstallInstructionsWarning,
    BypassConditionsNotMet,
    CommandParseFailed,
    CommandNotProfileCcs,
    BypassingWrapper {
        command: String,
    },
    ClaudeBinaryPath {
        path: String,
    },
    OriginalCommand {
        command: String,
    },
    AliasName {
        name: String,
    },
    EnvVarsLoadedFlag {
        loaded: bool,
    },
    CanBypassFlag {
        can_bypass: bool,
    },
    CommandParts {
        parts: Vec<String>,
    },
    IsProfileCcs {
        is_profile: bool,
    },
}

use std::fmt;

impl fmt::Display for CcsDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CcsDiagnostic::EnvVarsLoaded { count, profile } => {
                write!(
                    f,
                    "CCS DEBUG: Loaded {} environment variable(s) for profile '{}'",
                    count, profile
                )
            }
            CcsDiagnostic::EnvVarsLoadFailed { profile } => {
                write!(
                    f,
                    "CCS DEBUG: Failed to load environment variables for profile '{}'",
                    profile
                )
            }
            CcsDiagnostic::EnvVarsKeyListed { key } => {
                write!(f, "CCS DEBUG:   - {key}")
            }
            CcsDiagnostic::EnvVarsKeyRedacted { count } => {
                write!(f, "CCS DEBUG:   - ({} sensitive key(s) redacted)", count)
            }
            CcsDiagnostic::EnvVarsKeysHidden { count } => {
                write!(
                    f,
                    "CCS DEBUG:   - ({} non-whitelisted key(s) hidden)",
                    count
                )
            }
            CcsDiagnostic::ProfileNotFound { profile, guessed } => {
                if let Some(ref g) = guessed {
                    write!(f, "Info: CCS profile '{profile}' not found; using '{g}'")
                } else {
                    write!(f, "Info: CCS profile '{profile}' not found")
                }
            }
            CcsDiagnostic::EnvLoadFailed { profile, error } => {
                write!(
                    f,
                    "Warning: failed to load CCS env vars for profile '{profile}': {error}"
                )
            }
            CcsDiagnostic::ProfileSuggestion { suggestion } => {
                write!(f, "Tip: available/nearby CCS profiles:  - {suggestion}")
            }
            CcsDiagnostic::ClaudeBinaryNotFound => {
                write!(f, "CCS DEBUG: Claude binary not found in PATH")
            }
            CcsDiagnostic::UsingCcsWrapperWarning => {
                write!(
                    f,
                    "Warning: `claude` binary not found in PATH, using `ccs` wrapper"
                )
            }
            CcsDiagnostic::StreamingFlagsWarning => {
                write!(
                    f,
                    "  This may cause issues with streaming flags like --include-partial-messages"
                )
            }
            CcsDiagnostic::InstallInstructionsWarning => {
                write!(f, "  Consider installing the Claude CLI (see your internal install docs, or the upstream Claude CLI installer)")
            }
            CcsDiagnostic::BypassConditionsNotMet => {
                write!(f, "CCS DEBUG: Not bypassing (conditions not met)")
            }
            CcsDiagnostic::CommandParseFailed => {
                write!(f, "CCS DEBUG: Failed to parse command, using original")
            }
            CcsDiagnostic::CommandNotProfileCcs => {
                write!(
                    f,
                    "CCS DEBUG: Not bypassing (command doesn't match pattern)"
                )
            }
            CcsDiagnostic::BypassingWrapper { command } => {
                write!(f, "CCS DEBUG: bypassing `ccs` wrapper for `ccs` to preserve Claude CLI flag passthrough. New command: {command}")
            }
            CcsDiagnostic::ClaudeBinaryPath { path } => {
                write!(f, "CCS DEBUG: Claude binary found at: {}", path)
            }
            CcsDiagnostic::OriginalCommand { command } => {
                write!(f, "CCS DEBUG: Original command: {command}")
            }
            CcsDiagnostic::AliasName { name } => {
                write!(f, "CCS DEBUG: Alias name: '{name}'")
            }
            CcsDiagnostic::EnvVarsLoadedFlag { loaded } => {
                write!(f, "CCS DEBUG: Env vars loaded: {loaded}")
            }
            CcsDiagnostic::CanBypassFlag { can_bypass } => {
                write!(f, "CCS DEBUG: Can bypass wrapper: {can_bypass}")
            }
            CcsDiagnostic::CommandParts { parts } => {
                write!(f, "CCS DEBUG: Command parts: {parts:?}")
            }
            CcsDiagnostic::IsProfileCcs { is_profile } => {
                write!(f, "CCS DEBUG: Is profile CCS command: {is_profile}")
            }
        }
    }
}

/// Result type for CCS command resolution with diagnostics.
#[derive(Debug, Clone)]
pub struct CcsCommandResult {
    pub command: String,
    pub diagnostics: Vec<CcsDiagnostic>,
}

/// Emit CCS diagnostics to stderr.
///
/// This is the I/O boundary function that converts diagnostic data into
/// console output. It should only be called once, at the outermost
/// call site where CCS configuration is resolved.
pub fn emit_ccs_diagnostics(debug_mode: bool, diagnostics: &[CcsDiagnostic]) {
    if debug_mode {
        diagnostics.iter().for_each(|d| eprintln!("{d}"));
    }
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
///
/// Debug summary of CCS environment variables loaded for a profile.
/// Public in test mode for testing; crate-private in production.
#[cfg(any(test, feature = "test-utils"))]
pub struct CcsEnvVarDebugSummary {
    pub whitelisted_keys_present: Vec<String>,
    pub redacted_sensitive_keys: usize,
    pub hidden_non_whitelisted_keys: usize,
}

#[cfg(not(any(test, feature = "test-utils")))]
pub struct CcsEnvVarDebugSummary {
    pub(crate) whitelisted_keys_present: Vec<String>,
    pub(crate) redacted_sensitive_keys: usize,
    pub(crate) hidden_non_whitelisted_keys: usize,
}

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn ccs_env_var_debug_summary<S: std::hash::BuildHasher>(
    env_vars: &HashMap<String, String, S>,
) -> CcsEnvVarDebugSummary {
    ccs_env_var_debug_summary_impl(env_vars)
}

#[cfg(not(any(test, feature = "test-utils")))]
pub fn ccs_env_var_debug_summary(env_vars: &HashMap<String, String>) -> CcsEnvVarDebugSummary {
    ccs_env_var_debug_summary_impl(env_vars)
}

fn ccs_env_var_debug_summary_impl<S: std::hash::BuildHasher>(
    env_vars: &HashMap<String, String, S>,
) -> CcsEnvVarDebugSummary {
    // Whitelist of safe-to-log environment variable keys.
    // These are configuration keys, not credentials, so it's safe to log them.
    const SAFE_KEYS: &[&str] = &[
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    ];

    // Helper to check if a key looks sensitive
    fn looks_sensitive(key: &str) -> bool {
        let key_normalized: String = key.chars().filter(char::is_ascii_alphanumeric).collect();
        key_normalized.contains("TOKEN")
            || key_normalized.contains("SECRET")
            || key_normalized.contains("PASSWORD")
            || key_normalized.contains("AUTH")
            || key_normalized.contains("KEY")
            || key_normalized.contains("API")
            || key_normalized == "AUTHORIZATION"
    }

    // Use partition to separate keys into whitelisted vs (sensitive or hidden)
    let (whitelisted, others): (Vec<_>, Vec<_>) = env_vars
        .keys()
        .partition(|key| SAFE_KEYS.contains(&key.to_uppercase().as_str()));

    let (redacted_sensitive_keys, hidden_non_whitelisted_keys): (usize, usize) = others
        .iter()
        .map(|k| {
            let key_upper = k.to_uppercase();
            looks_sensitive(&key_upper) as usize
        })
        .fold((0usize, 0usize), |(r, h), is_sensitive| {
            if is_sensitive != 0 {
                (r.saturating_add(1), h)
            } else {
                (r, h.saturating_add(1))
            }
        });

    let whitelisted_keys_present: Vec<String> = {
        use std::collections::BTreeSet;
        whitelisted
            .into_iter()
            .map(|k| k.to_uppercase())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    };

    CcsEnvVarDebugSummary {
        whitelisted_keys_present,
        redacted_sensitive_keys,
        hidden_non_whitelisted_keys,
    }
}

fn log_ccs_env_vars_loaded(
    debug_mode: bool,
    alias_name: &str,
    profile_used_for_env: Option<&String>,
    env_vars_loaded: bool,
    env_vars: &HashMap<String, String>,
) -> Vec<CcsDiagnostic> {
    if !debug_mode || alias_name.is_empty() {
        return Vec::new();
    }
    let profile = profile_used_for_env.map_or(alias_name, |s| s.as_str());
    if env_vars_loaded {
        let summary = ccs_env_var_debug_summary(env_vars);
        let mut diagnostics = vec![CcsDiagnostic::EnvVarsLoaded {
            count: env_vars.len(),
            profile: profile.to_string(),
        }];
        for key in &summary.whitelisted_keys_present {
            diagnostics.push(CcsDiagnostic::EnvVarsKeyListed { key: key.clone() });
        }
        if summary.redacted_sensitive_keys > 0 {
            diagnostics.push(CcsDiagnostic::EnvVarsKeyRedacted {
                count: summary.redacted_sensitive_keys,
            });
        }
        if summary.hidden_non_whitelisted_keys > 0 {
            diagnostics.push(CcsDiagnostic::EnvVarsKeysHidden {
                count: summary.hidden_non_whitelisted_keys,
            });
        }
        diagnostics
    } else {
        vec![CcsDiagnostic::EnvVarsLoadFailed {
            profile: profile.to_string(),
        }]
    }
}

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
    debug_mode: bool,
) -> CcsCommandResult {
    let original_cmd = alias_config.cmd.as_str();

    find_claude_binary().map_or_else(
        || {
            let mut diagnostics = Vec::new();
            if original_cmd.starts_with("ccs ") || original_cmd == "ccs" {
                if debug_mode {
                    diagnostics.push(CcsDiagnostic::ClaudeBinaryNotFound);
                }
                diagnostics.push(CcsDiagnostic::UsingCcsWrapperWarning);
                diagnostics.push(CcsDiagnostic::StreamingFlagsWarning);
                diagnostics.push(CcsDiagnostic::InstallInstructionsWarning);
            }
            CcsCommandResult {
                command: original_cmd.to_string(),
                diagnostics,
            }
        },
        |claude_path| {
            let can_bypass_wrapper = is_glm_alias(alias_name) && env_vars_loaded;

            let mut diagnostics = Vec::new();
            if debug_mode {
                diagnostics.push(CcsDiagnostic::ClaudeBinaryPath {
                    path: claude_path.to_string_lossy().to_string(),
                });
                diagnostics.push(CcsDiagnostic::OriginalCommand {
                    command: original_cmd.to_string(),
                });
                diagnostics.push(CcsDiagnostic::AliasName {
                    name: alias_name.to_string(),
                });
                diagnostics.push(CcsDiagnostic::EnvVarsLoadedFlag {
                    loaded: env_vars_loaded,
                });
                diagnostics.push(CcsDiagnostic::CanBypassFlag {
                    can_bypass: can_bypass_wrapper,
                });
            }

            if !can_bypass_wrapper {
                if debug_mode {
                    diagnostics.push(CcsDiagnostic::BypassConditionsNotMet);
                }
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                    diagnostics,
                };
            }

            let Ok(parts) = split_command(original_cmd) else {
                if debug_mode {
                    diagnostics.push(CcsDiagnostic::CommandParseFailed);
                }
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                    diagnostics,
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

            if debug_mode {
                diagnostics.push(CcsDiagnostic::CommandParts {
                    parts: parts.clone(),
                });
                diagnostics.push(CcsDiagnostic::IsProfileCcs {
                    is_profile: is_profile_ccs_cmd,
                });
            }

            if !is_profile_ccs_cmd {
                if debug_mode {
                    diagnostics.push(CcsDiagnostic::CommandNotProfileCcs);
                }
                return CcsCommandResult {
                    command: original_cmd.to_string(),
                    diagnostics,
                };
            }

            let skip = skip.unwrap_or(2);
            let new_parts: Vec<String> = std::iter::once(claude_path.to_string_lossy().to_string())
                .chain(parts.into_iter().skip(skip))
                .collect();
            let new_cmd = shell_words::join(&new_parts);

            if debug_mode {
                diagnostics.push(CcsDiagnostic::CommandParts {
                    parts: new_parts.clone(),
                });
                diagnostics.push(CcsDiagnostic::OriginalCommand {
                    command: new_cmd.clone(),
                });
                diagnostics.push(CcsDiagnostic::BypassingWrapper {
                    command: new_cmd.clone(),
                });
            }
            CcsCommandResult {
                command: new_cmd,
                diagnostics,
            }
        },
    )
}

include!("agent_config.rs");
