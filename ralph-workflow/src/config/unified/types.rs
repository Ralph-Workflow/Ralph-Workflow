//! Configuration type definitions.
//!
//! This module contains all the type definitions for Ralph's unified configuration system.
//! Types are organized into three categories:
//!
//! - **General Configuration**: User preferences, workflow settings, execution behavior
//! - **CCS Configuration**: Claude Code Switch aliases and defaults
//! - **Agent Configuration**: Agent-specific settings and overrides
//!
//! # Type Organization
//!
//! The configuration types follow a nested structure:
//!
//! ```text
//! UnifiedConfig
//! ├── GeneralConfig (user preferences, workflow settings)
//! │   ├── GeneralBehaviorFlags (interactive, auto-detect, strict validation)
//! │   ├── GeneralWorkflowFlags (checkpoint, auto-rebase)
//! │   └── GeneralExecutionFlags (universal prompt, isolation mode)
//! ├── CcsConfig (CCS defaults)
//! ├── CcsAliases (HashMap<String, CcsAliasToml>)
//! │   └── CcsAliasToml (Command string or CcsAliasConfig)
//! ├── agents (HashMap<String, AgentConfigToml>)
//! ├── agent_chains (HashMap<String, Vec<String>>)
//! ├── agent_drains (HashMap<String, String>)
//! └── agent_chain (legacy migration detection only)
//! ```

use crate::agents::fallback::{
    AgentDrain, FallbackConfig, ResolvedDrainBinding, ResolvedDrainConfig,
};
use serde::Deserialize;
use std::collections::HashMap;

// =============================================================================
// General Configuration
// =============================================================================

/// General configuration behavioral flags.
///
/// Groups user interaction and validation-related boolean settings for `GeneralConfig`.
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct GeneralBehaviorFlags {
    /// Interactive mode (keep agent in foreground).
    pub interactive: bool,
    /// Auto-detect project stack for review guidelines.
    pub auto_detect_stack: bool,
    /// Strict PROMPT.md validation.
    pub strict_validation: bool,
}

/// General configuration workflow automation flags.
///
/// Groups workflow automation features for `GeneralConfig`.
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct GeneralWorkflowFlags {
    /// Enable checkpoint/resume functionality.
    pub checkpoint_enabled: bool,
}

/// General configuration execution behavior flags.
///
/// Groups execution behavior settings for `GeneralConfig`.
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct GeneralExecutionFlags {
    /// Force universal review prompt for all agents.
    pub force_universal_prompt: bool,
    /// Isolation mode (prevent context contamination).
    pub isolation_mode: bool,
}

