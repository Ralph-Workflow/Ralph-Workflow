//! Policy configuration types.
//!
//! Contains TOML-deserializable types for orchestration policy:
//! drain binding configuration, orchestration behavior flags, phase definitions,
//! artifact contracts, analysis decision enums, and the compiled normative defaults.

mod drain;

pub use drain::{DrainConfigTable, DrainConfigToml, OrchestrationConfig, ResolveDrainError};

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// =============================================================================
// Pipeline Configuration
// =============================================================================

/// Deserialized from .agent/pipeline.toml (system-defined orchestration policy).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PipelineConfig {
    /// Phase document paths to load (relative to the config root).
    #[serde(default)]
    pub phase_documents: Vec<String>,
    /// Optional user-authored phase documents (same schema as shipped defaults).
    #[serde(default)]
    pub extra_phase_documents: Vec<String>,
    #[serde(default)]
    pub top_level_phases: TopLevelPhasesConfig,
    #[serde(default)]
    pub cycle_accounting: CycleAccountingConfig,
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
    #[serde(default)]
    pub phase_side_effects: HashMap<String, PhaseSideEffectConfig>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct TopLevelPhasesConfig {
    /// Legacy flat sequence field (kept for backward compatibility).
    #[serde(default)]
    pub sequence: Vec<String>,
    /// Normative default sequence from the configurable-orchestration plan.
    #[serde(default)]
    pub default_sequence: Vec<String>,
    #[serde(default)]
    pub recovery_phase: Option<String>,
}

/// Cycle accounting policy: when do cycle counters advance and what are the
/// trigger phases for each counter?
#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct CycleAccountingConfig {
    /// The phase that triggers development counter increment.
    #[serde(default)]
    pub development_counter_increment_phase: Option<String>,
    /// Condition describing when the development counter increments.
    #[serde(default)]
    pub development_counter_increment_when: Option<String>,
    /// The phase that triggers review counter increment.
    #[serde(default)]
    pub review_counter_increment_phase: Option<String>,
    /// Condition describing when the review counter increments.
    #[serde(default)]
    pub review_counter_increment_when: Option<String>,
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

const fn default_max_development_cycles() -> u32 {
    5
}
const fn default_max_review_cycles() -> u32 {
    2
}
const fn default_max_dev_continuations() -> u32 {
    3
}
const fn default_max_fix_continuations() -> u32 {
    10
}
const fn default_loop_detection_threshold() -> u32 {
    100
}

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

const fn bool_true() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct PipelineValidationConfig {
    #[serde(default = "bool_true")]
    pub require_explicit_drain_bindings: bool,
    #[serde(default = "bool_true")]
    pub forbid_sibling_drain_inference: bool,
    #[serde(default = "bool_true")]
    pub preserve_runtime_execution_order: bool,
    /// Reject pipeline.toml entries in phase_documents that do not load to a valid phase.
    #[serde(default = "bool_true")]
    pub reject_unbound_phase_documents: bool,
    /// Reject transition rules that reference unknown phases or drains.
    #[serde(default = "bool_true")]
    pub reject_unbound_phase_transitions: bool,
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

const fn default_max_concurrent_agents() -> u32 {
    20
}
const fn default_default_concurrent_agents() -> u32 {
    5
}

/// A policy-visible side effect declared in pipeline.toml under [phase_side_effects.*].
///
/// Each side effect is identified by a string key that phase TOML files reference
/// via `side_effects = ["..."]`.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhaseSideEffectConfig {
    /// The MCP artifact type involved in this side effect.
    pub artifact_type: String,
    /// Output path for the artifact (submit_mcp_artifact mode).
    #[serde(default)]
    pub output_path: Option<String>,
    /// Submission mode (e.g. "submit_mcp_artifact").
    #[serde(default)]
    pub mode: Option<String>,
    /// Action to take after artifact submission (e.g. "apply_commit").
    #[serde(default)]
    pub action: Option<String>,
}

// =============================================================================
// Phase Definition
// =============================================================================

