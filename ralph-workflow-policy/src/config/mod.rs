//! Policy configuration types.
//!
//! Contains TOML-deserializable types for orchestration policy:
//! drain binding configuration and orchestration behavior flags.

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
}

impl ResolveDrainError {
    /// Helper used by legacy integration assertions that expect `contains`.
    #[must_use]
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }
}

// =============================================================================
// Pipeline Configuration
// =============================================================================

/// Deserialized from .agent/pipeline.toml (system-defined orchestration policy).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PipelineConfig {
    #[serde(default)]
    pub top_level_phases: TopLevelPhasesConfig,
    #[serde(default)]
    pub embedded_decision_points: EmbeddedDecisionPointsConfig,
    #[serde(default)]
    pub decision_routes: DecisionRoutesConfig,
    #[serde(default)]
    pub budgets: BudgetConfig,
    #[serde(default)]
    pub artifact_acceptance: ArtifactAcceptanceConfig,
    #[serde(default)]
    pub validation: PipelineValidationConfig,
    #[serde(default)]
    pub parallel_execution: ParallelExecutionConfig,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct TopLevelPhasesConfig {
    #[serde(default)]
    pub sequence: Vec<String>,
    #[serde(default)]
    pub recovery_phase: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct EmbeddedDecisionPointsConfig {
    #[serde(default)]
    pub development: Vec<String>,
    #[serde(default)]
    pub review: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct DecisionRoutesConfig {
    #[serde(default)]
    pub development_analysis: Vec<String>,
    #[serde(default)]
    pub fix_analysis: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct BudgetConfig {
    #[serde(default = "default_max_development_cycles")]
    pub max_development_cycles: u32,
    #[serde(default = "default_max_review_cycles")]
    pub max_review_cycles: u32,
    #[serde(default = "default_max_dev_continuations")]
    pub max_dev_continuations: u32,
    #[serde(default = "default_max_fix_continuations")]
    pub max_fix_continuations: u32,
    #[serde(default = "default_loop_detection_threshold")]
    pub loop_detection_threshold: u32,
}

const fn default_max_development_cycles() -> u32 { 5 }
const fn default_max_review_cycles() -> u32 { 2 }
const fn default_max_dev_continuations() -> u32 { 3 }
const fn default_max_fix_continuations() -> u32 { 10 }
const fn default_loop_detection_threshold() -> u32 { 100 }

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ArtifactAcceptanceConfig {
    #[serde(default = "bool_true")]
    pub require_current_run_identity: bool,
    #[serde(default = "bool_true")]
    pub require_current_drain_identity: bool,
    #[serde(default = "bool_true")]
    pub require_current_namespace_when_present: bool,
}

const fn bool_true() -> bool { true }

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct PipelineValidationConfig {
    #[serde(default = "bool_true")]
    pub require_explicit_drain_bindings: bool,
    #[serde(default = "bool_true")]
    pub forbid_sibling_drain_inference: bool,
    #[serde(default = "bool_true")]
    pub preserve_runtime_execution_order: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ParallelExecutionConfig {
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default = "bool_true")]
    pub require_namespaces: bool,
    #[serde(default = "bool_true")]
    pub require_directory_scopes: bool,
    #[serde(default = "bool_true")]
    pub dispatch_remains_runtime_owned: bool,
    #[serde(default = "default_max_concurrent_agents")]
    pub max_concurrent_agents: u32,
    #[serde(default = "default_default_concurrent_agents")]
    pub default_concurrent_agents: u32,
}

const fn default_max_concurrent_agents() -> u32 { 20 }
const fn default_default_concurrent_agents() -> u32 { 5 }

// =============================================================================
// Artifacts Configuration
// =============================================================================

/// Deserialized from .agent/artifacts.toml (system-defined artifact contracts per drain).
#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ArtifactsConfig {
    #[serde(default)]
    pub planning: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub development: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub analysis: Option<AnalysisDrainArtifactConfig>,
    #[serde(default)]
    pub review: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub fix: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub commit: Option<DrainArtifactConfig>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DrainArtifactConfig {
    pub artifact_type: String,
    #[serde(default)]
    pub required_sections: Vec<String>,
    pub submission_mode: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisDrainArtifactConfig {
    pub artifact_type: String,
    pub submission_mode: String,
    #[serde(default)]
    pub required_decision_outcomes: Vec<String>,
}

// =============================================================================
// Drain Invariants
// =============================================================================

/// System-enforced immutable properties for each built-in drain.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DrainInvariant {
    pub role: &'static str,
    pub policy_mode: &'static str,
    pub drain_class: &'static str,
    pub artifact_type: &'static str,
    pub write_capable: bool,
}

/// Canonical invariants for each built-in drain.
///
/// Role names match the identifiers returned by `AgentRole::to_str()`:
/// "developer" for the development drain, "reviewer" for the review drain.
pub const DRAIN_INVARIANTS: &[(&str, DrainInvariant)] = &[
    ("planning",    DrainInvariant { role: "planning",  policy_mode: "read_only", drain_class: "planning", artifact_type: "plan",               write_capable: false }),
    ("development", DrainInvariant { role: "developer", policy_mode: "dev",       drain_class: "dev",      artifact_type: "development_result", write_capable: true  }),
    ("analysis",    DrainInvariant { role: "analysis",  policy_mode: "read_only", drain_class: "planning", artifact_type: "analysis_decision",  write_capable: false }),
    ("review",      DrainInvariant { role: "reviewer",  policy_mode: "read_only", drain_class: "review",   artifact_type: "issues",             write_capable: false }),
    ("fix",         DrainInvariant { role: "fix",       policy_mode: "fixer",     drain_class: "fixer",    artifact_type: "fix_result",         write_capable: true  }),
    ("commit",      DrainInvariant { role: "commit",    policy_mode: "commit",    drain_class: "commit",   artifact_type: "commit_message",     write_capable: true  }),
];

/// Look up the invariant for a built-in drain by name.
#[must_use]
pub fn drain_invariant(drain_name: &str) -> Option<&'static DrainInvariant> {
    DRAIN_INVARIANTS
        .iter()
        .find(|(name, _)| *name == drain_name)
        .map(|(_, inv)| inv)
}

// =============================================================================
// Worker Contract Configuration
// =============================================================================

/// Contract for parallel worker execution.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerContractConfig {
    /// The drain that is permitted to spawn this worker type (must be "development").
    pub parent_drain: String,
    /// Max concurrent workers for this contract.
    pub max_workers: u32,
    /// Whether workers must declare a namespace.
    #[serde(default = "bool_true")]
    pub require_namespace: bool,
    /// Whether workers must declare allowed_directories.
    #[serde(default = "bool_true")]
    pub require_directory_scope: bool,
    /// Merge strategy for rejoining worker artifacts.
    pub merge_strategy: String,
}

// =============================================================================
// Artifact Identity
// =============================================================================

/// The expected identity that an accepted artifact must match.
#[derive(Debug, Clone)]
pub struct ArtifactIdentity {
    pub run_id: String,
    pub drain: &'static str,
    pub artifact_type: &'static str,
    pub namespace: Option<String>,
}