/// General configuration section.
#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(default)]
// Configuration options naturally use many boolean flags. These represent
// independent feature toggles, not a state machine, so bools are appropriate.
pub struct GeneralConfig {
    /// Verbosity level (0-4).
    pub verbosity: u8,
    /// Behavioral flags (interactive, auto-detect, strict validation)
    #[serde(default)]
    pub behavior: GeneralBehaviorFlags,
    /// Workflow automation flags (checkpoint, auto-rebase)
    #[serde(default, flatten)]
    pub workflow: GeneralWorkflowFlags,
    /// Execution behavior flags (universal prompt, isolation mode)
    #[serde(default, flatten)]
    pub execution: GeneralExecutionFlags,
    /// Number of developer iterations.
    pub developer_iters: u32,
    /// Number of reviewer re-review passes.
    pub reviewer_reviews: u32,
    /// Developer context level.
    pub developer_context: u8,
    /// Reviewer context level.
    pub reviewer_context: u8,
    /// Review depth level.
    #[serde(default)]
    pub review_depth: String,
    /// Path to save last prompt.
    #[serde(default)]
    pub prompt_path: Option<String>,
    /// User templates directory for custom template overrides.
    /// When set, templates in this directory take priority over embedded templates.
    #[serde(default)]
    pub templates_dir: Option<String>,
    /// Git user name for commits (optional, falls back to git config).
    #[serde(default)]
    pub git_user_name: Option<String>,
    /// Git user email for commits (optional, falls back to git config).
    #[serde(default)]
    pub git_user_email: Option<String>,
    /// Provider/model fallbacks keyed by agent name.
    #[serde(default)]
    pub provider_fallback: HashMap<String, Vec<String>>,
    /// Maximum continuation attempts when developer returns "partial" or "failed".
    ///
    /// Higher values allow more attempts to complete complex tasks within a single plan.
    ///
    /// # Semantics
    ///
    /// This value counts *continuation attempts* beyond the initial attempt.
    /// Total valid attempts per iteration is `1 + max_dev_continuations`.
    ///
    /// - `0` = no continuations (1 total attempt)
    /// - `2` = two continuations (3 total attempts)
    ///
    /// # Default Behavior
    ///
    /// When omitted from config file, serde applies `default_max_dev_continuations() -> 2`.
    /// This ensures dev loop always has a bounded continuation count, preventing infinite loops.
    ///
    /// The value is wrapped in `Some()` during conversion to `Config`, so
    /// `Config::max_dev_continuations` is never `None` when loaded via `config_from_unified()`.
    ///
    /// Default: 2 continuations (3 total attempts per iteration).
    #[serde(default = "default_max_dev_continuations")]
    pub max_dev_continuations: u32,
    /// Maximum XSD retry attempts when agent output fails XML validation.
    ///
    /// Higher values allow more attempts to fix XML formatting issues before
    /// switching to the next agent in the fallback chain.
    ///
    /// Default: 10 retries before falling back to the next agent.
    #[serde(default = "default_max_xsd_retries")]
    pub max_xsd_retries: u32,
    /// Maximum same-agent retry attempts for transient invocation failures (timeout/internal).
    ///
    /// Semantics: this is a *failure budget* for the current agent. With a value of `2`:
    /// 1st failure → retry the same agent; 2nd failure → fall back to the next agent.
    ///
    /// Default: 2 (one retry before falling back).
    #[serde(default = "default_max_same_agent_retries")]
    pub max_same_agent_retries: u32,
    /// Maximum additional residual commit retries after the initial residual-files check.
    ///
    /// This value counts retry passes beyond pass 1.
    ///
    /// - `0` = no additional retries; residuals carry forward immediately after pass 1
    /// - `10` = retry through pass 11 before carrying forward
    #[serde(default = "default_max_commit_residual_retries")]
    pub max_commit_residual_retries: u32,
    /// Maximum retries per agent before trying the next agent in the active drain.
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    /// Base delay between agent retries in milliseconds.
    #[serde(default = "default_retry_delay_ms")]
    pub retry_delay_ms: u64,
    /// Multiplier for exponential retry backoff.
    #[serde(default = "default_backoff_multiplier")]
    pub backoff_multiplier: f64,
    /// Maximum retry backoff delay in milliseconds.
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
    /// Maximum number of full fallback cycles through a drain before giving up.
    #[serde(default = "default_max_cycles")]
    pub max_cycles: u32,
    /// Maximum number of execution history entries to keep in memory.
    ///
    /// This limits memory growth by dropping oldest entries when the limit is reached.
    /// Prevents unbounded memory growth during long-running pipelines.
    ///
    /// Default: 1000 entries (ring buffer behavior)
    #[serde(default = "default_execution_history_limit")]
    pub execution_history_limit: usize,
}

/// Default maximum continuation attempts per development iteration.
///
/// This allows 2 continuations per iteration (3 total valid attempts including the initial)
/// for fast iteration cycles.
const fn default_max_dev_continuations() -> u32 {
    2
}

/// Default maximum XSD retry attempts before agent fallback.
///
/// This allows 10 retries to fix XML formatting issues before switching agents.
const fn default_max_xsd_retries() -> u32 {
    10
}

/// Default maximum same-agent retry attempts before agent fallback.
///
/// This allows 2 retries for the same agent before switching to the next agent.
const fn default_max_same_agent_retries() -> u32 {
    2
}

const fn default_max_commit_residual_retries() -> u32 {
    10
}

const fn default_max_retries() -> u32 {
    3
}

const fn default_retry_delay_ms() -> u64 {
    1000
}

const fn default_backoff_multiplier() -> f64 {
    2.0
}

const fn default_max_backoff_ms() -> u64 {
    60_000
}

const fn default_max_cycles() -> u32 {
    3
}

/// Default maximum execution history entries to keep in memory.
///
/// This limits memory growth to approximately 400-500 KB of history data.
/// Older entries are dropped to maintain a bounded memory footprint.
const fn default_execution_history_limit() -> usize {
    1000
}