/// A phase definition loaded from a phases/*.toml file.
///
/// Phase definitions are the normative description of how each phase in the
/// orchestration graph behaves: which drain it uses, which template it renders,
/// which artifact it expects, and which transitions are legal.
///
/// Shipped defaults and user-authored phases use the same schema.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PhaseDefinition {
    /// Canonical phase identifier (e.g., "planning", "development_commit").
    pub phase_id: String,
    /// Whether this phase is a shipped default or user-authored.
    pub origin: PhaseOrigin,
    /// The built-in drain this phase binds to (e.g., "planning", "commit").
    pub drain: String,
    /// Lookup key into artifacts.toml for the prompt template.
    pub template_key: String,
    /// Lookup key into artifacts.toml for the artifact contract.
    pub artifact_profile: String,
    /// Phases that follow this phase on the happy path (no embedded decision points).
    #[serde(default)]
    pub next: Vec<String>,
    /// Names of embedded analysis/decision points inside this phase.
    #[serde(default)]
    pub embedded_decision_points: Vec<String>,
    /// template_key values for each embedded decision point (keyed by decision-point name).
    #[serde(default)]
    pub decision_point_template_keys: HashMap<String, String>,
    /// Names of sub-drains in this phase's subflow (e.g., fix phases include fix + review_analysis).
    #[serde(default)]
    pub subflow: Vec<String>,
    /// template_key values for each subflow step.
    #[serde(default)]
    pub subflow_template_keys: HashMap<String, String>,
    /// Transition rules from embedded decision points.
    #[serde(default)]
    pub transitions: Vec<TransitionRule>,
    /// Post-commit routing rules (evaluated after the cycle counter advances at this phase).
    #[serde(default)]
    pub post_commit_routes: Vec<PostCommitRoute>,
    /// Side-effect keys from pipeline.toml [phase_side_effects.*] that Ralph executes at this phase.
    #[serde(default)]
    pub side_effects: Vec<String>,
    /// Artifact type produced/applied by this phase's side effects.
    #[serde(default)]
    pub side_effect_artifact: Option<String>,
    /// Workspace path for the side-effect artifact.
    #[serde(default)]
    pub side_effect_artifact_path: Option<String>,
}

/// Origin of a phase definition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PhaseOrigin {
    /// A phase shipped as part of the ralph-workflow-policy defaults.
    ShippedDefault,
    /// A phase authored by the project/user in extra_phase_documents.
    UserDefined,
}

/// A single transition rule inside a phase definition.
///
/// Transition rules map embedded-decision-point outcomes (identified by the
/// decision-point name and MCP status field value) to the next phase(s).
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransitionRule {
    /// The condition that triggers this transition.
    pub when: TransitionCondition,
    /// The phase(s) to transition to when `when` matches.
    pub next: Vec<String>,
    /// Whether the cycle counter for the current phase increments immediately.
    ///
    /// For commit-gated cycles this is always `false`; the counter advances
    /// after `development_commit` or `review_commit`, not at the analysis step.
    #[serde(default)]
    pub increment_counter: bool,
    /// If set, increments the named counter after the named commit phase completes.
    #[serde(default)]
    pub increment_counter_after_commit: Option<String>,
}

/// The condition that triggers a `TransitionRule`.
///
/// Keyed by decision-point name and MCP artifact status field value.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransitionCondition {
    /// The embedded decision point this condition applies to.
    pub decision_point: String,
    /// The MCP artifact status field value (e.g., "completed", "partial", "failed").
    pub status: String,
}

/// A post-commit routing rule evaluated after the cycle counter advances.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PostCommitRoute {
    /// The condition that selects this route.
    pub when: PostCommitCondition,
    /// The phase(s) to route to when `when` matches.
    pub next: Vec<String>,
}

