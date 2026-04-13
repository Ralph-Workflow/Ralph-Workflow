//! Orchestration policy types, TOML schemas, and validation rules for ralph-workflow.
//!
//! This crate has no dependency on `ralph-workflow` — it defines the policy surface
//! that `ralph-workflow` consumes. The dependency direction is:
//!
//! `ralph-workflow` → `ralph-workflow-policy` (never the reverse)

pub mod config;
pub mod validation;

pub use config::{
    ArtifactAcceptanceConfig, ArtifactIdentity, ArtifactsConfig, AnalysisDrainArtifactConfig,
    BudgetConfig, DecisionRoutesConfig, DrainArtifactConfig, DrainInvariant, DRAIN_INVARIANTS,
    EmbeddedDecisionPointsConfig, ParallelExecutionConfig, PipelineConfig, PipelineValidationConfig,
    TopLevelPhasesConfig, WorkerContractConfig, drain_invariant,
};
pub use validation::{
    VALID_PIPELINE_KEYS, VALID_ARTIFACTS_KEYS, VALID_DRAIN_ARTIFACT_CONFIG_KEYS,
    CANONICAL_ANALYSIS_DECISION_OUTCOMES,
};