impl Default for GeneralConfig {
    fn default() -> Self {
        Self {
            verbosity: 2, // Verbose
            behavior: GeneralBehaviorFlags {
                interactive: true,
                auto_detect_stack: true,
                strict_validation: false,
            },
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: true,
            },
            execution: GeneralExecutionFlags {
                force_universal_prompt: false,
                isolation_mode: true,
            },
            developer_iters: 5,
            reviewer_reviews: 2,
            developer_context: 1,
            reviewer_context: 0,
            review_depth: "standard".to_string(),
            prompt_path: None,
            templates_dir: None,
            git_user_name: None,
            git_user_email: None,
            provider_fallback: HashMap::new(),
            max_dev_continuations: default_max_dev_continuations(),
            max_xsd_retries: default_max_xsd_retries(),
            max_same_agent_retries: default_max_same_agent_retries(),
            max_commit_residual_retries: default_max_commit_residual_retries(),
            max_retries: default_max_retries(),
            retry_delay_ms: default_retry_delay_ms(),
            backoff_multiplier: default_backoff_multiplier(),
            max_backoff_ms: default_max_backoff_ms(),
            max_cycles: default_max_cycles(),
            execution_history_limit: default_execution_history_limit(),
        }
    }
}

// =============================================================================
// CCS Configuration
// =============================================================================

/// CCS (Claude Code Switch) alias configuration.
///
/// Maps alias names to CCS profile commands.
/// For example: `work = "ccs work"` allows using `ccs/work` as an agent.
pub type CcsAliases = HashMap<String, CcsAliasToml>;

/// CCS defaults applied to all CCS aliases unless overridden per-alias.
#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(default)]
pub struct CcsConfig {
    /// Output-format flag for CCS (often Claude-compatible stream JSON).
    pub output_flag: String,
    /// Flag for autonomous mode (skip permission/confirmation prompts).
    /// Ralph is designed for unattended automation, so this is enabled by default.
    /// Set to empty string ("") to disable and require confirmations.
    pub yolo_flag: String,
    /// Flag for verbose output.
    pub verbose_flag: String,
    /// Print flag for non-interactive mode.
    ///
    /// IMPORTANT: CCS treats `-p` / `--prompt` as *its own* headless delegation mode.
    /// When we execute via the `ccs` wrapper (e.g. `ccs codex`), we must use
    /// Claude's long-form `--print` flag to avoid triggering CCS delegation.
    ///
    /// Default: "--print"
    pub print_flag: String,
    /// Streaming flag for JSON output with -p (required for Claude/CCS to stream).
    /// Default: "--include-partial-messages"
    pub streaming_flag: String,
    /// Which JSON parser to use for CCS output.
    pub json_parser: String,
    /// Session continuation flag template for CCS aliases (Claude CLI).
    /// The `{}` placeholder is replaced with the session ID at runtime.
    ///
    /// Default: "--resume {}"
    pub session_flag: String,
    /// Whether CCS can run workflow tools (git commit, etc.).
    pub can_commit: bool,
}

impl Default for CcsConfig {
    fn default() -> Self {
        Self {
            output_flag: "--output-format=stream-json".to_string(),
            // Default to unattended automation (config can override to disable).
            yolo_flag: "--dangerously-skip-permissions".to_string(),
            verbose_flag: "--verbose".to_string(),
            print_flag: "--print".to_string(),
            streaming_flag: "--include-partial-messages".to_string(),
            json_parser: "claude".to_string(),
            session_flag: "--resume {}".to_string(),
            can_commit: true,
        }
    }
}

/// Per-alias CCS configuration (table form).
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct CcsAliasConfig {
    /// Base CCS command to run (e.g., "ccs work", "ccs gemini").
    pub cmd: String,
    /// Optional output flag override for this alias. Use "" to disable.
    pub output_flag: Option<String>,
    /// Optional yolo flag override for this alias. Use "" to enable/disable explicitly.
    pub yolo_flag: Option<String>,
    /// Optional verbose flag override for this alias. Use "" to disable.
    pub verbose_flag: Option<String>,
    /// Optional print flag override for this alias (e.g., "-p" for Claude/CCS).
    pub print_flag: Option<String>,
    /// Optional streaming flag override for this alias (e.g., "--include-partial-messages").
    pub streaming_flag: Option<String>,
    /// Optional JSON parser override (e.g., "claude", "generic").
    pub json_parser: Option<String>,
    /// Optional `can_commit` override for this alias.
    pub can_commit: Option<bool>,
    /// Optional model flag appended to the command.
    pub model_flag: Option<String>,
    /// Optional session continuation flag (e.g., "--resume {}" for Claude CLI).
    /// The "{}" placeholder is replaced with the session ID.
    pub session_flag: Option<String>,
}

