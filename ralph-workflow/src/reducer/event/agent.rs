// NOTE: split from reducer/event.rs to keep the main file under line limits.
use super::types::{default_timeout_output_kind, AgentErrorKind, TimeoutOutputKind};
use crate::agents::session::{ParallelPlan, WorkerIdentity, WorkerReconciliationMetadata};
use crate::agents::{AgentDrain, AgentRole};
use crate::common::domain_types::{AgentName, ModelName};
use crate::ChildProcessInfo;
use serde::{Deserialize, Serialize};

/// Agent invocation and chain management events.
///
/// Events related to agent execution, fallback chains, model switching,
/// rate limiting, and retry cycles. The agent chain provides fault tolerance
/// through multiple fallback levels:
///
/// 1. Model level: Try different models for the same agent
/// 2. Agent level: Switch to a fallback agent
/// 3. Retry cycle: Start over with exponential backoff
///
/// # State Transitions
///
/// - `InvocationFailed(retriable=true)`: Advances to next model
/// - `InvocationFailed(retriable=false)`: Typically switches to next agent (policy may vary by kind)
/// - `RateLimited`: Typically immediate agent switch with prompt preservation
/// - `ChainExhausted`: Starts new retry cycle
/// - `InvocationSucceeded`: Clears continuation prompt
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
pub enum AgentEvent {
    /// Agent invocation started.
    InvocationStarted {
        /// Compatibility role metadata for the active drain.
        ///
        /// Runtime routing is drain-owned; reducers use explicit drain state as the
        /// authoritative consumer identity.
        role: AgentRole,
        /// The agent being invoked.
        agent: AgentName,
        /// The model being used, if specified.
        model: Option<ModelName>,
    },
    /// Agent invocation succeeded.
    InvocationSucceeded {
        /// Compatibility role metadata for the active drain.
        role: AgentRole,
        /// The agent that succeeded.
        agent: AgentName,
    },
    /// Agent invocation failed.
    InvocationFailed {
        /// Compatibility role metadata for the active drain.
        role: AgentRole,
        /// The agent that failed.
        agent: AgentName,
        /// The exit code from the agent process.
        exit_code: i32,
        /// The kind of error that occurred.
        error_kind: AgentErrorKind,
        /// Whether this error is retriable with the same agent.
        retriable: bool,
    },
    /// Fallback triggered to switch to a different agent.
    FallbackTriggered {
        /// The role being fulfilled.
        role: AgentRole,
        /// The agent being switched from.
        from_agent: AgentName,
        /// The agent being switched to.
        to_agent: AgentName,
    },
    /// Model fallback triggered within the same agent.
    ModelFallbackTriggered {
        /// The role being fulfilled.
        role: AgentRole,
        /// The agent whose model is changing.
        agent: AgentName,
        /// The model being switched from.
        from_model: ModelName,
        /// The model being switched to.
        to_model: ModelName,
    },
    /// Retry cycle started (all agents exhausted, starting over).
    RetryCycleStarted {
        /// The role being retried.
        role: AgentRole,
        /// The cycle number starting.
        cycle: u32,
    },
    /// Agent chain exhausted (no more agents/models to try).
    ChainExhausted {
        /// The role whose chain is exhausted.
        role: AgentRole,
    },
    /// Agent chain initialized with available agents.
    ChainInitialized {
        /// The explicit runtime drain this chain is for.
        drain: AgentDrain,
        /// The agents available in this chain.
        agents: Vec<AgentName>,
        /// Maximum number of retry cycles allowed for this chain.
        max_cycles: u32,
        /// Base retry-cycle delay in milliseconds.
        retry_delay_ms: u64,
        /// Exponential backoff multiplier.
        backoff_multiplier: f64,
        /// Maximum backoff delay in milliseconds.
        max_backoff_ms: u64,
    },
    /// Agent hit rate limit (429).
    ///
    /// Effects/executors emit this as a *fact* event. The reducer decides
    /// whether/when to switch agents.
    RateLimited {
        /// The role being fulfilled.
        role: AgentRole,
        /// The agent that hit the rate limit.
        agent: AgentName,
        /// The prompt that was being executed when rate limit was hit.
        /// This allows the next agent to continue the same work.
        prompt_context: Option<String>,
    },

    /// Agent hit authentication failure (401/403).
    ///
    /// Effects/executors emit this as a *fact* event. The reducer decides
    /// whether/when to switch agents.
    AuthFailed {
        /// The role being fulfilled.
        role: AgentRole,
        /// The agent that failed authentication.
        agent: AgentName,
    },

    /// Agent hit an idle timeout.
    ///
    /// Emitted as a fact; the reducer decides retry vs fallback based on `output_kind`.
    /// `NoOutput` triggers immediate agent switch; `PartialOutput` uses the same-agent
    /// retry budget (same semantics as before this feature).
    TimedOut {
        /// The role being fulfilled.
        role: AgentRole,
        /// The agent that timed out.
        agent: AgentName,
        /// Whether the agent produced any output before timing out.
        #[serde(default = "default_timeout_output_kind")]
        output_kind: TimeoutOutputKind,
        /// Path to the agent's logfile (for context extraction on `PartialOutput` retry).
        ///
        /// When `output_kind` is `PartialOutput` and the agent has no session ID,
        /// this path is used to extract context for the retry prompt.
        #[serde(default)]
        logfile_path: Option<String>,
        /// Child process status when the timeout was enforced.
        ///
        /// `None` if no children existed or child checking was disabled.
        /// When `Some`, contains the child count and cumulative CPU time at timeout.
        #[serde(default)]
        child_status_at_timeout: Option<ChildProcessInfo>,
    },