/// Condition for a `PostCommitRoute`.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PostCommitCondition {
    /// The cycle counter to evaluate (e.g., "development", "review").
    pub counter: String,
    /// The comparison operator (e.g., "less_than_budget", "budget_exhausted").
    pub comparison: String,
}

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
    pub development_analysis: Option<AnalysisDrainArtifactConfig>,
    #[serde(default)]
    pub review_analysis: Option<AnalysisDrainArtifactConfig>,
    #[serde(default)]
    pub analysis: Option<AnalysisDrainArtifactConfig>,
    #[serde(default)]
    pub review: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub fix: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub commit: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub development_commit: Option<DrainArtifactConfig>,
    #[serde(default)]
    pub review_commit: Option<DrainArtifactConfig>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DrainArtifactConfig {
    pub artifact_type: String,
    #[serde(default)]
    pub required_sections: Vec<String>,
    pub submission_mode: String,
    /// Optional: output path for the artifact (e.g., ".agent/tmp/commit_message.json").
    #[serde(default)]
    pub artifact_output_path: Option<String>,
    /// Optional: continuation template for this drain.
    #[serde(default)]
    pub continuation_template: Option<String>,
    /// Prompt template file (relative to ralph-workflow-policy/templates/).
    #[serde(default)]
    pub prompt_template: Option<String>,
    /// Allow-listed template variables for this entry.
    #[serde(default)]
    pub template_variables: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisDrainArtifactConfig {
    pub artifact_type: String,
    pub submission_mode: String,
    #[serde(default)]
    pub required_decision_outcomes: Vec<String>,
    /// MCP artifact status field name used for routing.
    #[serde(default)]
    pub status_field: Option<String>,
    /// Valid values for the status field.
    #[serde(default)]
    pub allowed_statuses: Vec<String>,
    /// The binary decision vocabulary for this analysis point.
    #[serde(default)]
    pub decision_vocabulary: Vec<String>,
    /// Prompt template file.
    #[serde(default)]
    pub prompt_template: Option<String>,
    /// Allow-listed template variables.
    #[serde(default)]
    pub template_variables: Vec<String>,
}

// =============================================================================
// Typed Analysis Decision Enums
// =============================================================================

/// Typed decision outcome from the `development_analysis` embedded decision point.
///
/// This is the binary policy vocabulary for the development cycle. The analysis
/// agent's MCP result maps to exactly one of these variants before the reducer
/// routes execution:
///
/// - `NeedsMoreWork` → loop back to `development` (stays in current cycle; counter unchanged)
/// - `CycleComplete` → route into `development_commit` (counter advances after commit)
///
/// This replaces the broader five-variant `AnalysisDecision` for the development
/// path, eliminating the ambiguous `NeedsReplanning`, `ReadyForReview`,
/// `ReadyToCommit`, and `NeedsAnotherReview` variants that carried implicit
/// cross-cycle semantics. Cross-cycle routing now happens only through the
/// post-commit guards in `phases/development.toml`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DevelopmentAnalysisDecision {
    /// Development iteration is incomplete. Loop back to `Development`.
    NeedsMoreWork,
    /// Development iteration succeeded. Proceed to `DevelopmentCommit`.
    CycleComplete,
}

impl DevelopmentAnalysisDecision {
    /// Parse from the canonical artifact-key string.
    #[must_use]
    pub fn from_artifact_key(key: &str) -> Option<Self> {
        match key {
            "needs_more_work" => Some(Self::NeedsMoreWork),
            "cycle_complete" => Some(Self::CycleComplete),
            _ => None,
        }
    }

    /// Canonical artifact-key string for this variant.
    #[must_use]
    pub fn as_artifact_key(self) -> &'static str {
        match self {
            Self::NeedsMoreWork => "needs_more_work",
            Self::CycleComplete => "cycle_complete",
        }
    }

    /// All valid artifact keys in declaration order.
    #[must_use]
    pub fn all_artifact_keys() -> &'static [&'static str] {
        &["needs_more_work", "cycle_complete"]
    }

    /// All variants in declaration order.
    #[must_use]
    pub fn all() -> &'static [Self] {
        &[Self::NeedsMoreWork, Self::CycleComplete]
    }
}

/// Typed decision outcome from the `review_analysis` embedded decision point.
///
/// This is the binary policy vocabulary for the review cycle. The analysis
/// agent's MCP result maps to exactly one of these variants before the reducer
/// routes execution:
///
/// - `NeedsMoreFix` → loop back to `fix` (stays in current cycle; counter unchanged)
/// - `CycleComplete` → route into `review_commit` (counter advances after commit)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewAnalysisDecision {
    /// Fix work is insufficient. Loop back to `Fix`.
    NeedsMoreFix,
    /// Review/fix iteration succeeded. Proceed to `ReviewCommit`.
    CycleComplete,
}

impl ReviewAnalysisDecision {
    /// Parse from the canonical artifact-key string.
    #[must_use]
    pub fn from_artifact_key(key: &str) -> Option<Self> {
        match key {
            "needs_more_fix" => Some(Self::NeedsMoreFix),
            "cycle_complete" => Some(Self::CycleComplete),
            _ => None,
        }
    }

