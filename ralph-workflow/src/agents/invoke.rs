//! Abstract agent invocation contract.
//!
//! This module defines the [`AgentInvoker`] trait and supporting types that provide
//! a domain-shaped abstraction for AI coding agent invocation. Boundary adapters
//! (claude, codex, opencode, etc.) implement this trait, allowing domain code
//! to depend on the abstraction rather than concrete provider implementations.
//!
//! # Design Principles
//!
//! - **Domain-shaped I/O**: Input/output types contain plain values that are
//!   meaningful in the domain context, not raw process types.
//! - **Object-safe**: The trait is designed for dynamic dispatch via `dyn AgentInvoker`.
//! - **Capability injection**: Callers provide the [`AgentConfig`] at invocation time,
//!   not construction time, enabling flexible agent selection.

use crate::agents::config::AgentConfig;
use std::path::Path;

/// Input for agent invocation.
///
/// This struct represents all the information needed to invoke an AI coding agent.
/// It contains plain domain values rather than process-level types.
#[derive(Debug, Clone)]
pub struct AgentInput<'a> {
    /// The prompt/content to send to the agent.
    pub prompt: &'a str,
    /// Configuration for the agent to invoke.
    pub agent_config: &'a AgentConfig,
    /// Optional log file path for capturing agent output.
    pub logfile: Option<&'a Path>,
}

/// Output from successful agent invocation.
///
/// This struct represents the result of a completed agent invocation,
/// containing the agent's output streams and exit status.
#[derive(Debug, Clone)]
pub struct AgentOutput {
    /// Standard output from the agent.
    pub stdout: String,
    /// Standard error output from the agent.
    pub stderr: String,
    /// Exit code returned by the agent process.
    pub exit_code: i32,
}

/// Errors that can occur during agent invocation.
///
/// These errors represent failure modes that are meaningful in the domain context,
/// such as execution failures, classification of agent errors, and invocation issues.
#[derive(Debug, Clone)]
pub enum AgentInvokeError {
    /// The agent command could not be executed (not found, permission denied, etc.).
    ExecutionFailed(String),
    /// The agent process was killed (OOM, signal, etc.).
    ProcessKilled(String),
    /// Invalid input provided to the invocation.
    InvalidInput(String),
    /// Classification of the agent's error response.
    AgentError(crate::agents::error::AgentErrorKind),
    /// The agent produced no parseable output.
    NoOutput,
    /// The agent output was truncated.
    TruncatedOutput,
}

impl std::fmt::Display for AgentInvokeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ExecutionFailed(msg) => write!(f, "Agent execution failed: {msg}"),
            Self::ProcessKilled(msg) => write!(f, "Agent process killed: {msg}"),
            Self::InvalidInput(msg) => write!(f, "Invalid invocation input: {msg}"),
            Self::AgentError(kind) => write!(f, "Agent error: {}", kind.description()),
            Self::NoOutput => write!(f, "Agent produced no output"),
            Self::TruncatedOutput => write!(f, "Agent output was truncated"),
        }
    }
}

impl std::error::Error for AgentInvokeError {}

/// Trait for invoking AI coding agents.
///
/// This trait abstracts the execution of an AI coding agent, enabling dependency
/// injection and mock testing. Boundary adapters for different agents (claude,
/// codex, opencode, gemini, etc.) implement this trait.
///
/// # Object Safety
///
/// This trait is designed to be object-safe via `dyn AgentInvoker`. Implementors
/// must not require any type parameters or use generic methods.
///
/// # Example
///
/// ```ignore
/// struct AgentRunner {
///     invoker: Arc<dyn AgentInvoker>,
/// }
///
/// impl AgentRunner {
///     fn run_agent(&self, prompt: &str, config: &AgentConfig) -> Result<AgentOutput, AgentInvokeError> {
///         let input = AgentInput {
///             prompt,
///             agent_config: config,
///             logfile: None,
///         };
///         self.invoker.invoke(input)
///     }
/// }
/// ```
pub trait AgentInvoker: Send + Sync {
    /// Invoke the agent with the given input.
    ///
    /// # Arguments
    ///
    /// * `input` - The invocation input containing prompt and agent configuration
    ///
    /// # Returns
    ///
    /// Returns [`AgentOutput`] on success, or [`AgentInvokeError`] on failure.
    fn invoke(&self, input: AgentInput<'_>) -> Result<AgentOutput, AgentInvokeError>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_invoker_is_object_safe() {
        fn assert_object_safe(_: &dyn AgentInvoker) {}
        assert_object_safe(&MockAgentInvoker);
    }

    #[test]
    fn test_agent_input_clone() {
        let config = AgentConfig::default();
        let input = AgentInput {
            prompt: "test prompt",
            agent_config: &config,
            logfile: None,
        };
        let cloned = input.clone();
        assert_eq!(cloned.prompt, "test prompt");
    }

    #[test]
    fn test_agent_output_clone() {
        let output = AgentOutput {
            stdout: "test output".to_string(),
            stderr: "test error".to_string(),
            exit_code: 0,
        };
        let cloned = output.clone();
        assert_eq!(cloned.stdout, "test output");
        assert_eq!(cloned.stderr, "test error");
        assert_eq!(cloned.exit_code, 0);
    }

    #[test]
    fn test_agent_invoke_error_display() {
        let err = AgentInvokeError::ExecutionFailed("command not found".to_string());
        assert!(err.to_string().contains("Agent execution failed"));
        assert!(err.to_string().contains("command not found"));

        let err = AgentInvokeError::NoOutput;
        assert!(err.to_string().contains("no output"));
    }

    #[derive(Debug)]
    struct MockAgentInvoker;

    impl AgentInvoker for MockAgentInvoker {
        fn invoke(&self, input: AgentInput<'_>) -> Result<AgentOutput, AgentInvokeError> {
            Ok(AgentOutput {
                stdout: format!("mock response to: {}", input.prompt),
                stderr: String::new(),
                exit_code: 0,
            })
        }
    }

    #[test]
    fn test_mock_agent_invoker() {
        let invoker = MockAgentInvoker;
        let config = AgentConfig::default();
        let input = AgentInput {
            prompt: "hello",
            agent_config: &config,
            logfile: None,
        };
        let result = invoker.invoke(input).expect("should succeed");
        assert!(result.stdout.contains("hello"));
        assert_eq!(result.exit_code, 0);
    }
}