    /// Session established with agent.
    ///
    /// Emitted when an agent response includes a session ID that can be
    /// used for XSD retry continuation. This enables reusing the same
    /// session when retrying due to validation failures.
    SessionEstablished {
        /// The role this agent is fulfilling.
        role: AgentRole,
        /// The agent name.
        agent: AgentName,
        /// The session ID returned by the agent.
        session_id: String,
    },

    /// XSD validation failed for agent output.
    ///
    /// Emitted when agent output cannot be parsed or fails XSD validation.
    /// Distinct from `OutputValidationFailed` events in phase-specific enums,
    /// this is the canonical XSD retry trigger that the reducer uses to
    /// decide whether to retry with the same agent/session or advance the chain.
    XsdValidationFailed {
        /// The role whose output failed validation.
        role: AgentRole,
        /// The artifact type that failed validation.
        artifact: crate::reducer::state::ArtifactType,
        /// Error message from validation.
        error: String,
        /// Current XSD retry count for this artifact.
        retry_count: u32,
    },

    /// Template rendering failed due to missing required variables or unresolved placeholders.
    ///
    /// Emitted when a prompt template cannot be rendered because required variables
    /// are missing or unresolved placeholders (e.g., `{{VAR}}`) remain in the output.
    /// The reducer decides fallback policy, typically switching to the next agent.
    TemplateVariablesInvalid {
        /// The role whose template failed to render.
        role: AgentRole,
        /// The name of the template that failed.
        template_name: String,
        /// Variables that were required but not provided.
        missing_variables: Vec<String>,
        /// Placeholder patterns that remain unresolved in the rendered output.
        unresolved_placeholders: Vec<String>,
    },

    /// Timeout context written to temp file for session-less agent retry.
    ///
    /// Emitted when a timeout with meaningful output occurs but the agent doesn't
    /// support session IDs. The prior context is extracted from the logfile and
    /// written to a temp file for the retry prompt to reference.
    TimeoutContextWritten {
        /// The role this agent is fulfilling.
        role: AgentRole,
        /// Source logfile path the context was extracted from.
        logfile_path: String,
        /// Target temp file path where context was written.
        context_path: String,
    },

    /// A capability check was denied by the policy gate.
    ///
    /// Emitted when an effect requires a capability that the session doesn't have.
    /// This indicates a misconfiguration — the session's capabilities don't match
    /// the effects the orchestrator is trying to execute.
    ///
    /// The reducer handles this by triggering recovery escalation.
    CapabilityDenied {
        /// The role whose capability was denied.
        role: AgentRole,
        /// The capability identifier that was missing (e.g., "workspace.write_tracked").
        capability: String,
        /// Human-readable reason for the denial.
        reason: String,
    },

    // ========================================================================
    // Phase 4: Parallel Worker Events
    // ========================================================================
    /// Planning agent produced a parallel plan.
    ///
    /// Emitted when the planning agent's output indicates the plan should be
    /// executed in parallel with multiple workers. The plan contains work units
    /// with non-overlapping edit areas.
    ParallelPlanProduced {
        /// The parallel plan produced by the planning agent.
        plan: ParallelPlan,
    },

    /// Parallel workers were successfully dispatched.
    ///
    /// Emitted after worktrees are created and agent processes are spawned
    /// for each work unit in the parallel plan.
    ParallelWorkersDispatched {
        /// Number of workers dispatched.
        worker_count: usize,
        /// Identity of each dispatched worker.
        workers: Vec<WorkerIdentity>,
    },

    /// A parallel worker completed its work unit.
    ///
    /// Emitted when a worker finishes execution (success, failure, or timeout).
    /// The reducer uses this to track progress toward awaiting verification.
    ParallelWorkerCompleted {
        /// The worker's identity.
        worker_id: String,
        /// Reconciliation metadata for this worker.
        metadata: WorkerReconciliationMetadata,
    },

    /// A parallel plan was validated and accepted.
    ///
    /// Emitted after `EvaluateParallelPlan` confirms the plan is valid
    /// (non-overlapping edit areas, valid dependencies).
    ParallelPlanValidated {
        /// The validated parallel plan.
        plan: ParallelPlan,
    },

    /// A parallel plan was rejected as invalid.
    ///
    /// Emitted when `EvaluateParallelPlan` finds issues:
    /// overlapping edit areas, circular dependencies, or invalid paths.
    /// The pipeline falls back to single-agent execution.
    ParallelPlanRejected {
        /// The rejected parallel plan.
        plan: ParallelPlan,
        /// Reason for rejection.
        reason: String,
    },
}