/// CCS alias entry supports both shorthand string and table form.
#[derive(Debug, Clone, Deserialize, serde::Serialize)]
#[serde(untagged)]
pub enum CcsAliasToml {
    Command(String),
    Config(CcsAliasConfig),
}

impl CcsAliasToml {
    #[must_use]
    pub fn as_config(&self) -> CcsAliasConfig {
        match self {
            Self::Command(cmd) => CcsAliasConfig {
                cmd: cmd.clone(),
                ..CcsAliasConfig::default()
            },
            Self::Config(cfg) => cfg.clone(),
        }
    }
}

// =============================================================================
// Agent Configuration
// =============================================================================

/// Agent TOML configuration (compatible with `examples/agents.toml`).
///
/// Fields are used via serde deserialization.
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct AgentConfigToml {
    /// Base command to run the agent.
    ///
    /// When overriding a built-in agent, this may be omitted to keep the built-in command.
    pub cmd: Option<String>,
    /// Output-format flag.
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables output flag.
    pub output_flag: Option<String>,
    /// Flag for autonomous mode.
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables yolo mode.
    pub yolo_flag: Option<String>,
    /// Flag for verbose output.
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables verbose flag.
    pub verbose_flag: Option<String>,
    /// Print/non-interactive mode flag (e.g., "-p" for Claude/CCS).
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables print mode.
    pub print_flag: Option<String>,
    /// Include partial messages flag for streaming with -p (e.g., "--include-partial-messages").
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables streaming flag.
    pub streaming_flag: Option<String>,
    /// Session continuation flag template (e.g., "-s {}" for `OpenCode`, "--resume {}" for Claude).
    /// The `{}` placeholder is replaced with the session ID at runtime.
    ///
    /// Omitted means "keep built-in default". Empty string explicitly disables session continuation.
    /// See agent documentation for correct flag format:
    /// - Claude: --resume <`session_id`> (from `claude --help`)
    /// - `OpenCode`: -s <`session_id`> (from `opencode run --help`)
    pub session_flag: Option<String>,
    /// Whether the agent can run git commit.
    ///
    /// Omitted means "keep built-in default". For new agents, this defaults to true when omitted.
    pub can_commit: Option<bool>,
    /// Which JSON parser to use.
    ///
    /// Omitted means "keep built-in default". For new agents, defaults to "generic" when omitted.
    pub json_parser: Option<String>,
    /// Model/provider flag.
    pub model_flag: Option<String>,
    /// Human-readable display name for UI/UX.
    ///
    /// Omitted means "keep built-in default". Empty string explicitly clears the display name.
    pub display_name: Option<String>,
}

// =============================================================================
// Unified Configuration
// =============================================================================

// =============================================================================
// Agent Drain Error
// =============================================================================

/// Error returned by [`UnifiedConfig::resolve_agent_drains_checked`].
///
/// Each variant preserves the original human-facing guidance text via [`Display`].
#[derive(Debug)]
pub enum ResolveDrainError {
    /// `[agent_chain]` has conflicting named-key definitions with `[agent_chains]`.
    ConflictingLegacyChainNames { names: Vec<String> },
    /// `[agent_drains]` found alongside the singular `[agent_chain]` key; probably meant `[agent_chains]`.
    SingularAgentChainWithDrains,
    /// Legacy `[agent_chain]` role bindings cannot be combined with the named schema.
    LegacyRoleCombinedWithNamedSchema,
    /// A key in `agent_drains` is not a recognised built-in drain.
    UnknownBuiltinDrain { drain_name: String },
    /// A value in `agent_drains` references a chain absent from `agent_chains`.
    UnknownChainReference {
        drain_name: String,
        chain_name: String,
    },
    /// After iterative default-resolution some built-in drains remain unbound.
    MissingBuiltinCoverage { missing: String },
    /// A built-in drain resolves to an empty agent list via its named chain.
    EmptyChainBinding { drain: String, chain: String },
}

