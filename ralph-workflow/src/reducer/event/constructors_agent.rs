// NOTE: Agent constructors split from constructors.rs

use crate::common::domain_types::{AgentName, ModelName};

impl PipelineEvent {
    // Agent constructors
    /// Create an `AgentInvocationStarted` event.
    #[must_use]
    pub const fn agent_invocation_started(
        role: AgentRole,
        agent: AgentName,
        model: Option<ModelName>,
    ) -> Self {
        Self::Agent(AgentEvent::InvocationStarted { role, agent, model })
    }

    /// Create an `AgentInvocationSucceeded` event.
    #[must_use]
    pub const fn agent_invocation_succeeded(role: AgentRole, agent: AgentName) -> Self {
        Self::Agent(AgentEvent::InvocationSucceeded { role, agent })
    }

    /// Create an `AgentInvocationFailed` event.
    #[must_use]
    pub const fn agent_invocation_failed(
        role: AgentRole,
        agent: AgentName,
        exit_code: i32,
        error_kind: AgentErrorKind,
        retriable: bool,
    ) -> Self {
        Self::Agent(AgentEvent::InvocationFailed {
            role,
            agent,
            exit_code,
            error_kind,
            retriable,
        })
    }

    /// Create an `AgentFallbackTriggered` event.
    #[must_use]
    pub const fn agent_fallback_triggered(
        role: AgentRole,
        from_agent: AgentName,
        to_agent: AgentName,
    ) -> Self {
        Self::Agent(AgentEvent::FallbackTriggered {
            role,
            from_agent,
            to_agent,
        })
    }

    /// Create an `AgentModelFallbackTriggered` event.
    #[must_use]
    pub const fn agent_model_fallback_triggered(
        role: AgentRole,
        agent: AgentName,
        from_model: ModelName,
        to_model: ModelName,
    ) -> Self {
        Self::Agent(AgentEvent::ModelFallbackTriggered {
            role,
            agent,
            from_model,
            to_model,
        })
    }

    /// Create an `AgentRetryCycleStarted` event.
    #[must_use]
    pub const fn agent_retry_cycle_started(role: AgentRole, cycle: u32) -> Self {
        Self::Agent(AgentEvent::RetryCycleStarted { role, cycle })
    }

    /// Create an `AgentChainExhausted` event.
    #[must_use]
    pub const fn agent_chain_exhausted(role: AgentRole) -> Self {
        Self::Agent(AgentEvent::ChainExhausted { role })
    }

    /// Create an `AgentChainInitialized` event.
    #[must_use]
    pub const fn agent_chain_initialized(
        drain: AgentDrain,
        agents: Vec<AgentName>,
        max_cycles: u32,
        retry_delay_ms: u64,
        backoff_multiplier: f64,
        max_backoff_ms: u64,
    ) -> Self {
        Self::Agent(AgentEvent::ChainInitialized {
            drain,
            agents,
            max_cycles,
            retry_delay_ms,
            backoff_multiplier,
            max_backoff_ms,
        })
    }

    /// Create an `AgentRateLimited` event.
    #[must_use]
    pub const fn agent_rate_limited(
        role: AgentRole,
        agent: AgentName,
        prompt_context: Option<String>,
    ) -> Self {
        Self::Agent(AgentEvent::RateLimited {
            role,
            agent,
            prompt_context,
        })
    }

    /// Create an `AgentAuthFailed` event.
    #[must_use]
    pub const fn agent_auth_failed(role: AgentRole, agent: AgentName) -> Self {
        Self::Agent(AgentEvent::AuthFailed { role, agent })
    }

    /// Create an `AgentTimedOut` event.
    #[must_use]
    pub const fn agent_timed_out(
        role: AgentRole,
        agent: AgentName,
        output_kind: TimeoutOutputKind,
        logfile_path: Option<String>,
        child_status_at_timeout: Option<ChildProcessInfo>,
    ) -> Self {
        Self::Agent(AgentEvent::TimedOut {
            role,
            agent,
            output_kind,
            logfile_path,
            child_status_at_timeout,
        })
    }

    /// Create an `AgentSessionEstablished` event.
    #[must_use]
    pub const fn agent_session_established(
        role: AgentRole,
        agent: AgentName,
        session_id: String,
    ) -> Self {
        Self::Agent(AgentEvent::SessionEstablished {
            role,
            agent,
            session_id,
        })
    }

    /// Create an `AgentXsdValidationFailed` event.
    #[must_use]
    pub const fn agent_xsd_validation_failed(
        role: AgentRole,
        artifact: crate::reducer::state::ArtifactType,
        error: String,
        retry_count: u32,
    ) -> Self {
        Self::Agent(AgentEvent::XsdValidationFailed {
            role,
            artifact,
            error,
            retry_count,
        })
    }

    /// Create an `AgentTemplateVariablesInvalid` event.
    #[must_use]
    pub const fn agent_template_variables_invalid(
        role: AgentRole,
        template_name: String,
        missing_variables: Vec<String>,
        unresolved_placeholders: Vec<String>,
    ) -> Self {
        Self::Agent(AgentEvent::TemplateVariablesInvalid {
            role,
            template_name,
            missing_variables,
            unresolved_placeholders,
        })
    }

    /// Create an `AgentTimeoutContextWritten` event.
    #[must_use]
    pub const fn agent_timeout_context_written(
        role: AgentRole,
        logfile_path: String,
        context_path: String,
    ) -> Self {
        Self::Agent(AgentEvent::TimeoutContextWritten {
            role,
            logfile_path,
            context_path,
        })
    }
}
