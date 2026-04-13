//! Drain configuration and resolution error types.
//!
//! Contains per-drain chain binding config, orchestration policy flags,
//! and the `ResolveDrainError` enum produced when drain resolution fails.

use serde::{Deserialize, Serialize};

// =============================================================================
// Drain Configuration
// =============================================================================

/// Per-drain chain binding in TOML.
///
/// Supports two forms for backward compatibility:
/// - Flat string: `planning = "planner"` → `DrainConfigToml::Chain("planner")`
/// - Table form: `[agent_drains.planning]\nchain = "planner"` → `DrainConfigToml::Config { chain: "planner" }`
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(untagged)]
pub enum DrainConfigToml {
    /// Flat string form (backward compatible): `planning = "developer"`
    Chain(String),
    /// Table form: `[agent_drains.planning]\nchain = "developer"`
    Config(DrainConfigTable),
}

impl DrainConfigToml {
    /// Extract the chain name regardless of form.
    #[must_use]
    pub fn chain_name(&self) -> &str {
        match self {
            Self::Chain(name) => name.as_str(),
            Self::Config(cfg) => cfg.chain.as_str(),
        }
    }
}

/// Table form of per-drain chain configuration.
#[derive(Debug, Clone, Deserialize, Serialize, Default)]
pub struct DrainConfigTable {
    /// Chain name to use for this drain.
    pub chain: String,
}

// =============================================================================
// Orchestration Configuration
// =============================================================================

/// Orchestration policy configuration.
///
/// Controls startup validation rules and drain resolution behavior.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct OrchestrationConfig {
    /// When true (the default), disables the two permissive fallback tiers
    /// in drain resolution:
    ///
    /// - Tier 2: sibling-drain inference (planning ↔ development, review ↔ fix, etc.)
    /// - Tier 3: legacy role-family chain lookup (developer, reviewer chains)
    ///
    /// With this flag enabled, every built-in drain must either be explicitly
    /// bound via `agent_drains` OR resolve via a chain named exactly after the
    /// drain (tier 1). Missing drains are rejected at load time.
    ///
    /// Default: `true` — explicit bindings are required.
    pub forbid_sibling_drain_inference: bool,

    /// When true, require every built-in drain to have an explicit chain
    /// binding in `agent_drains`. Drains that can only be resolved via
    /// tier-1 chain-name matching (but not via an explicit binding) still
    /// satisfy this flag if `forbid_sibling_drain_inference` is also true.
    ///
    /// Default: `true` — every built-in drain must be explicitly bound in `agent_drains`.
    pub require_explicit_drain_bindings: bool,
}

impl Default for OrchestrationConfig {
    fn default() -> Self {
        Self {
            forbid_sibling_drain_inference: true,
            require_explicit_drain_bindings: true,
        }
    }
}

// =============================================================================
// Drain Resolution Errors
// =============================================================================

/// Error returned when agent drain resolution fails during config validation.
///
/// Each variant preserves the original human-facing guidance text via `Display`.
#[derive(Debug, thiserror::Error)]
pub enum ResolveDrainError {
    /// `[agent_chain]` has conflicting named-key definitions with `[agent_chains]`.
    #[error(
        "conflicting agent chain definitions in [agent_chain] and [agent_chains] for: {names}; \
         remove the duplicate legacy definitions and keep the canonical agent_chains/agent_drains config \
         ([agent_chains]/[agent_drains])",
        names = names.join(", ")
    )]
    ConflictingLegacyChainNames { names: Vec<String> },

    /// `[agent_drains]` found alongside the singular `[agent_chain]` key; probably meant `[agent_chains]`.
    #[error(
        "found [agent_drains] with singular [agent_chain]; did you mean [agent_chains]? \
         Move retry/backoff settings to [general] \
         (max_retries, retry_delay_ms, backoff_multiplier, max_backoff_ms, max_cycles)"
    )]
    SingularAgentChainWithDrains,

    /// Legacy `[agent_chain]` role bindings cannot be combined with the named schema.
    #[error(
        "deprecated legacy [agent_chain] role bindings cannot be combined with the canonical \
         agent_chains/agent_drains schema; migrate agent lists to [agent_chains] + [agent_drains] \
         and move retry/backoff settings to [general] \
         (max_retries, retry_delay_ms, backoff_multiplier, max_backoff_ms, max_cycles)"
    )]
    LegacyRoleCombinedWithNamedSchema,

    /// A key in `agent_drains` is not a recognised built-in drain.
    #[error("agent_drains.{drain_name} is not a built-in drain")]
    UnknownBuiltinDrain { drain_name: String },

    /// A value in `agent_drains` references a chain absent from `agent_chains`.
    #[error("agent_drains.{drain_name} references unknown chain '{chain_name}'")]
    UnknownChainReference {
        drain_name: String,
        chain_name: String,
    },

    /// After iterative default-resolution some built-in drains remain unbound.
    #[error("agent_drains does not resolve all built-in drains; missing bindings for: {missing}")]
    MissingBuiltinCoverage { missing: String },

    /// A built-in drain resolves to an empty agent list via its named chain.
    #[error("agent_drains.{drain} must not resolve to an empty chain (chain '{chain}')")]
    EmptyChainBinding { drain: String, chain: String },

    /// Drain resolution was blocked by `forbid_sibling_drain_inference = true`.
    ///
    /// Includes:
    /// - `drain_name`: the drain that failed to resolve
    /// - `attempted_tier`: the fallback tier (2 = sibling drain, 3 = legacy role-family) that
    ///   would have resolved the drain when the restriction is relaxed
    /// - `toml_fix_hint`: the TOML snippet to add under `[agent_drains]` to fix the issue
    #[error(
        "drain '{drain_name}' has no explicit chain binding and implicit inference is disabled \
         (forbid_sibling_drain_inference = true); tier {attempted_tier} fallback was blocked — \
         fix: add `{toml_fix_hint}` under [agent_drains] in your config"
    )]
    ImplicitInferenceDisabled {
        drain_name: String,
        /// Tier that would have resolved this drain (2 = sibling-drain, 3 = legacy role-family).
        attempted_tier: u8,
        /// Minimal TOML key/value to add under [agent_drains] to fix the missing binding.
        toml_fix_hint: String,
    },
}

impl ResolveDrainError {
    /// Helper used by legacy integration assertions that expect `contains`.
    #[must_use]
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }
}