impl std::fmt::Display for ResolveDrainError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ConflictingLegacyChainNames { names } => write!(
                f,
                "conflicting agent chain definitions in [agent_chain] and [agent_chains] for: {};                  remove the duplicate legacy definitions and keep the canonical agent_chains/agent_drains config                  ([agent_chains]/[agent_drains])",
                names.join(", ")
            ),
            Self::SingularAgentChainWithDrains => write!(
                f,
                "found [agent_drains] with singular [agent_chain]; did you mean [agent_chains]?                  Move retry/backoff settings to [general]                  (max_retries, retry_delay_ms, backoff_multiplier, max_backoff_ms, max_cycles)"
            ),
            Self::LegacyRoleCombinedWithNamedSchema => write!(
                f,
                "deprecated legacy [agent_chain] role bindings cannot be combined with the canonical                  agent_chains/agent_drains schema; migrate agent lists to [agent_chains] + [agent_drains]                  and move retry/backoff settings to [general]                  (max_retries, retry_delay_ms, backoff_multiplier, max_backoff_ms, max_cycles)"
            ),
            Self::UnknownBuiltinDrain { drain_name } => {
                write!(f, "agent_drains.{drain_name} is not a built-in drain")
            }
            Self::UnknownChainReference {
                drain_name,
                chain_name,
            } => write!(
                f,
                "agent_drains.{drain_name} references unknown chain '{chain_name}'"
            ),
            Self::MissingBuiltinCoverage { missing } => write!(
                f,
                "agent_drains does not resolve all built-in drains; missing bindings for: {missing}"
            ),
            Self::EmptyChainBinding { drain, chain } => write!(
                f,
                "agent_drains.{drain} must not resolve to an empty chain (chain '{chain}')"
            ),
        }
    }
}

impl std::error::Error for ResolveDrainError {}

impl ResolveDrainError {
    /// Helper used by legacy integration assertions that expect `contains`.
    #[must_use]
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }
}

/// Unified configuration file structure.
///
/// This is the sole source of truth for Ralph configuration,
/// located at `~/.config/ralph-workflow.toml`.
#[derive(Debug, Clone, Deserialize, serde::Serialize, Default)]
#[serde(default)]
pub struct UnifiedConfig {
    /// General settings.
    pub general: GeneralConfig,
    /// CCS defaults for aliases.
    pub ccs: CcsConfig,
    /// Agent definitions (used via serde deserialization for future expansion).
    #[serde(default)]
    pub agents: HashMap<String, AgentConfigToml>,
    /// CCS alias mappings.
    #[serde(default)]
    pub ccs_aliases: CcsAliases,
    /// Named reusable chain definitions.
    #[serde(default)]
    pub agent_chains: HashMap<String, Vec<String>>,
    /// Drain-to-chain bindings for the built-in drains.
    #[serde(default)]
    pub agent_drains: HashMap<String, String>,
    /// Legacy role-keyed agent chain configuration.
    ///
    /// This is retained only so validation can produce an explicit migration
    /// error instead of silently ignoring the removed schema.
    #[serde(default, rename = "agent_chain")]
    pub agent_chain: Option<FallbackConfig>,
}

impl UnifiedConfig {
    /// Resolve configuration into explicit built-in drain bindings.
    #[must_use]
    pub fn resolve_agent_drains(&self) -> Option<ResolvedDrainConfig> {
        self.resolve_agent_drains_checked().ok().flatten()
    }

