//! Mock process executor for testing.
//!
//! This module provides mock implementations of `ProcessExecutor` and `AgentChild`
//! used by unit tests and integration tests (via the `test-utils` feature).

mod agent_child;
mod agent_output;
mod process_executor;

/// Type alias for captured execute calls.
///
/// Each call is a tuple of (command, args, env, workdir).
type ExecuteCall = (String, Vec<String>, Vec<(String, String)>, Option<String>);

pub use agent_child::MockAgentChild;
pub use process_executor::MockProcessExecutor;

#[cfg(test)]
mod tests;