    /// Canonical artifact-key string for this variant.
    #[must_use]
    pub fn as_artifact_key(self) -> &'static str {
        match self {
            Self::NeedsMoreFix => "needs_more_fix",
            Self::CycleComplete => "cycle_complete",
        }
    }

    /// All valid artifact keys in declaration order.
    #[must_use]
    pub fn all_artifact_keys() -> &'static [&'static str] {
        &["needs_more_fix", "cycle_complete"]
    }

    /// All variants in declaration order.
    #[must_use]
    pub fn all() -> &'static [Self] {
        &[Self::NeedsMoreFix, Self::CycleComplete]
    }
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
    (
        "planning",
        DrainInvariant {
            role: "planning",
            policy_mode: "read_only",
            drain_class: "planning",
            artifact_type: "plan",
            write_capable: false,
        },
    ),
    (
        "development",
        DrainInvariant {
            role: "developer",
            policy_mode: "dev",
            drain_class: "dev",
            artifact_type: "development_result",
            write_capable: true,
        },
    ),
    (
        "analysis",
        DrainInvariant {
            role: "analysis",
            policy_mode: "read_only",
            drain_class: "planning",
            artifact_type: "analysis_decision",
            write_capable: false,
        },
    ),
    (
        "review",
        DrainInvariant {
            role: "reviewer",
            policy_mode: "read_only",
            drain_class: "review",
            artifact_type: "issues",
            write_capable: false,
        },
    ),
    (
        "fix",
        DrainInvariant {
            role: "fix",
            policy_mode: "fixer",
            drain_class: "fixer",
            artifact_type: "fix_result",
            write_capable: true,
        },
    ),
    (
        "commit",
        DrainInvariant {
            role: "commit",
            policy_mode: "commit",
            drain_class: "commit",
            artifact_type: "commit_message",
            write_capable: true,
        },
    ),
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

// =============================================================================
// Normative Defaults Loading
// =============================================================================

/// Error returned when the normative pipeline.toml fails to parse.
///
/// This is a programming error (the shipped TOML is malformed), not a
/// user-configuration error.
#[derive(Debug, thiserror::Error)]
#[error("failed to parse built-in pipeline.toml: {source}")]
pub struct PipelineLoadError {
    #[from]
    source: toml::de::Error,
}

/// Load and parse the normative `pipeline.toml` shipped by the policy crate.
///
/// The TOML content is embedded at compile time via `include_str!`. This means
/// the normative defaults are always available without filesystem access and
/// without risk of stale/missing files at runtime.
///
/// # Errors
///
/// Returns `PipelineLoadError` if the embedded TOML is malformed. This indicates
/// a build-time defect in the policy crate, not a runtime user error.
pub fn load_pipeline() -> Result<PipelineConfig, PipelineLoadError> {
    let content = include_str!("../../pipeline.toml");
    Ok(toml::from_str(content)?)
}

/// Load and parse a `PhaseDefinition` from TOML content.
///
/// # Errors
///
/// Returns a `toml::de::Error` if the content is malformed.
pub fn parse_phase_definition(content: &str) -> Result<PhaseDefinition, toml::de::Error> {
    toml::from_str(content)
}

/// Load and parse the normative `artifacts.toml` shipped by the policy crate.
///
/// # Errors
///
/// Returns a `toml::de::Error` if the embedded TOML is malformed.
pub fn load_artifacts() -> Result<ArtifactsConfig, toml::de::Error> {
    let content = include_str!("../../artifacts.toml");
    toml::from_str(content)
}