    /// Resolve configuration into explicit built-in drain bindings with diagnostics.
    ///
    /// # Errors
    ///
    /// Returns an error when the named-chain schema is internally inconsistent,
    /// such as invalid drain references, mixed legacy/new chain bindings, or
    /// missing coverage for built-in drains after default resolution. A
    /// metadata-only legacy `agent_chain` section is still accepted so named
    /// drains can reuse provider fallback and retry settings.
    pub fn resolve_agent_drains_checked(
        &self,
    ) -> Result<Option<ResolvedDrainConfig>, ResolveDrainError> {
        if self.agent_chain.is_some()
            && !self.agent_drains.is_empty()
            && self.agent_chains.is_empty()
        {
            return Err(ResolveDrainError::SingularAgentChainWithDrains);
        }

        if !self.agent_chains.is_empty() || !self.agent_drains.is_empty() {
            if self
                .agent_chain
                .as_ref()
                .is_some_and(crate::agents::fallback::FallbackConfig::uses_legacy_role_schema)
            {
                return Err(agent_chain_migration_error(self));
            }

            let bindings: HashMap<AgentDrain, ResolvedDrainBinding> = self
                .agent_drains
                .iter()
                .map(|(drain_name, chain_name)| {
                    let drain = AgentDrain::from_name(drain_name).ok_or_else(|| {
                        ResolveDrainError::UnknownBuiltinDrain {
                            drain_name: drain_name.clone(),
                        }
                    })?;
                    let agents = self.agent_chains.get(chain_name).ok_or_else(|| {
                        ResolveDrainError::UnknownChainReference {
                            drain_name: drain_name.clone(),
                            chain_name: chain_name.clone(),
                        }
                    })?;
                    Ok::<_, ResolveDrainError>((
                        drain,
                        ResolvedDrainBinding {
                            chain_name: chain_name.clone(),
                            agents: agents.clone(),
                        },
                    ))
                })
                .collect::<Result<HashMap<_, _>, _>>()?;

            let all_drains = AgentDrain::all();
            let bindings = (0..all_drains.len()).try_fold(bindings, |current_bindings, _| {
                let unresolved: Vec<AgentDrain> = all_drains
                    .iter()
                    .filter(|drain| !current_bindings.contains_key(drain))
                    .cloned()
                    .collect();

                if unresolved.is_empty() {
                    return Ok(current_bindings);
                }

                let new_bindings: HashMap<AgentDrain, ResolvedDrainBinding> = unresolved
                    .iter()
                    .filter_map(|drain| {
                        default_chain_binding_for_drain(self, &current_bindings, *drain)
                            .map(|binding| (*drain, binding))
                    })
                    .collect();

                let resolved_any = !new_bindings.is_empty();

                if !resolved_any {
                    let missing = unresolved
                        .iter()
                        .map(|drain| drain.as_str())
                        .collect::<Vec<_>>()
                        .join(", ");
                    return Err(ResolveDrainError::MissingBuiltinCoverage { missing });
                }

                let next_bindings: HashMap<AgentDrain, ResolvedDrainBinding> = current_bindings
                    .clone()
                    .into_iter()
                    .chain(new_bindings)
                    .collect();

                Ok(next_bindings)
            })?;

            let all_have_valid_bindings = AgentDrain::all().iter().all(|drain| {
                bindings
                    .get(drain)
                    .is_some_and(|binding| !binding.agents.is_empty())
            });

            if !all_have_valid_bindings {
                let all_drains = AgentDrain::all();
                let invalid_drain = all_drains
                    .iter()
                    .find(|drain| {
                        bindings
                            .get(drain)
                            .is_none_or(|binding| binding.agents.is_empty())
                    })
                    .expect(
                        "at least one drain must be invalid since all_have_valid_bindings is false",
                    );

                let drain_name = invalid_drain.as_str().to_string();
                return Err(
                    if bindings
                        .get(invalid_drain)
                        .is_none_or(|b| b.agents.is_empty())
                    {
                        ResolveDrainError::EmptyChainBinding {
                            drain: drain_name,
                            chain: bindings
                                .get(invalid_drain)
                                .map(|b| b.chain_name.clone())
                                .unwrap_or_default(),
                        }
                    } else {
                        ResolveDrainError::MissingBuiltinCoverage {
                            missing: drain_name,
                        }
                    },
                );
            }

            let provider_fallback = if self.general.provider_fallback.is_empty() {
                self.agent_chain
                    .as_ref()
                    .map_or_else(HashMap::new, |legacy| legacy.provider_fallback.clone())
            } else {
                self.general.provider_fallback.clone()
            };

            return Ok(Some(ResolvedDrainConfig {
                bindings,
                provider_fallback,
                max_retries: self.general.max_retries,
                retry_delay_ms: self.general.retry_delay_ms,
                backoff_multiplier: self.general.backoff_multiplier,
                max_backoff_ms: self.general.max_backoff_ms,
                max_cycles: self.general.max_cycles,
            }));
        }

        Ok(self
            .agent_chain
            .as_ref()
            .filter(|fallback| fallback.uses_legacy_role_schema())
            .map(|fallback| {
                let resolved = fallback.resolve_drains();

                if !self.general.provider_fallback.is_empty() {
                    ResolvedDrainConfig {
                        bindings: resolved.bindings,
                        provider_fallback: self.general.provider_fallback.clone(),
                        max_retries: self.general.max_retries,
                        retry_delay_ms: self.general.retry_delay_ms,
                        backoff_multiplier: self.general.backoff_multiplier,
                        max_backoff_ms: self.general.max_backoff_ms,
                        max_cycles: self.general.max_cycles,
                    }
                } else {
                    ResolvedDrainConfig {
                        bindings: resolved.bindings,
                        provider_fallback: resolved.provider_fallback,
                        max_retries: self.general.max_retries,
                        retry_delay_ms: self.general.retry_delay_ms,
                        backoff_multiplier: self.general.backoff_multiplier,
                        max_backoff_ms: self.general.max_backoff_ms,
                        max_cycles: self.general.max_cycles,
                    }
                }
            }))
    }
}

