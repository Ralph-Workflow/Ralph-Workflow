//! Orchestration policy types, TOML schemas, and validation rules for ralph-workflow.
//!
//! This crate has no dependency on `ralph-workflow` — it defines the policy surface
//! that `ralph-workflow` consumes. The dependency direction is:
//!
//! `ralph-workflow` → `ralph-workflow-policy` (never the reverse)

pub mod config;
pub mod templates;
pub mod validation;

pub use config::{
    drain_invariant,
    // Normative defaults loaders
    load_artifacts,
    load_pipeline,
    load_shipped_phases,
    parse_phase_definition,
    AnalysisDrainArtifactConfig,
    // Drain config
    ArtifactAcceptanceConfig,
    ArtifactIdentity,
    ArtifactsConfig,
    BudgetConfig,
    CycleAccountingConfig,
    DecisionRoutesConfig,
    // Typed analysis decision enums
    DevelopmentAnalysisDecision,
    DrainArtifactConfig,
    DrainConfigTable,
    DrainConfigToml,
    DrainInvariant,
    EmbeddedDecisionPointsConfig,
    OrchestrationConfig,
    ParallelExecutionConfig,
    // Phase definition types
    PhaseDefinition,
    PhaseOrigin,
    PhaseSideEffectConfig,
    PipelineConfig,
    PipelineLoadError,
    PipelineValidationConfig,
    PostCommitCondition,
    PostCommitRoute,
    ResolveDrainError,
    ReviewAnalysisDecision,
    TopLevelPhasesConfig,
    TransitionCondition,
    TransitionRule,
    WorkerContractConfig,
    DRAIN_INVARIANTS,
};

pub use templates::{
    get_policy_template, ANALYSIS_SYSTEM_PROMPT_TEMPLATE, CANONICAL_TEMPLATE_NAMES,
    COMMIT_MESSAGE_TEMPLATE, COMMIT_SIMPLIFIED_TEMPLATE, CONFLICT_RESOLUTION_FALLBACK_TEMPLATE,
    CONFLICT_RESOLUTION_TEMPLATE, DEVELOPER_ITERATION_CONTINUATION_TEMPLATE,
    DEVELOPER_ITERATION_TEMPLATE, FIX_ANALYSIS_SYSTEM_PROMPT_TEMPLATE, FIX_MODE_TEMPLATE,
    PARALLEL_DEV_WORKER_TEMPLATE, PARALLEL_PLANNING_TEMPLATE, PARALLEL_VERIFIER_TEMPLATE,
    PARTIAL_CONTEXT_SECTION, PARTIAL_CRITICAL_HEADER, PARTIAL_DEVELOPER_ITERATION_GUIDANCE,
    PARTIAL_DIFF_SECTION, PARTIAL_MCP_TOOLS, PARTIAL_NO_GIT_COMMIT, PARTIAL_OUTPUT_CHECKLIST,
    PARTIAL_SAFETY_NO_EXECUTE, PARTIAL_SESSION_CAPABILITIES, PARTIAL_UNATTENDED_MODE,
    PLANNING_TEMPLATE, REVIEW_TEMPLATE,
};

pub use validation::{
    // Validation diagnostic types
    DiagnosticKind,
    ValidationDiagnostic,
    // Valid key constants
    CANONICAL_ANALYSIS_DECISION_OUTCOMES,
    CANONICAL_DEVELOPMENT_ANALYSIS_DECISIONS,
    CANONICAL_REVIEW_ANALYSIS_DECISIONS,
    VALID_AGENT_DRAIN_KEYS,
    VALID_ARTIFACTS_KEYS,
    VALID_DRAIN_ARTIFACT_CONFIG_KEYS,
    VALID_DRAIN_CONFIG_KEYS,
    VALID_ORCHESTRATION_KEYS,
    VALID_PIPELINE_KEYS,
};