/// Load all normative phase definitions shipped by the policy crate.
///
/// Returns the five shipped phase definitions in declaration order:
/// planning, development, review, development_commit, review_commit.
///
/// # Errors
///
/// Returns a `toml::de::Error` if any of the embedded phase TOML files are malformed.
pub fn load_shipped_phases() -> Result<Vec<PhaseDefinition>, toml::de::Error> {
    let phase_sources = [
        include_str!("../../phases/planning.toml"),
        include_str!("../../phases/development.toml"),
        include_str!("../../phases/review.toml"),
        include_str!("../../phases/development_commit.toml"),
        include_str!("../../phases/review_commit.toml"),
    ];
    phase_sources
        .iter()
        .map(|src| toml::from_str(src))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_pipeline_parses_normative_defaults() {
        let config = load_pipeline().expect("normative pipeline.toml must parse");
        assert_eq!(
            config.top_level_phases.default_sequence,
            vec![
                "planning",
                "development",
                "development_commit",
                "review",
                "review_commit"
            ],
            "default_sequence must match Section 1 target flow"
        );
        assert_eq!(config.budgets.max_development_cycles, 5);
        assert_eq!(config.budgets.max_review_cycles, 2);
        assert!(config.validation.require_explicit_drain_bindings);
        assert!(config.validation.forbid_sibling_drain_inference);
        assert_eq!(
            config.phase_documents.len(),
            5,
            "five shipped phase documents"
        );
        assert_eq!(
            config.phase_side_effects.len(),
            4,
            "four side-effect entries"
        );
    }

    #[test]
    fn load_shipped_phases_returns_five_phases() {
        let phases = load_shipped_phases().expect("all shipped phases must parse");
        assert_eq!(phases.len(), 5);
        let ids: Vec<&str> = phases.iter().map(|p| p.phase_id.as_str()).collect();
        assert_eq!(
            ids,
            vec![
                "planning",
                "development",
                "review",
                "development_commit",
                "review_commit"
            ]
        );
    }

    #[test]
    fn load_artifacts_parses_normative_defaults() {
        let artifacts = load_artifacts().expect("normative artifacts.toml must parse");
        assert!(artifacts.planning.is_some());
        assert!(artifacts.development.is_some());
        assert!(artifacts.development_analysis.is_some());
        assert!(artifacts.review_analysis.is_some());
        assert!(artifacts.review.is_some());
        assert!(artifacts.fix.is_some());
        assert!(artifacts.development_commit.is_some());
        assert!(artifacts.review_commit.is_some());
    }

    #[test]
    fn development_phase_has_required_transitions() {
        let phases = load_shipped_phases().expect("phases must parse");
        let dev = phases
            .iter()
            .find(|p| p.phase_id == "development")
            .expect("development phase");
        assert_eq!(dev.embedded_decision_points, vec!["development_analysis"]);
        assert_eq!(
            dev.transitions.len(),
            3,
            "partial/failed/completed transitions"
        );
        let completed = dev
            .transitions
            .iter()
            .find(|t| t.when.status == "completed")
            .expect("completed transition");
        assert_eq!(completed.next, vec!["development_commit"]);
        assert_eq!(
            completed.increment_counter_after_commit.as_deref(),
            Some("development")
        );
        assert_eq!(dev.post_commit_routes.len(), 2, "two post-commit routes");
    }

    #[test]
    fn review_phase_has_required_transitions() {
        let phases = load_shipped_phases().expect("phases must parse");
        let review = phases
            .iter()
            .find(|p| p.phase_id == "review")
            .expect("review phase");
        assert!(review.subflow.contains(&"fix".to_string()));
        assert!(review.subflow.contains(&"review_analysis".to_string()));
        let completed = review
            .transitions
            .iter()
            .find(|t| t.when.status == "completed")
            .expect("completed transition");
        assert_eq!(completed.next, vec!["review_commit"]);
        assert_eq!(review.post_commit_routes.len(), 2);
    }

    #[test]
    fn development_analysis_decision_roundtrip() {
        for key in DevelopmentAnalysisDecision::all_artifact_keys() {
            let decision = DevelopmentAnalysisDecision::from_artifact_key(key)
                .unwrap_or_else(|| panic!("must parse key: {key}"));
            assert_eq!(decision.as_artifact_key(), *key);
        }
    }

    #[test]
    fn review_analysis_decision_roundtrip() {
        for key in ReviewAnalysisDecision::all_artifact_keys() {
            let decision = ReviewAnalysisDecision::from_artifact_key(key)
                .unwrap_or_else(|| panic!("must parse key: {key}"));
            assert_eq!(decision.as_artifact_key(), *key);
        }
    }

    #[test]
    fn development_analysis_unknown_key_returns_none() {
        assert!(DevelopmentAnalysisDecision::from_artifact_key("needs_replanning").is_none());
        assert!(DevelopmentAnalysisDecision::from_artifact_key("ready_for_review").is_none());
        assert!(DevelopmentAnalysisDecision::from_artifact_key("").is_none());
    }

    #[test]
    fn review_analysis_unknown_key_returns_none() {
        assert!(ReviewAnalysisDecision::from_artifact_key("needs_more_work").is_none());
        assert!(ReviewAnalysisDecision::from_artifact_key("ready_to_commit").is_none());
        assert!(ReviewAnalysisDecision::from_artifact_key("").is_none());
    }
}