fn agent_chain_migration_error(config: &UnifiedConfig) -> ResolveDrainError {
    let conflicting_legacy_names = conflicting_legacy_chain_names(config);
    if !conflicting_legacy_names.is_empty() {
        return ResolveDrainError::ConflictingLegacyChainNames {
            names: conflicting_legacy_names
                .into_iter()
                .map(String::from)
                .collect(),
        };
    }

    if !config.agent_drains.is_empty() && config.agent_chains.is_empty() {
        ResolveDrainError::SingularAgentChainWithDrains
    } else {
        ResolveDrainError::LegacyRoleCombinedWithNamedSchema
    }
}

fn conflicting_legacy_chain_names(config: &UnifiedConfig) -> Vec<&'static str> {
    ["developer", "reviewer", "commit", "analysis"]
        .into_iter()
        .filter(|&name| {
            config.agent_chain.as_ref().is_some_and(|fallback| {
                let chain = match name {
                    "developer" => &fallback.developer,
                    "reviewer" => &fallback.reviewer,
                    "commit" => &fallback.commit,
                    "analysis" => &fallback.analysis,
                    _ => return false,
                };
                !chain.is_empty()
            }) && config.agent_chains.contains_key(name)
        })
        .collect()
}

fn default_chain_binding_for_drain(
    config: &UnifiedConfig,
    bindings: &HashMap<AgentDrain, ResolvedDrainBinding>,
    drain: AgentDrain,
) -> Option<ResolvedDrainBinding> {
    let explicit_drain_binding = drain_specific_chain_names_for_drain(drain)
        .iter()
        .find_map(|&chain_name| resolve_named_chain_binding(config, chain_name));

    let sibling_binding = fallback_source_drains_for_drain(drain)
        .iter()
        .find_map(|source| bindings.get(source).cloned());

    let legacy_role_binding = legacy_role_chain_names_for_drain(drain)
        .iter()
        .find_map(|&chain_name| resolve_named_chain_binding(config, chain_name));

    explicit_drain_binding
        .or(sibling_binding)
        .or(legacy_role_binding)
}

fn resolve_named_chain_binding(
    config: &UnifiedConfig,
    chain_name: &str,
) -> Option<ResolvedDrainBinding> {
    config
        .agent_chains
        .get(chain_name)
        .map(|agents| ResolvedDrainBinding {
            chain_name: chain_name.to_string(),
            agents: agents.clone(),
        })
}

const fn drain_specific_chain_names_for_drain(drain: AgentDrain) -> &'static [&'static str] {
    match drain {
        AgentDrain::Planning => &["planning"],
        AgentDrain::Development => &["development"],
        AgentDrain::Review => &["review"],
        AgentDrain::Fix => &["fix"],
        AgentDrain::Commit => &["commit"],
        AgentDrain::Analysis => &["analysis"],
    }
}

const fn legacy_role_chain_names_for_drain(drain: AgentDrain) -> &'static [&'static str] {
    match drain {
        AgentDrain::Planning | AgentDrain::Development | AgentDrain::Analysis => &["developer"],
        AgentDrain::Review | AgentDrain::Fix | AgentDrain::Commit => &["reviewer"],
    }
}

const fn fallback_source_drains_for_drain(drain: AgentDrain) -> &'static [AgentDrain] {
    match drain {
        AgentDrain::Planning => &[AgentDrain::Development],
        AgentDrain::Development => &[AgentDrain::Planning],
        AgentDrain::Review => &[AgentDrain::Fix],
        AgentDrain::Fix => &[AgentDrain::Review],
        AgentDrain::Commit => &[AgentDrain::Review, AgentDrain::Fix],
        AgentDrain::Analysis => &[AgentDrain::Development, AgentDrain::Planning],
    }
}
